"""结构聚合测试：面包屑追踪 / section 划分 / 阶段三聚合规则。"""

from __future__ import annotations

from conftest import (
    make_heading,
    make_list_item,
    make_paragraph,
    make_table,
)

from app.core.structure import (
    aggregate_section,
    attach_breadcrumbs,
    build_sections,
    normalize_blocks,
)
from app.core.types import Block, BlockType, ChunkType


class TestNormalizeBlocks:
    def test_blank_blocks_removed(self):
        blocks = [
            make_paragraph("内容"),
            Block(type=BlockType.PARAGRAPH, text="   "),
            Block(type=BlockType.PARAGRAPH, text=""),
        ]
        result = normalize_blocks(blocks)
        assert len(result) == 1
        assert result[0].text == "内容"


class TestAttachBreadcrumbs:
    def test_heading_stack_pop_and_push(self):
        blocks = [
            make_heading("第一章", level=1),
            make_paragraph("正文1"),
            make_heading("1.1 节", level=2),
            make_paragraph("正文2"),
            make_heading("第二章", level=1),
            make_paragraph("正文3"),
        ]
        attach_breadcrumbs(blocks)
        assert blocks[1].breadcrumb == ["第一章"]
        assert blocks[3].breadcrumb == ["第一章", "1.1 节"]
        # 同级 heading 弹出栈中 level >= 当前的标题
        assert blocks[5].breadcrumb == ["第二章"]

    def test_heading_records_own_path(self):
        blocks = [make_heading("标题", level=1)]
        attach_breadcrumbs(blocks)
        assert blocks[0].breadcrumb == ["标题"]


class TestBuildSections:
    def test_sections_split_by_heading(self):
        blocks = [
            make_paragraph("前言内容"),
            make_heading("第一章", level=1),
            make_paragraph("章节内容"),
        ]
        sections = build_sections(attach_breadcrumbs(blocks))
        assert len(sections) == 2
        assert sections[0].breadcrumb == []  # 无标题隐式 section
        assert sections[1].heading_text == "第一章"
        assert len(sections[1].blocks) == 1


class TestAggregateSection:
    def test_default_table_handling_is_markdown(self, config):
        """默认 table_handling 为 markdown：表格融入正文，不独立成块。"""
        blocks = [make_paragraph("前文"), make_table(), make_paragraph("后文")]
        attach_breadcrumbs(blocks)
        section = build_sections(blocks)[0]
        chunks = aggregate_section(section, config)
        types = [c.chunk_type for c in chunks]
        assert types == [ChunkType.TEXT]
        assert "| a | b |" in chunks[0].content

    def test_table_code_image_independent(self, config):
        blocks = [
            make_paragraph("前文"),
            make_table(),
            make_paragraph("后文"),
        ]
        attach_breadcrumbs(blocks)
        section = build_sections(blocks)[0]
        # preserve 模式：表格独立成块（默认值已改为 markdown，此处显式指定）
        config.table_handling = "preserve"
        chunks = aggregate_section(section, config)
        types = [c.chunk_type for c in chunks]
        assert types == [ChunkType.TEXT, ChunkType.TABLE, ChunkType.TEXT]
        # 表格保留 table_data
        assert chunks[1].table_data is not None
        assert chunks[1].table_data["headers"] == ["a", "b"]

    def test_table_handling_markdown_merges_into_text(self, config):
        """markdown 模式：表格转为文本参与正文聚合（不独立成块）。"""
        config.table_handling = "markdown"
        blocks = [
            make_paragraph("前文"),
            make_table(),
            make_paragraph("后文"),
        ]
        attach_breadcrumbs(blocks)
        section = build_sections(blocks)[0]
        chunks = aggregate_section(section, config)
        types = [c.chunk_type for c in chunks]
        assert types == [ChunkType.TEXT]
        assert "前文" in chunks[0].content
        assert "后文" in chunks[0].content
        assert "| a | b |" in chunks[0].content

    def test_table_handling_skip_drops_table(self, config):
        """skip 模式：表格块整体丢弃。"""
        config.table_handling = "skip"
        blocks = [
            make_paragraph("前文"),
            make_table(),
            make_paragraph("后文"),
        ]
        attach_breadcrumbs(blocks)
        section = build_sections(blocks)[0]
        chunks = aggregate_section(section, config)
        # 表格被丢弃后，前后文相邻自然合并为一个 TEXT
        types = [c.chunk_type for c in chunks]
        assert types == [ChunkType.TEXT]
        assert "前文" in chunks[0].content and "后文" in chunks[0].content
        assert "|" not in chunks[0].content

    def test_consecutive_list_items_merged(self, config):
        blocks = [
            make_list_item("第一项"),
            make_list_item("第二项", level=1),
            make_paragraph("段落"),
        ]
        attach_breadcrumbs(blocks)
        section = build_sections(blocks)[0]
        chunks = aggregate_section(section, config)
        assert len(chunks) == 2
        assert "- 第一项" in chunks[0].content
        assert "  - 第二项" in chunks[0].content
        assert chunks[1].content == "段落"

    def test_breadcrumb_propagated(self, config):
        blocks = [
            make_heading("章节", level=1),
            make_paragraph("内容"),
        ]
        attach_breadcrumbs(blocks)
        section = build_sections(blocks)[0]
        chunks = aggregate_section(section, config)
        assert chunks[0].breadcrumb == ["章节"]
        assert chunks[0].heading_text == "章节"
