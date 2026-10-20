"""评测引擎：Q&A 生成 + Recall@k / MRR + 实验编排。

- Q&A 生成：按 chunk 均匀抽样（跳过 table/image），每文档默认 20 条，
  LLM 逐 chunk 生成问答对并回填 source_chunk_id
- 实验：按 chunking_config 切好的 chunks 写入独立向量表
  exp_{experiment_id前8位}（命名空间隔离，不覆盖主库），
  逐条 QA 检索，命中判定：source_chunk_id 或其 parent 在 top-k；
  重切后 chunk id 全变，命中判定回退到内容重叠（QA 存源内容快照）
- 指标：Recall@1/5/10 + MRR
- core 层不依赖数据库；QAItem/Experiment 落库由 api/workers 层完成
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from app.core.retriever import Retriever
from app.core.types import Chunk, ChunkType, ExperimentResult, QAItemData
from app.core.vector_store import VectorItem, VectorStore

logger = logging.getLogger(__name__)

# 生成 Q&A 时跳过的 chunk 类型
SKIP_SAMPLE_TYPES = {ChunkType.TABLE, ChunkType.IMAGE}

# 内容重叠命中阈值：检索结果 chunk 覆盖 QA 源内容 >= 该比例即算命中。
# 实验按新配置重切后 chunk id 全变，命中判定需回退到内容匹配；
# 覆盖 40% 以上认为答案内容基本在该 chunk 中。
OVERLAP_THRESHOLD = 0.4

_QA_PROMPT = """基于以下文档片段，生成一个用于检索评测的问答对。
要求：问题应能通过该片段内容回答；答案简洁（一两句话）。

【片段】
{content}

