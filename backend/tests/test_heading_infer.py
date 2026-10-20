"""规则标题推断单测：数字序号 / 常见章节名 / 目录区跳过 / 防误判。"""

from __future__ import annotations

from conftest import make_heading, make_paragraph

from app.core.heading_infer import infer_headings
from app.core.types import BlockType


def _types(blocks):
    return [(b.type.value, b.level) for b in blocks]


def _headings(blocks):
    return [b.text for b in blocks if b.type is BlockType.HEADING]


class TestHeadingInfer:
    def test_numbered_headings(self):
        blocks = infer_headings(
            [
                make_paragraph("1 引言"),
                make_paragraph("正文段落内容，比较长，包含章节的实际叙述。"),
                make_paragraph("2.1 大语言模型与Function Calling"),
                make_paragraph("2.1.3 语义切分"),
                make_paragraph("普通正文内容"),
            ]
        )
        assert _headings(blocks) == ["1 引言", "2.1 大语言模型与Function Calling", "2.1.3 语义切分"]
        # 层级按序号段数
        assert [b.level for b in blocks if b.type is BlockType.HEADING] == [1, 2, 3]

    def test_section_names_with_whitespace_variants(self):
        blocks = infer_headings(
            [
                make_paragraph("摘  要"),
                make_paragraph("摘要正文内容很长很长用于模拟真实正文段落。"),
                make_paragraph("参考文献"),
                make_paragraph("引用条目一。"),
            ]
        )
        assert _headings(blocks) == ["摘  要", "参考文献"]

    def test_toc_skipped(self):
        """目录条目（数字序号短行）不被推断，避免与正文标题重复。"""
        blocks = infer_headings(
            [
                make_paragraph("目  录"),
                make_paragraph("1 引言"),
                make_paragraph("2 相关技术概述"),
                make_paragraph("3 系统需求分析"),
                make_paragraph("1  引言"),
                make_paragraph("Linux 作为开源操作系统的代表，已在服务器、云计算等领域占据主导地位。"),
                make_paragraph("2  相关技术概述"),
            ]
        )
        assert _headings(blocks) == ["1  引言", "2  相关技术概述"]

    def test_existing_headings_untouched(self):
        blocks = infer_headings(
            [
                make_heading("第一章 概述", level=1),
                make_paragraph("1 引言"),
                make_heading("2.1 已有标题", level=2),
            ]
        )
        # 已有 heading 原样保留；paragraph 数字序号照常推断
        assert _headings(blocks) == ["第一章 概述", "1 引言", "2.1 已有标题"]
        assert blocks[0].level == 1
        assert blocks[2].level == 2

    def test_no_false_positive(self):
        """正文特征行不被误判：句读标点 / 过长 / 非序号开头。"""
        blocks = infer_headings(
            [
                make_paragraph("2023 年，我国人工智能产业规模突破 5000 亿元。"),
                make_paragraph("23 级人工智能1班 张三 学号 23001001"),
                make_paragraph("1 引言，带逗号的句子不是标题"),
                make_paragraph("表1 系统三层架构"),
                make_paragraph("关键词：Linux；大语言模型；"),
            ]
        )
        assert _headings(blocks) == []

    def test_table_and_image_untouched(self):
        from conftest import make_table

        blocks = infer_headings([make_paragraph("1 引言"), make_table()])
        assert _headings(blocks) == ["1 引言"]
        assert blocks[1].type is BlockType.TABLE

    def test_idempotent(self):
        """幂等：对已推断结果重复应用不改变结果。"""
        source = [
            make_paragraph("1 引言"),
            make_paragraph("正文内容。"),
            make_paragraph("参考文献"),
        ]
        first = infer_headings(source)
        second = infer_headings(first)
        assert _types(first) == _types(second)


class TestRefineHeadingLevels:
    """已判 HEADING 块的层级细化（Docling level 恒 1 的修复）。"""

    def _levels(self, blocks):
        from app.core.heading_infer import refine_heading_levels

        return [b.level for b in refine_heading_levels(blocks)]

    def test_numeric_multilevel(self):
        blocks = [
            make_heading("4 著录项目与著录格式"),
            make_heading("4.1 专著"),
            make_heading("4.1.1 著录项目"),
            make_heading("4.1.2 著录格式"),
            make_heading("5 著录细则"),
        ]
        assert self._levels(blocks) == [1, 2, 3, 3, 1]

    def test_numeric_without_space_cjk(self):
        """OCR 丢空格：「1范围」「4著录项目」照常分级。"""
        blocks = [make_heading("1范围"), make_heading("4.1专著"), make_heading("4.1.1著录项目")]
        assert self._levels(blocks) == [1, 2, 3]

    def test_numeric_no_space_ascii_rejected(self):
        """无空格且紧跟非 CJK（如「1.5x」）不重新分级，保持原层级。"""
        blocks = [make_heading("1.5x 性能提升", level=1)]
        assert self._levels(blocks) == [1]

    def test_unit_word_rejected(self):
        """序号紧跟量词字（「1级 XX」）不是章节序号，保持原层级。"""
        blocks = [make_heading("1级 等保要求", level=1)]
        assert self._levels(blocks) == [1]

    def test_chinese_ordinal(self):
        """「一、 A 类」中文序号小节 → level 2。"""
        blocks = [
            make_heading("中国计算机学会推荐国际学术期刊"),
            make_heading("一、 A 类"),
            make_heading("二、 B 类"),
        ]
        assert self._levels(blocks) == [1, 2, 2]

    def test_chapter_section(self):
        blocks = [make_heading("第一章 概述"), make_heading("第三节 系统设计")]
        assert self._levels(blocks) == [1, 2]

    def test_unnumbered_headings_keep_level(self):
        """无序号标题（术语条目/栏目名）无法从文本分级，保持原层级。"""
        blocks = [
            make_heading("主要责任者 creator", level=1),
            make_heading("前 言", level=1),
        ]
        assert self._levels(blocks) == [1, 1]

    def test_non_heading_untouched(self):
        """refine 只调层级不改类型：paragraph/table 原样。"""
        from conftest import make_table

        blocks = [make_paragraph("4.1 仍是段落"), make_table()]
        from app.core.heading_infer import refine_heading_levels

        refine_heading_levels(blocks)
        assert blocks[0].type is BlockType.PARAGRAPH
        assert blocks[1].type is BlockType.TABLE

    def test_infer_headings_end_to_end_levels(self):
        """infer_headings 全链路：段落提升 + docling 标题细化一次完成。"""
        blocks = infer_headings(
            [
                make_heading("4 著录项目与著录格式"),  # docling 已判标题，level 1
                make_heading("4.1 专著"),
                make_paragraph("4.1.1 著录项目"),  # 段落提升为标题
                make_paragraph("正文内容，普通段落。"),
            ]
        )
        assert [b.type for b in blocks[:3]] == [BlockType.HEADING] * 3
        assert [b.level for b in blocks[:3]] == [1, 2, 3]
