"""Parent-Child 构建测试。"""

from __future__ import annotations

from app.core.parent_child import assign_parents, build_parent_groups
from app.core.types import Chunk, ChunkingConfig


def _child(content: str, tokens: int, crumb: list[str] | None = None) -> Chunk:
    return Chunk(
        content=content,
        breadcrumb=crumb or ["章节A"],
        token_count=tokens,
    )


class TestBuildParentGroups:
    def test_group_by_parent_size(self):
        config = ChunkingConfig(parent_chunk_size=100)
        children = [_child(f"c{i}", 40) for i in range(5)]
        groups = build_parent_groups(children, config)
        # 100/40 → 每组最多 2 个（80），第 3 个开新组
        assert [len(g) for g in groups] == [2, 2, 1]

    def test_group_breaks_at_section_boundary(self):
        config = ChunkingConfig(parent_chunk_size=1000)
        children = [
            _child("a", 10, ["章节A"]),
            _child("b", 10, ["章节A"]),
            _child("c", 10, ["章节B"]),
        ]
        groups = build_parent_groups(children, config)
        assert [len(g) for g in groups] == [2, 1]


class TestAssignParents:
    def test_multi_child_group_creates_parent(self):
        config = ChunkingConfig(parent_chunk_size=100)
        children = [_child(f"内容{i}", 40) for i in range(2)]
        parents, out = assign_parents(children, config)
        assert len(parents) == 1
        parent = parents[0]
        assert parent.is_embedded is False
        assert "内容0" in parent.content and "内容1" in parent.content
        assert all(c.parent_id == parent.id for c in out)
        assert all(c.is_embedded for c in out)

    def test_single_child_group_stays_standalone(self):
        config = ChunkingConfig(parent_chunk_size=1000)
        children = [_child("独立块", 10)]
        parents, out = assign_parents(children, config)
        assert parents == []
        assert out[0].parent_id is None
        assert out[0].is_embedded is True

    def test_disabled_parent_child(self):
        config = ChunkingConfig(enable_parent_child=False)
        children = [_child(f"c{i}", 10) for i in range(3)]
        parents, out = assign_parents(children, config)
        assert parents == []
        assert all(c.parent_id is None and c.is_embedded for c in out)
