"""结构聚合（切分阶段一清洗、阶段二面包屑、阶段三结构聚合）。

规则（ARCHITECTURE 第七章）：
- 阶段一清洗：过滤纯空白、合并被拆散的连续文本（parser 已做类型映射）
- 阶段二：标题栈维护面包屑 —— 遇到 heading 弹出栈中 level >= 当前 level
  的标题后入栈；非 heading 元素记录栈快照
- 阶段三：table/code/image 独立成块；连续 list_item 合并；
  其余文本块聚合为 section 文本单元，由后续阶段决定是否拆分
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.tokenizer import count_tokens
from app.core.types import Block, BlockType, Chunk, ChunkingConfig, ChunkType

_BLANK_RE = re.compile(r"\s+")

#: 图片块无文本时的兜底内容。chunker 据此识别「零信息块」（见
#: chunker.is_empty_info_chunk），集中定义避免两处字面量漂移。
PLACEHOLDER_IMAGE = "[图片]"


def normalize_blocks(
    blocks: list[Block], drop_empty_images: bool = False
) -> list[Block]:
    """阶段一清洗：过滤纯空白块，合并相邻同页普通段落（解析器拆散的文本）。

    drop_empty_images=True 时在此丢弃**空图片块**（text 空或占位符），必须在
    聚合之前——「幽灵打断」根因：aggregate_section 遇 image 会 flush_text()
    让前面的文本孤立成 unit，而空图片块随后又被 drop_empty_blocks 过滤掉，
    打断者自己消失，只剩孤立小标签 unit（2026-10-08 归因 v3）。提前丢弃后
    文本流不再被打断，标签自然融进相邻内容。
    """
    cleaned: list[Block] = []
    for block in blocks:
        if block.type != BlockType.IMAGE and not block.text.strip():
            continue
        if (
            drop_empty_images
            and block.type == BlockType.IMAGE
            and (not block.text.strip() or block.text.strip() == PLACEHOLDER_IMAGE)
        ):
            continue
        block.text = block.text.strip("\n")
        cleaned.append(block)
    return cleaned


def attach_breadcrumbs(blocks: list[Block]) -> list[Block]:
    """阶段二：面包屑追踪（标题栈）。"""
    stack: list[tuple[int, str]] = []  # (level, 标题文本)
    for block in blocks:
        if block.type == BlockType.HEADING:
            level = block.level if block.level > 0 else 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, block.text.strip()))
            block.breadcrumb = [text for _, text in stack]
        else:
            block.breadcrumb = [text for _, text in stack]
    return blocks


@dataclass
class Section:
    """同一标题路径下的 Block 序列。"""

    breadcrumb: list[str] = field(default_factory=list)
    heading_text: str = ""
    blocks: list[Block] = field(default_factory=list)
    start_page: int = 0
    end_page: int = 0

    @property
    def token_count(self) -> int:
        return sum(count_tokens(b.text) for b in self.blocks)


def build_sections(blocks: list[Block]) -> list[Section]:
    """按标题路径把 Block 流切分为 section 列表。

    heading 块本身开启新 section（其文本作为 heading_text，不进正文）。
    文档开头无标题的内容归入 breadcrumb 为空的隐式 section。
    """
    sections: list[Section] = []
    current: Section | None = None
    for block in blocks:
        if block.type == BlockType.HEADING:
            current = Section(
                breadcrumb=list(block.breadcrumb),
                heading_text=block.text.strip(),
                blocks=[],
                start_page=block.page,
                end_page=block.end_page,
            )
            sections.append(current)
            continue
        if current is None:
            current = Section(
                breadcrumb=[],
                heading_text="",
                blocks=[],
                start_page=block.page,
                end_page=block.end_page,
            )
            sections.append(current)
        current.blocks.append(block)
        current.end_page = max(current.end_page, block.end_page)
    return sections


def _make_chunk(
    *,
    content: str,
    chunk_type: ChunkType,
    breadcrumb: list[str],
    heading_text: str,
    blocks: list[Block],
    strategy: str = "structure",
    table_data: dict | None = None,
) -> Chunk:
    """从 Block 组构建 draft Chunk（后续阶段再补 token_count 等）。"""
    pages = [b.page for b in blocks] or [0]
    end_pages = [b.end_page for b in blocks] or [0]
    bbox = next((b.bbox for b in blocks if b.bbox), [])
    return Chunk(
        content=content,
        chunk_type=chunk_type,
        breadcrumb=list(breadcrumb),
        heading_text=heading_text,
        page_number=min(pages),
        end_page=max(end_pages),
        bbox=list(bbox),
        block_ids=[b.id for b in blocks],
        strategy=strategy,
        table_data=table_data,
        token_count=count_tokens(content),
    )


def aggregate_section(section: Section, config: ChunkingConfig) -> list[Chunk]:
    """阶段三：结构聚合。

    - table/code/image 独立成块（不与其他类型合并），保留 table_data
    - 连续 list_item 合并为一个块
    - 其余段落聚合成文本单元（整体超限的交给阶段四拆分）
    """
    chunks: list[Chunk] = []
    text_buf: list[Block] = []
    list_buf: list[Block] = []

    def flush_text() -> None:
        if not text_buf:
            return
        content = "\n\n".join(b.text for b in text_buf if b.text.strip())
        if content.strip():
            chunks.append(
                _make_chunk(
                    content=content,
                    chunk_type=ChunkType.TEXT,
                    breadcrumb=section.breadcrumb,
                    heading_text=section.heading_text,
                    blocks=text_buf,
                )
            )
        text_buf.clear()

    def flush_list() -> None:
        if not list_buf:
            return
        lines = []
        for b in list_buf:
            indent = "  " * max(0, b.list_level)
            lines.append(f"{indent}- {b.text.strip()}")
        content = "\n".join(lines)
        chunks.append(
            _make_chunk(
                content=content,
                chunk_type=ChunkType.TEXT,
                breadcrumb=section.breadcrumb,
                heading_text=section.heading_text,
                blocks=list_buf,
            )
        )
        list_buf.clear()

    for block in section.blocks:
        if block.type == BlockType.TABLE:
            # 表格处理模式（config.table_handling）：
            # - skip：表格块整体丢弃，不进入切分产物
            # - markdown：转为 markdown 文本参与正文聚合（可合并/被切分）
            # - preserve：独立成块保留 table_data（默认）
            if config.table_handling == "skip":
                continue
            if config.table_handling == "markdown":
                flush_list()
                text_buf.append(block)
                continue
            flush_text()
            flush_list()
            table_data = block.table_data or {}
            content = table_data.get("markdown") or block.text
            chunks.append(
                _make_chunk(
                    content=content,
                    chunk_type=ChunkType.TABLE,
                    breadcrumb=section.breadcrumb,
                    heading_text=section.heading_text,
                    blocks=[block],
                    table_data=table_data or None,
                )
            )
        elif block.type == BlockType.CODE:
            flush_text()
            flush_list()
            chunks.append(
                _make_chunk(
                    content=block.text,
                    chunk_type=ChunkType.CODE,
                    breadcrumb=section.breadcrumb,
                    heading_text=section.heading_text,
                    blocks=[block],
                )
            )
        elif block.type == BlockType.IMAGE:
            flush_text()
            flush_list()
            content = block.text.strip() or PLACEHOLDER_IMAGE
            chunks.append(
                _make_chunk(
                    content=content,
                    chunk_type=ChunkType.IMAGE,
                    breadcrumb=section.breadcrumb,
                    heading_text=section.heading_text,
                    blocks=[block],
                )
            )
        elif block.type == BlockType.LIST_ITEM:
            flush_text()
            list_buf.append(block)
        else:  # paragraph 等文本块
            flush_list()
            # 相邻同文本块去重（OCR 页眉/页脚等重复捕获）
            if (
                block.text.strip()
                and text_buf
                and text_buf[-1].text.strip() == block.text.strip()
            ):
                continue
            text_buf.append(block)
    flush_text()
    flush_list()
    return chunks
