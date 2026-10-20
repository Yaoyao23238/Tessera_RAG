"""混合检索：稠密+稀疏并行 → 应用层 RRF(k=60) → Rerank（可选，top 20）
→ 父块展开。任何向量库后端不例外（共享知识 #9）。

RRF 公式：score(d) = Σ 1/(k + rank_i(d))，k=60，两路都命中分数叠加。

父块展开通过注入的 parent_resolver 回调完成（chunk_id → 父块信息），
api/workers 层用 SQLite 实现；core 层保持无数据库依赖。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from app.core.types import (
    EmbeddingResult,
    ScoredChunk,
    SearchResponse,
    SearchResultItem,
)
from app.core.vector_store import MAIN_TABLE, Filters, StoreHit, VectorStore

logger = logging.getLogger(__name__)

RRF_K = 60  # RRF 常数
_CANDIDATE_MULTIPLIER = 2  # 稠密/稀疏各取 top_k * 2 候选


class _EmbedderProto:
    """duck-typing 文档：embedder 需实现 embed(list[str]) -> EmbeddingResult。"""

    def embed(self, texts: list[str]) -> EmbeddingResult:  # pragma: no cover
        raise NotImplementedError


# parent_resolver(chunk_ids) -> {chunk_id: {parent_id, parent_content,
#   matched_child_text, source_file, page_number, breadcrumb, chunk_type}}
ParentResolver = Callable[[list[str]], dict[str, dict[str, Any]]]


class Retriever:
    """混合检索编排器。"""

    def __init__(
        self,
        embedder: Any,
        store: VectorStore,
        reranker: Any | None = None,
        parent_resolver: ParentResolver | None = None,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._reranker = reranker
        self._parent_resolver = parent_resolver

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
        use_rerank: bool | None = None,
    ) -> SearchResponse:
        """执行完整检索链路。"""
        started = time.perf_counter()

        # 1. Query 向量化（BGE-M3 时带稀疏权重）
        result: EmbeddingResult = self._embedder.embed([query])
        query_vec = result.dense[0] if result.dense else []
        query_sparse = result.sparse[0] if result.sparse else None

        # 2. 稠密 + 稀疏两路检索（候选各取 top_k*2）
        candidate_k = top_k * _CANDIDATE_MULTIPLIER
        dense_hits = self._store.dense_search(query_vec, candidate_k, filters, table)
        # 稀疏查询形态按后端能力分派：weights 档（Qdrant）传 BGE-M3
        # lexical_weights 字典；FTS 档（sqlite-vec/LanceDB/pgvector）传原始
        # 查询文本（此前无脑传 dict，FTS 后端收到非 str 直接返空，
        # 导致 BGE-M3 下稀疏通道完全失效、RRF 退化为纯稠密）
        if query_sparse is not None and self._store.sparse_query_kind == "weights":
            sparse_query: "str | dict[int, float]" = query_sparse
        else:
            sparse_query = query
        sparse_hits = self._store.sparse_search(
            sparse_query, candidate_k, filters, table
        )

        # 3. 应用层 RRF 融合
        fused = self.rrf_fuse(dense_hits, sparse_hits, k=RRF_K)

        # 4. Rerank（可选，仅 top_n，失败回退无 rerank）
        threshold_dropped = 0
        want_rerank = (
            use_rerank
            if use_rerank is not None
            else bool(self._reranker and self._reranker.enabled)
        )
        if want_rerank and self._reranker is not None and fused:
            top_n = getattr(self._reranker, "top_n", 20)
            # 无该属性的 duck-typed 对象按"不过滤"处理（显式 0.0 同义）
            min_score = float(getattr(self._reranker, "min_score", 0.0) or 0.0)
            head, tail = fused[:top_n], fused[top_n:]
            for hit, hit_content in zip(head, self._fetch_contents(head, table)):
                hit.content = hit_content
            try:
                head = self._reranker.rerank(query, head)
            except Exception:  # noqa: BLE001 - 回退无 rerank
                logger.warning("Rerank 失败，回退 RRF 排序", exc_info=True)
            else:
                if min_score > 0:
                    # 阈值只对已精排的候选有意义，故未精排的 tail 一并丢弃：
                    # 否则 head 被过滤干净后，tail 里更低分的噪声反而会冒到前面。
                    kept = [
                        hit
                        for hit in head
                        if (hit.rerank_score or 0.0) >= min_score
                    ]
                    threshold_dropped = (len(head) - len(kept)) + len(tail)
                    logger.info(
                        "Rerank 阈值 %.4f：保留 %d/%d 条，丢弃未精排 tail %d 条",
                        min_score,
                        len(kept),
                        len(head),
                        len(tail),
                    )
                    head, tail = kept, []
            fused = [*head, *tail]

        # 5. 截断 + 父块展开
        fused = fused[:top_k]
        results = self._expand_parents(fused, table)

        latency_ms = int((time.perf_counter() - started) * 1000)
        return SearchResponse(
            query=query,
            results=results,
            total=len(results),
            latency_ms=latency_ms,
            threshold_dropped=threshold_dropped,
        )

    # ------------------------------------------------------------------
    # RRF 融合
    # ------------------------------------------------------------------

    @staticmethod
    def rrf_fuse(
        dense_hits: list[StoreHit],
        sparse_hits: list[StoreHit],
        k: int = RRF_K,
    ) -> list[ScoredChunk]:
        """Reciprocal Rank Fusion：score(d) = Σ 1/(k + rank_i(d))。"""
        fused: dict[str, ScoredChunk] = {}
        for rank, hit in enumerate(dense_hits, start=1):
            entry = fused.setdefault(hit.id, ScoredChunk(chunk_id=hit.id))
            entry.score += 1.0 / (k + rank)
            entry.dense_rank = rank
            entry.dense_score = float(hit.score)
            if not entry.content:
                entry.content = hit.content
            if not entry.metadata:
                entry.metadata = {
                    "document_id": hit.document_id,
                    "chunk_type": hit.chunk_type,
                    **hit.metadata,
                }
        for rank, hit in enumerate(sparse_hits, start=1):
            entry = fused.setdefault(hit.id, ScoredChunk(chunk_id=hit.id))
            entry.score += 1.0 / (k + rank)
            entry.sparse_rank = rank
            entry.sparse_score = float(hit.score)
            if not entry.content:
                entry.content = hit.content
            if not entry.metadata:
                entry.metadata = {
                    "document_id": hit.document_id,
                    "chunk_type": hit.chunk_type,
                    **hit.metadata,
                }
        return sorted(fused.values(), key=lambda c: c.score, reverse=True)

    # ------------------------------------------------------------------
    # 父块展开
    # ------------------------------------------------------------------

    def _fetch_contents(self, hits: list[ScoredChunk], table: str) -> list[str]:
        """为 rerank 准备内容（向量库 hit 通常已带 content）。"""
        return [h.content for h in hits]

    def _expand_parents(
        self, fused: list[ScoredChunk], table: str
    ) -> list[SearchResultItem]:
        """命中子块 → parent_id → 父块全文（经注入的 resolver）。"""
        resolved: dict[str, dict[str, Any]] = {}
        if self._parent_resolver is not None:
            try:
                resolved = self._parent_resolver([c.chunk_id for c in fused])
            except Exception:  # noqa: BLE001
                logger.warning("父块展开失败，返回子块内容", exc_info=True)

        results: list[SearchResultItem] = []
        for chunk in fused:
            info = resolved.get(chunk.chunk_id, {})
            metadata = chunk.metadata or {}
            results.append(
                SearchResultItem(
                    chunk_id=chunk.chunk_id,
                    parent_content=info.get("parent_content", chunk.content),
                    matched_child_text=info.get(
                        "matched_child_text", chunk.content
                    ),
                    source_file=info.get(
                        "source_file", metadata.get("source_file", "")
                    ),
                    page_number=int(
                        info.get("page_number", metadata.get("page_number", 0)) or 0
                    ),
                    breadcrumb=info.get(
                        "breadcrumb", metadata.get("breadcrumb", [])
                    ),
                    chunk_type=info.get(
                        "chunk_type", metadata.get("chunk_type", "text")
                    ),
                    score=chunk.score,
                    dense_rank=chunk.dense_rank,
                    sparse_rank=chunk.sparse_rank,
                    dense_score=chunk.dense_score,
                    sparse_score=chunk.sparse_score,
                    rerank_score=chunk.rerank_score,
                )
            )
        return results
