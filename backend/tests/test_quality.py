"""core.quality 解析质量检测测试：4 类警告 + 统计摘要 + token 预估。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from conftest import make_heading, make_paragraph, make_table  # noqa: E402

from app.core.quality import (  # noqa: E402
    compute_parse_report,
    _detect_garbled,
    _detect_no_heading,
    _detect_repeated_lines,
    _detect_short_blocks,
)
from app.core.types import Block, BlockType  # noqa: E402


def _report(blocks: list[Block]):
    return compute_parse_report(blocks)


class TestNoHeading:
    def test_no_heading_detected(self):
        blocks = [make_paragraph(f"第{i}段正文内容，长度达标。", page=1) for i in range(15)]
        warning = _detect_no_heading(blocks, _report(blocks))
        assert warning is not None
        assert warning.kind == "no_heading"
        assert warning.severity == "info"

    def test_has_heading_not_detected(self):
        blocks = [make_heading("第一章")] + [
            make_paragraph(f"第{i}段正文内容，长度达标。") for i in range(15)
        ]
        assert _detect_no_heading(blocks, _report(blocks)) is None

    def test_tiny_document_skipped(self):
        blocks = [make_paragraph("只有一段。")]
        assert _detect_no_heading(blocks, _report(blocks)) is None


class TestShortBlocks:
    def test_ocr_failure_danger(self):
        # 每页字符极少 → 疑似 OCR 失败
        blocks = [make_paragraph("残留", page=p) for p in range(1, 11)]
        warning = _detect_short_blocks(blocks, _report(blocks))
        assert warning is not None
        assert warning.severity == "danger"
        assert warning.action == "reparse"

    def test_fragmented_warning(self):
        # 大量超短块（碎裂），但每页字符量正常
        long_blocks = [
            make_paragraph("这是一段足够长的正文内容，用来拉高每页字符平均值。" * 3, page=p)
            for p in range(1, 4)
        ]
        short_blocks = [
            make_paragraph(f"短{i}", page=1) for i in range(30)
        ]
        blocks = long_blocks + short_blocks
        warning = _detect_short_blocks(blocks, _report(blocks))
        assert warning is not None
        assert warning.severity == "warning"
        assert warning.action == "open_clean_dialog"

    def test_normal_document_clean(self):
        blocks = [
            make_paragraph("正常长度的段落内容，每页两百字左右的正常文档密度。" * 10, page=p)
            for p in range(1, 6)
        ]
        assert _detect_short_blocks(blocks, _report(blocks)) is None


class TestRepeatedLines:
    def _blocks_with_header(self, pages: int, header: str) -> list[Block]:
        blocks: list[Block] = []
        for p in range(1, pages + 1):
            blocks.append(make_paragraph(header, page=p))
            blocks.append(
                make_paragraph(f"第{p}页的正文内容，每页都不一样的实质文本。", page=p)
            )
        return blocks

    def test_header_residue_detected(self):
        blocks = self._blocks_with_header(8, "某公司机密文档")
        warning = _detect_repeated_lines(blocks, _report(blocks))
        assert warning is not None
        assert warning.kind == "repeated_lines"
        assert "某公司机密文档" in warning.detail["examples"]

    def test_page_number_suffix_normalized(self):
        # 末尾带不同页码的同一页眉应归一化为同一文本
        blocks: list[Block] = []
        for p in range(1, 9):
            blocks.append(make_paragraph(f"产品手册 第{p}页", page=p))
            blocks.append(make_paragraph(f"第{p}页各不相同的正文内容文本。", page=p))
        warning = _detect_repeated_lines(blocks, _report(blocks))
        assert warning is not None

    def test_few_pages_skipped(self):
        blocks = self._blocks_with_header(4, "某页眉")
        assert _detect_repeated_lines(blocks, _report(blocks)) is None

    def test_unique_edges_clean(self):
        blocks = [
            make_paragraph(f"第{p}页的首块内容各不相同编号{p * 37}。", page=p)
            for p in range(1, 9)
        ]
        assert _detect_repeated_lines(blocks, _report(blocks)) is None


class TestGarbled:
    def test_danger_level(self):
        text = "正常文本" + "�" * 20 + "其余内容"
        blocks = [make_paragraph(text)]
        warning = _detect_garbled(blocks, _report(blocks))
        assert warning is not None
        assert warning.severity == "danger"

    def test_warning_level(self):
        # 约 1% 乱码
        text = "正常的正文内容。" * 100 + "�" * 8
        blocks = [make_paragraph(text)]
        warning = _detect_garbled(blocks, _report(blocks))
        assert warning is not None
        assert warning.severity == "warning"

    def test_clean_text_no_warning(self):
        blocks = [make_paragraph("完全正常的文本内容，没有任何乱码。")]
        assert _detect_garbled(blocks, _report(blocks)) is None


class TestReport:
    def test_stats_and_type_counts(self):
        blocks = [
            make_heading("标题", level=1),
            make_paragraph("正文内容。"),
            make_table(),
            Block(type=BlockType.IMAGE, text="", page=1),
        ]
        report = _report(blocks)
        assert report.total_blocks == 4
        assert report.type_counts["heading"] == 1
        assert report.type_counts["paragraph"] == 1
        assert report.type_counts["table"] == 1
        assert report.type_counts["image"] == 1
        assert report.total_chars > 0
        assert report.est_tokens_text > 0
        assert report.est_tokens_table > 0

    def test_page_count_uses_end_page(self):
        blocks = [make_paragraph("跨页内容", page=2)]
        blocks[0].end_page = 5
        assert _report(blocks).page_count == 5

    def test_to_dict_serializable(self):
        import json

        report = _report([make_paragraph("内容"), make_heading("标题")])
        json.dumps(report.to_dict(), ensure_ascii=False)  # 不抛异常即可


class TestProblemBlocks:
    def test_short_and_garbled_flagged(self):
        from app.core.quality import find_problem_block_indexes

        normal = "这是一段完全正常、长度足够、没有任何问题的正文内容，不需要清洗处理。"
        blocks = [
            make_paragraph(normal, page=1),
            make_paragraph("碎块", page=1),  # 超短
            make_paragraph("含乱码字符�的正常长度文本块", page=2),  # 乱码
            make_paragraph(normal, page=2),
        ]
        problem = find_problem_block_indexes(blocks)
        assert problem == {1, 2}

    def test_repeated_header_flagged(self):
        from app.core.quality import find_problem_block_indexes

        blocks: list[Block] = []
        for p in range(1, 9):
            blocks.append(make_paragraph("某文档页眉", page=p))
            blocks.append(
                make_paragraph(f"第{p}页各不相同的正文内容编号{p * 53}。", page=p)
            )
        problem = find_problem_block_indexes(blocks)
        # 每页首块（页眉残留）被判为问题块，正文块不在内
        assert 0 in problem and 2 in problem
        assert 1 not in problem and 3 not in problem

    def test_clean_document_no_problems(self):
        from app.core.quality import find_problem_block_indexes

        blocks = [
            make_paragraph("正常的正文内容，长度达标且无乱码。", page=p)
            for p in range(1, 4)
        ]
        assert find_problem_block_indexes(blocks) == set()

    def test_est_tokens_problem_in_report(self):
        blocks = [
            make_paragraph("正常的正文内容，长度达标且无乱码。", page=1),
            make_paragraph("碎", page=1),
        ]
        report = _report(blocks)
        assert report.est_tokens_problem > 0
        assert report.est_tokens_problem < report.est_tokens_text
