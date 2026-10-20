"""Parent-Child 关系构建（阶段八）。

- 父 chunk：section 级，token ≈ parent_chunk_size，is_embedded=False，不向量化
- 子 chunk：细粒度，token ≈ child_chunk_size，is_embedded=True，parent_id 指向父块
- 同 section 内按序把子块分组到父块，累计 token 达到 parent_chunk_size 即封口
- 若分组内只有一个子块，则该子块独立成顶层块（parent_id=None），避免冗余父块
- enable_parent_child=false 时全部直接向量化，无父子关系
"""

from __future__ import annotations

from app.core.tokenizer import count_tokens
from app.core.types import Chunk, ChunkingConfig, ChunkType


def build_parent_groups(
    children: list[Chunk], config: ChunkingConfig
) -> list[list[Chunk]]:
    """把同一 section 内有序子块按 parent_chunk_size 分组。

    遇到 breadcrumb 变化强制封口（父块不跨 section）。
    """
    groups: list[list[Chunk]] = []
    current: list[Chunk] = []
    current_tokens = 0
    current_crumb: tuple[str, ...] | None = None

    def flush() -> None:
        nonlocal current, current_tokens, current_crumb
        if current:
            groups.append(current)
        current = []
        current_tokens = 0
        current_crumb = None

    for child in children:
        crumb = tuple(child.breadcrumb)
        if current_crumb is not None and crumb != current_crumb:
            flush()
        if current and current_tokens + child.token_count > config.parent_chunk_size:
            flush()
        current.append(child)
        current_tokens += child.token_count
        current_crumb = crumb
    flush()
    return groups


def _child_content(child: Chunk) -> str:
    """子块原文：剥离阶段六加的 overlap 前缀。

    父块是 section 级干净拼接，不应携带子块的检索上下文前缀，
    否则前块末尾文本会在父块中部重复出现。
    """
    content = child.content
    prefix = getattr(child, "_overlap_prefix", "")
    if prefix and content.startswith(prefix):
        content = content[len(prefix) :].lstrip()
    return content


def _make_parent(group: list[Chunk]) -> Chunk:
    """由子块组构建父块（内容为子块拼接，不向量化）。"""
    content = "\n\n".join(
        part for part in (_child_content(c) for c in group) if part.strip()
    )
    first = group[0]
    chunk_types = {c.chunk_type for c in group}
    # 组内类型单一时父块沿用该类型，混合时统一标 TEXT
    parent_type = next(iter(chunk_types)) if len(chunk_types) == 1 else ChunkType.TEXT
    return Chunk(
        document_id=first.document_id,
        content=content,
        chunk_type=parent_type,
        breadcrumb=list(first.breadcrumb),
        heading_text=first.heading_text,
        page_number=min(c.page_number for c in group),
        end_page=max(c.end_page for c in group),
        token_count=count_tokens(content),
        strategy=first.strategy,
        block_ids=[bid for c in group for bid in c.block_ids],
        is_embedded=False,  # 父块不向量化
    )


def assign_parents(
    children: list[Chunk], config: ChunkingConfig
) -> tuple[list[Chunk], list[Chunk]]:
    """构建父子关系。

    Returns:
        (parents, children)：父块列表（is_embedded=False）与
        已回填 parent_id 的子块列表（is_embedded=True）。
        enable_parent_child=false 时 parents 为空，子块直接向量化。
    """
    for child in children:
        child.is_embedded = True
    if not config.enable_parent_child:
        return [], children

    parents: list[Chunk] = []
    out_children: list[Chunk] = []
    for group in build_parent_groups(children, config):
        if len(group) == 1:
            # 独生子块直接作为顶层块，避免内容冗余的父块
            only = group[0]
            only.parent_id = None
            out_children.append(only)
            continue
        parent = _make_parent(group)
        parents.append(parent)
        for child in group:
            child.parent_id = parent.id
            out_children.append(child)
    return parents, out_children
