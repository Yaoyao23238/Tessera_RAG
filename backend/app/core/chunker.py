"""八阶段切分引擎主编排。

流水线（ARCHITECTURE 第七章）：
  1. 元素归一化清洗（structure.normalize_blocks）
  2. 面包屑追踪（structure.attach_breadcrumbs）
  3. 结构聚合（structure.aggregate_section：table/code/image 独立、list 合并）
  4. 长块拆分（4a 段落级 → 4b 语义断崖 → 4c 递归兜底）
  5. 空信息块过滤（可关：drop_empty_blocks，图片/表格零信息块不入库）
  6. LLM 辅助（可选，失败静默回退规则结果）
  7. Overlap（默认 50 token，不跨 section，table/code 不参与）
  8. 小块合并（< min_chunk_size 与同 section 相邻 text 合并）
  9. Parent-Child 构建（parent_child.assign_parents）

Embedder 以 duck-typing 注入（只需 embed(texts).dense），LLM 客户端实现
``chat(messages, **kw)`` 协议；两者均为 None 时跳过对应阶段，
保证引擎在无模型环境下可运行、可测试。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app.core import llm_chunker, parent_child, recursive, semantic, structure
from app.core.tokenizer import count_tokens, truncate_to_tokens
from app.core.types import (
    Block,
    Chunk,
    ChunkingConfig,
    ChunkResult,
    ChunkType,
)

logger = logging.getLogger(__name__)

#: 零信息块的占位内容集合（随聚合逻辑新增占位符时同步补充）。
_EMPTY_PLACEHOLDERS = frozenset({structure.PLACEHOLDER_IMAGE})


def is_empty_info_chunk(chunk: Chunk) -> bool:
    """判断块是否为「零信息块」：非文本块但内容为空或只剩占位符。

    背景（2026-09-07 PM/UX 走查）：图片块 text 为空时 structure 聚合会写成
    「[图片]」，既非空文本（逃过 merge_small 的空块丢弃）也无任何信息，
    却照常向量化入库 —— 实测检索 top5 被这类块霸榜。表格/代码同理。

    例外：表格若仍带结构化行列（headers/rows），结构本身就是信息，保留。
    """
    if chunk.chunk_type == ChunkType.TEXT:
        # 文本空块由 merge_small 的既有逻辑丢弃，此处不重复判定
        return False
    text = chunk.content.strip()
    if text and text not in _EMPTY_PLACEHOLDERS:
        return False
    table_data = chunk.table_data or {}
    return not (table_data.get("headers") or table_data.get("rows"))


class _Embedder(Protocol):
    """嵌入器协议（core.embedder.Embedder 实现）。"""

    def embed(self, texts: list[str]) -> Any: ...


class Chunker:
    """八阶段切分引擎。"""

    def __init__(
        self,
        embedder: _Embedder | None = None,
        llm_client: llm_chunker.ChatClient | None = None,
    ) -> None:
        self._embedder = embedder
        self._llm_client = llm_client

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def chunk(
        self,
        blocks: list[Block],
        config: ChunkingConfig | None = None,
        document_id: str = "",
    ) -> ChunkResult:
        """执行完整切分流水线。"""
        config = config or ChunkingConfig()

        # 阶段一 + 二：归一化清洗 + 面包屑。
        # 空图片块的丢弃随 drop_empty_blocks 提前到聚合之前（防幽灵打断，
        # 详见 structure.normalize_blocks docstring）
        blocks = structure.normalize_blocks(
            blocks, drop_empty_images=config.drop_empty_blocks
        )
        blocks = structure.attach_breadcrumbs(blocks)
        sections = structure.build_sections(blocks)

        # 阶段三 + 四：结构聚合 + 长块拆分（按 section 保持顺序）
        children: list[Chunk] = []
        for section in sections:
            units = self.structure_aggregate(section, config)
            children.extend(self.split_long_section(units, config))

        # 阶段四补：空信息块过滤（可关）。放在 overlap / merge / 父子块之前，
        # 让零信息块既不参与前缀传播也不进任何后续产物。
        if config.drop_empty_blocks:
            kept = [c for c in children if not is_empty_info_chunk(c)]
            if len(kept) != len(children):
                logger.info(
                    "空信息块过滤：丢弃 %d 块（共 %d）",
                    len(children) - len(kept),
                    len(children),
                )
            children = kept

        # 阶段五：LLM 辅助（可选，失败静默回退）
        if config.enable_llm and self._llm_client is not None:
            children = self.llm_review(children, config)

        # 阶段六：overlap
        children = self.apply_overlap(children, config)

        # 阶段七：小块合并
        children = self.merge_small(children, config)

        # 阶段八：Parent-Child 构建
        parents, children = parent_child.assign_parents(children, config)

        # 统一回填 document_id / token_count
        all_chunks = [*parents, *children]
        for chunk in all_chunks:
            chunk.document_id = document_id
            chunk.token_count = count_tokens(chunk.content)
            chunk.char_count = len(chunk.content)

        # 顶层块（父块 + 独生子块）按文档流顺序统一编号。
        # 父块在组内第一个子块的位置出现、独生子块原地出现，保证
        # 章节顺序与解析结果一致（修复父块全部提前导致的乱序）。
        parents_by_id = {p.id: p for p in parents}
        top_seen: set[str] = set()
        top_index = 0
        for child in children:
            if child.parent_id is None:
                child.chunk_index = top_index
                top_index += 1
            elif child.parent_id not in top_seen:
                top_seen.add(child.parent_id)
                parents_by_id[child.parent_id].chunk_index = top_index
                top_index += 1
        # 子块（parent_id 非空）按文档流顺序编号；独生子块用顶层编号
        child_index = 0
        for chunk in children:
            if chunk.parent_id is not None:
                chunk.chunk_index = child_index
                child_index += 1

        return ChunkResult(
            parents=parents, children=children, config_hash=config.config_hash()
        )

    # ------------------------------------------------------------------
    # 阶段三：结构聚合
    # ------------------------------------------------------------------

    def structure_aggregate(
        self, section: structure.Section, config: ChunkingConfig
    ) -> list[Chunk]:
        """table/code/image 独立成块；连续 list_item 合并；段落聚合为文本单元。"""
        return structure.aggregate_section(section, config)

    # ------------------------------------------------------------------
    # 阶段四：长块拆分（4a 段落 → 4b 语义 → 4c 递归）
    # ------------------------------------------------------------------

    def split_long_section(
        self, units: list[Chunk], config: ChunkingConfig
    ) -> list[Chunk]:
        """把超限文本单元拆到 child_chunk_size 量级。"""
        out: list[Chunk] = []
        for unit in units:
            if unit.chunk_type != ChunkType.TEXT:
                # table/code/image 保持整块，不拆分
                out.append(unit)
                continue
            out.extend(self._split_text_unit(unit, config))
        return out

    def _split_text_unit(
        self, unit: Chunk, config: ChunkingConfig
    ) -> list[Chunk]:
        """4a 段落级拆分；单段落超 1.5x child_chunk_size 进入 4b/4c。"""
        if unit.token_count <= config.child_chunk_size:
            return [unit]

        paragraphs = [p for p in unit.content.split("\n\n") if p.strip()]
        pieces: list[tuple[str, str]] = []  # (文本, 策略)
        buf = ""

        def flush_buf() -> None:
            nonlocal buf
            if buf.strip():
                pieces.append((buf.strip(), "structure"))
            buf = ""

        for para in paragraphs:
            para_tokens = count_tokens(para)
            if para_tokens > config.child_chunk_size * 1.5:
                # 单段落超长：4b 语义断崖 → 4c 递归兜底
                flush_buf()
                pieces.extend(self._split_long_paragraph(para, config))
            elif buf and count_tokens(buf + "\n\n" + para) > config.child_chunk_size:
                flush_buf()
                buf = para
            else:
                buf = f"{buf}\n\n{para}" if buf else para
        flush_buf()

        # 阶段四补：过小的碎片并入下一碎片（作为下一碎片的前缀）。
        #
        # 典型场景（CCF 目录）：「（人工智能）」这类表格分组标签，与它引入的
        # 若干表格 markdown 融成同一个 TEXT 单位（table_handling=markdown），
        # 超 child_chunk_size 后按 \n\n 段落切分，7 token 的标签放不下后面的
        # 表格段而被单独吐出，成为极短碎片块。这种碎片既逃过 merge_small 的
        # 向后合并（前邻是上一 section 尾块、breadcrumb 不同），也不会与表格
        # 同块，于是变成检索里的噪声。
        #
        # 这里刻意**不做**「这是不是标签」的启发式判定：任何小前导段（「见下表：」
        # 这类）一律并入下一碎片，语义上保持它与后续内容同块，也不跨 breadcrumb。
        # 连续多个小碎片会累积到一起，直到够大或到达末尾。
        pieces = self._merge_tiny_pieces(pieces, config)

        return [
            Chunk(
                document_id=unit.document_id,
                content=text,
                chunk_type=ChunkType.TEXT,
                breadcrumb=list(unit.breadcrumb),
                heading_text=unit.heading_text,
                page_number=unit.page_number,
                end_page=unit.end_page,
                bbox=list(unit.bbox),
                block_ids=list(unit.block_ids),
                strategy=strategy,
                token_count=count_tokens(text),
            )
            for text, strategy in pieces
        ]

    @staticmethod
    def _merge_tiny_pieces(
        pieces: list[tuple[str, str]], config: ChunkingConfig
    ) -> list[tuple[str, str]]:
        """把 token 数不足 min_chunk_size 的碎片并入下一碎片。

        策略标注取被并入的那个碎片（内容主体在它）；末尾碎片永远原样保留
        （没有"下一个"可并，保持边界真实），故循环结束时不可能有残留 carry。
        """
        merged: list[tuple[str, str]] = []
        carry = ""
        for i, (text, strategy) in enumerate(pieces):
            is_last = i == len(pieces) - 1
            combined = f"{carry}\n\n{text}" if carry else text
            if not is_last and count_tokens(combined) < config.min_chunk_size:
                carry = combined
                continue
            merged.append((combined, strategy))
            carry = ""
        return merged

    def _split_long_paragraph(
        self, para: str, config: ChunkingConfig
    ) -> list[tuple[str, str]]:
        """4b 语义断崖；无断崖或未启用则 4c 递归兜底。"""
        if config.enable_semantic and self._embedder is not None:
            def embed_fn(texts: list[str]) -> list[list[float]]:
                return self._embedder.embed(texts).dense  # type: ignore[union-attr]

            parts = semantic.semantic_split(
                para,
                embed_fn=embed_fn,
                threshold=config.semantic_threshold,
                max_tokens=config.child_chunk_size,
            )
            if parts:
                # 组内仍超限的段走递归兜底
                out: list[tuple[str, str]] = []
                for part in parts:
                    if count_tokens(part) > config.child_chunk_size * 1.5:
                        out.extend(
                            (t, "recursive")
                            for t in recursive.recursive_split(
                                part, config.child_chunk_size
                            )
                        )
                    else:
                        out.append((part, "semantic"))
                return out
        return [
            (t, "recursive")
            for t in recursive.recursive_split(para, config.child_chunk_size)
        ]

    # ------------------------------------------------------------------
    # 阶段五：LLM 辅助
    # ------------------------------------------------------------------

    def llm_review(
        self, chunks: list[Chunk], config: ChunkingConfig
    ) -> list[Chunk]:
        """LLM 边界合并 + 摘要增强；任何失败静默回退规则结果。"""
        try:
            return llm_chunker.llm_review(chunks, self._llm_client, config)
        except Exception:  # noqa: BLE001
            logger.warning("LLM 辅助切分失败，回退规则结果", exc_info=True)
            return chunks

    # ------------------------------------------------------------------
    # 阶段六：Overlap
    # ------------------------------------------------------------------

    def apply_overlap(
        self, chunks: list[Chunk], config: ChunkingConfig
    ) -> list[Chunk]:
        """相邻 text 子块前追加前块末尾 overlap token；不跨 section。

        - 前块过短（token < overlap*2）时跳过：tail 会覆盖前块大部分内容，
          造成相邻 chunk 整段重复
        - 后块原文过短（token < overlap*2）时同样跳过：前缀会把小块灌大到
          逃过 min_chunk_size 合并，表现为相邻 chunk 整段重叠
        - tail 基于前块原始内容（剥离其自身 overlap 前缀）计算，防止逐块累积
        """
        overlap = config.chunk_overlap
        if overlap <= 0:
            return chunks
        for i in range(1, len(chunks)):
            prev, curr = chunks[i - 1], chunks[i]
            if (
                prev.chunk_type == ChunkType.TEXT
                and curr.chunk_type == ChunkType.TEXT
                and prev.breadcrumb == curr.breadcrumb
                and prev.token_count >= overlap * 2
                and curr.token_count >= overlap * 2
            ):
                base = prev.content
                prev_prefix = getattr(prev, "_overlap_prefix", "")
                if prev_prefix and base.startswith(prev_prefix):
                    base = base[len(prev_prefix) :].lstrip()
                tail = truncate_to_tokens(base, overlap, from_tail=True)
                if tail.strip():
                    # strip 两侧空白：token 切点可能落在换行后使 tail 带前导空白，
                    # 后续 merge_small 的 cleaned/.strip() 会破坏 startswith 匹配
                    prefix = tail.strip()
                    curr.content = prefix + "\n" + curr.content.lstrip()
                    # 记录前缀供 merge_small / parent_child 精确剥离（Chunk 无 slots，临时属性不序列化）
                    curr._overlap_prefix = prefix
                    curr.token_count = count_tokens(curr.content)
                    curr.char_count = len(curr.content)
        return chunks

    # ------------------------------------------------------------------
    # 阶段七：小块合并与清洗
    # ------------------------------------------------------------------

    def merge_small(
        self, chunks: list[Chunk], config: ChunkingConfig
    ) -> list[Chunk]:
        """token < min_chunk_size 的 text 块与同 section 相邻 text 块合并。

        table/code/image 不参与合并；空块丢弃；清理首尾空白与重复换行。
        """
        cleaned: list[Chunk] = []
        for chunk in chunks:
            chunk.content = chunk.content.strip()
            if not chunk.content and chunk.chunk_type == ChunkType.TEXT:
                continue
            cleaned.append(chunk)

        merged: list[Chunk] = []
        for chunk in cleaned:
            if (
                chunk.chunk_type == ChunkType.TEXT
                and chunk.token_count < config.min_chunk_size
                and merged
                and merged[-1].chunk_type == ChunkType.TEXT
                and merged[-1].breadcrumb == chunk.breadcrumb
            ):
                prev = merged[-1]
                # 剥离阶段六加的 overlap 前缀：prev 末尾已含同样文本，
                # 直接拼接会使同一内容在合并后的 chunk 内出现两次
                content = chunk.content
                prefix = getattr(chunk, "_overlap_prefix", "")
                if prefix and content.startswith(prefix):
                    content = content[len(prefix) :].lstrip()
                if content:
                    prev.content = prev.content.rstrip() + "\n\n" + content
                prev.end_page = chunk.end_page
                prev.token_count = count_tokens(prev.content)
                prev.char_count = len(prev.content)
                prev.block_ids = [*prev.block_ids, *chunk.block_ids]
            else:
                merged.append(chunk)
        return merged