只返回 JSON：{{"question": "...", "answer": "..."}}"""


# ---------------------------------------------------------------------------
# 纯函数：抽样与指标
# ---------------------------------------------------------------------------


def sample_chunks(chunks: list[Chunk], count: int = 20) -> list[Chunk]:
    """按 chunk 均匀抽样（跳过 table/image，均匀覆盖章节）。"""
    eligible = [c for c in chunks if c.chunk_type not in SKIP_SAMPLE_TYPES]
    if not eligible or count <= 0:
        return []
    if len(eligible) <= count:
        return eligible
    step = len(eligible) / count
    return [eligible[int(i * step)] for i in range(count)]


def content_overlap(source: str, content: str) -> float:
    """source 被 content 覆盖的比例（0.0~1.0）。

    内容切分边界变化后 chunk id 全部失效，用内容重叠兜底判定。
    实现：source 按固定窗口切片，统计出现在 content 中的窗口占比
    （`in` 子串查找，O(n)，窗口 50 字符足够抗切分抖动）。
    """
    if not source:
        return 0.0
    if source in content:
        return 1.0
    if content in source:
        return min(1.0, len(content) / len(source))
    window = 50
    covered = 0
    total = 0
    for i in range(0, len(source), window):
        total += 1
        if source[i : i + window] in content:
            covered += 1
    return covered / total if total else 0.0


def hit_rank(
    ranked_ids: list[str],
    source_chunk_id: str | None,
    parent_map: dict[str, str],
    content_map: dict[str, str] | None = None,
    source_content: str | None = None,
    expect_document: str | None = None,
    chunk_doc_names: dict[str, str] | None = None,
) -> int | None:
    """命中判定：任一路径命中即算，返回名次（1 起）。

    三条路径（按判定成本从低到高）：
    1. ID：source_chunk_id 或其 parent 在 ranked_ids 中的名次。
       实验重切后 chunk id 全变（新 UUID + 实验前缀），仅 ID 匹配必然全部
       未命中，因此有兜底路径。
    2. 内容重叠：任一结果 chunk 的内容覆盖 QA 源内容快照达到
       OVERLAP_THRESHOLD 也算命中（LLM 生成的 QA 带快照走此路）。
    3. 文档级：结果 chunk 所属文档 == expect_document。评测集按文档给期望
       时走此路，与评测回归门 hit@5 口径一致，且不随重切过期。
    无任何期望信息（无 source_chunk_id 且无 expect_document）返回 None。
    """
    has_id = bool(source_chunk_id)
    has_doc = bool(expect_document) and bool(chunk_doc_names)
    if not has_id and not has_doc:
        return None
    parent_id = parent_map.get(source_chunk_id) if has_id else None
    for index, chunk_id in enumerate(ranked_ids, start=1):
        if has_id and (
            chunk_id == source_chunk_id
            or (parent_id is not None and chunk_id == parent_id)
        ):
            return index
        if content_map is not None and source_content:
            if content_overlap(source_content, content_map.get(chunk_id, "")) >= (
                OVERLAP_THRESHOLD
            ):
                return index
        if has_doc and chunk_doc_names.get(chunk_id) == expect_document:
            return index
    return None


def recall_at_k(
    ranks: list[int | None], ks: tuple[int, ...] = (1, 5, 10)
) -> dict[int, float]:
    """Recall@k：排名 <= k 视为命中。"""
    total = len(ranks)
    if total == 0:
        return {k: 0.0 for k in ks}
    return {
        k: sum(1 for r in ranks if r is not None and r <= k) / total for k in ks
    }


def mean_reciprocal_rank(ranks: list[int | None]) -> float:
    """MRR：命中排名倒数的均值。"""
    if not ranks:
        return 0.0
    return sum(1.0 / r for r in ranks if r is not None) / len(ranks)


# ---------------------------------------------------------------------------
# 评测编排
# ---------------------------------------------------------------------------


class Evaluator:
    """评测编排器（依赖注入，core 无数据库依赖）。"""

    def __init__(
        self,
        embedder: Any,
        store: VectorStore,
        llm_client: Any | None = None,
        reranker: Any | None = None,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._llm_client = llm_client
        self._reranker = reranker

    # ------------------------------------------------------------------
    # Q&A 生成
    # ------------------------------------------------------------------

    def generate_qa(
        self, chunks: list[Chunk], count: int = 20
    ) -> list[QAItemData]:
        """LLM 按抽样 chunk 生成问答对，回填 source_chunk_id。

        LLM 未配置或单条失败的 chunk 直接跳过（不阻断整体生成）。
        """
        if self._llm_client is None:
            raise ValueError("未配置 LLM，无法生成 Q&A 测试集")
        sampled = sample_chunks(chunks, count)
        items: list[QAItemData] = []
        for chunk in sampled:
            qa = self._generate_one(chunk)
            if qa is not None:
                items.append(qa)
        return items

    def _generate_one(self, chunk: Chunk) -> QAItemData | None:
        prompt = _QA_PROMPT.format(content=chunk.content[:2000])
        try:
            raw = self._llm_client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=512,
            )
        except Exception:  # noqa: BLE001
            logger.warning("Q&A 生成失败，跳过 chunk %s", chunk.id, exc_info=True)
            return None
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
        question = str(data.get("question", "")).strip()
        if not question:
            return None
        return QAItemData(
            question=question,
            answer=str(data.get("answer", "")).strip(),
            source_chunk_id=chunk.id,
            source_content=chunk.content,
            meta={
                "breadcrumb": chunk.breadcrumb,
                "page_number": chunk.page_number,
            },
        )

    # ------------------------------------------------------------------
    # 实验运行
    # ------------------------------------------------------------------

    def run_experiment(
        self,
        chunks: list[Chunk],
        qa_items: list[QAItemData],
        table: str,
        top_k: int = 10,
        progress_cb: Callable[[int, int], None] | None = None,
        doc_names: dict[str, str] | None = None,
    ) -> ExperimentResult:
        """运行一次实验。

        Args:
            chunks: 本次切分配置产出的全部 chunk（父+子，用于建索引与
                父块映射/展开）。
            qa_items: 测试集条目。
            table: 独立向量表名（exp_{experiment_id前8位}）。
            top_k: 检索深度（Recall@10 需要 top_k >= 10）。
            progress_cb: 进度回调 (current, total)。
            doc_names: document_id → 文件名映射，供文档级命中判定
                （meta.expect_document）使用。

        指标口径：仅"有期望答案"的条目（带 source_chunk_id 或
        meta.expect_document）计入 Recall@k / MRR 分母；负例（无期望，
        期望检索为空）不进分母，否则完美检索也只有 正例数/总条数。
        """
        children = [c for c in chunks if c.is_embedded]
        parent_map = {c.id: c.parent_id for c in children if c.parent_id}
        content_map = {c.id: c.content for c in chunks}
        child_map = {c.id: c for c in children}
        chunk_doc_names = {
            c.id: (doc_names or {}).get(c.document_id, "") for c in chunks
        }

        # 1. 向量化并写入独立实验表（命名空间隔离，不覆盖主库）
        if children:
            embedding = self._embedder.embed([c.content for c in children])
            items = []
            for i, chunk in enumerate(children):
                items.append(
                    VectorItem(
                        id=chunk.id,
                        vector=embedding.dense[i],
                        sparse=embedding.sparse[i] if embedding.sparse else None,
                        content=chunk.content,
                        document_id=chunk.document_id,
                        chunk_type=chunk.chunk_type.value,
                        metadata={
                            "breadcrumb": chunk.breadcrumb,
                            "page_number": chunk.page_number,
                        },
                    )
                )
            self._store.upsert(items, table=table)

        # 2. 逐条 QA 检索
        def resolver(chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
            resolved: dict[str, dict[str, Any]] = {}
            for chunk_id in chunk_ids:
                parent_id = parent_map.get(chunk_id)
                child = child_map.get(chunk_id)
                resolved[chunk_id] = {
                    "parent_content": content_map.get(
                        parent_id or chunk_id, ""
                    ),
                    "matched_child_text": content_map.get(chunk_id, ""),
                    "page_number": child.page_number if child else 0,
                    "breadcrumb": child.breadcrumb if child else [],
                    "chunk_type": child.chunk_type.value if child else "text",
                }
            return resolved

        retriever = Retriever(
            embedder=self._embedder,
            store=self._store,
            reranker=self._reranker,
            parent_resolver=resolver,
        )

        ranks: list[int | None] = []
        scored_ranks: list[int | None] = []
        total = len(qa_items)
        for index, qa in enumerate(qa_items, start=1):
            try:
                response = retriever.search(qa.question, top_k=top_k, table=table)
                ranked_ids = [r.chunk_id for r in response.results]
            except Exception:  # noqa: BLE001
                logger.warning("实验检索失败：%s", qa.question, exc_info=True)
                ranked_ids = []
            expect_document = (qa.meta or {}).get("expect_document")
            rank = hit_rank(
                ranked_ids,
                qa.source_chunk_id,
                parent_map,
                content_map=content_map,
                source_content=qa.source_content,
                expect_document=str(expect_document) if expect_document else None,
                chunk_doc_names=chunk_doc_names,
            )
            ranks.append(rank)
            # 负例（无 source_chunk_id 且无 expect_document）不进指标分母
            if qa.source_chunk_id or expect_document:
                scored_ranks.append(rank)
            if progress_cb:
                progress_cb(index, total)

        # 3. 指标（仅正例）
        recalls = recall_at_k(scored_ranks)
        return ExperimentResult(
            recall_at_1=recalls[1],
            recall_at_5=recalls[5],
            recall_at_10=recalls[10],
            mrr=mean_reciprocal_rank(scored_ranks),
            total=total,
            ranks=ranks,
        )
