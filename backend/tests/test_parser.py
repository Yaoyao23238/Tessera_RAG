"""解析器增强测试：TXT 编码自动检测 / 页眉页脚自动清理 / 元数据提取。

纯 core 层测试，不依赖 docling / 网络 / GPU。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.metadata import detect_language, extract_metadata, extract_title
from app.core.parser import EmptyDocumentError, Parser
from app.core.quality import remove_repeated_edges
from app.core.types import Block, BlockType
from conftest import make_heading, make_list_item, make_paragraph


# ---------------------------------------------------------------------------
# 待办3：TXT 编码自动检测
# ---------------------------------------------------------------------------


def test_utf8_txt_parsed(tmp_path):
    p = tmp_path / "utf8.txt"
    p.write_text("# 标题\n第一段。\n第二段。", encoding="utf-8")
    blocks = Parser().parse(p)
    assert blocks[0].type == BlockType.HEADING
    assert blocks[0].text == "标题"
    assert [b.text for b in blocks if b.type == BlockType.PARAGRAPH] == [
        "第一段。",
        "第二段。",
    ]


def test_utf8_bom_txt_parsed(tmp_path):
    """UTF-8 带 BOM：首字符 \\ufeff 应被 strip（_parse_plain_text 已 strip）。"""
    p = tmp_path / "bom.txt"
    p.write_bytes(b"\xef\xbb\xbfhello world")
    blocks = Parser().parse(p)
    assert blocks[0].text == "hello world"


def test_gbk_txt_parsed(tmp_path):
    """GBK 编码：UTF-8 解码失败后，由 _decode_cjk 的 gb18030（GBK 超集）命中。"""
    p = tmp_path / "gbk.txt"
    p.write_bytes("# 中文标题\n这是 GBK 编码的内容。".encode("gbk"))
    blocks = Parser().parse(p)
    assert blocks[0].type == BlockType.HEADING
    assert blocks[0].text == "中文标题"
    assert "GBK 编码" in blocks[1].text


def test_unknown_encoding_fallback_no_raise(tmp_path):
    """完全无法识别时回退 UTF-8 宽容解码，不抛异常（errors=replace）。"""
    p = tmp_path / "binary.txt"
    p.write_bytes(b"\xff\xfe\x00\x99\x88 raw bytes here")
    blocks = Parser().parse(p)
    # 至少能产出块，且 ^ 开头不因 # 被误判标题
    assert len(blocks) >= 1
    assert all(b.type == BlockType.PARAGRAPH or b.type == BlockType.HEADING for b in blocks)


def test_empty_txt_raises(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("   \n\n  ", encoding="utf-8")
    with pytest.raises(EmptyDocumentError):
        Parser().parse(p)


# ---------------------------------------------------------------------------
# 待办6：页眉页脚重复行自动清理
# ---------------------------------------------------------------------------


def _build_hf_doc() -> list[Block]:
    """构造 8 页文档：每页首块页眉「公司年度报告」，末块页脚「内部资料 第N页」。

    页脚带文本（非纯页码），归一化后残留「内部资料」，跨页重复可被检出。
    """
    blocks: list[Block] = []
    for page in range(1, 9):
        blocks.append(make_paragraph("公司年度报告", page))
        blocks.append(make_paragraph(f"第{page}页正文内容，描述业务进展。", page))
        blocks.append(make_paragraph(f"内部资料 第{page}页", page))
    return blocks


def test_remove_repeated_edges_cleanup():
    blocks = _build_hf_doc()
    cleaned = remove_repeated_edges(blocks)
    # 页眉「公司年度报告」与页脚「第N页」被剔除，正文保留
    assert [b.text for b in cleaned] == [
        f"第{p}页正文内容，描述业务进展。" for p in range(1, 9)
    ]


def test_remove_repeated_edges_noop_on_unique():
    """无重复时原样返回（同一引用，不制造新列表）。"""
    blocks = [make_paragraph("唯一内容一", 1), make_paragraph("唯一内容二", 1)]
    result = remove_repeated_edges(blocks)
    assert result is blocks
    assert len(result) == 2


def test_remove_repeated_edges_keeps_repeated_body():
    """正文中正常重复的段落（非页首/页尾边缘）不被误删。"""
    blocks: list[Block] = []
    for page in range(1, 9):
        blocks.append(make_paragraph("公司报告", page))              # 页首 → 删
        blocks.append(make_paragraph("普通正文。", page))             # 中间正文
        blocks.append(make_paragraph("重要结论：战略不变", page))      # 中间重复 → 不应删
        blocks.append(make_paragraph(f"内部资料 第{page}页", page))   # 页脚 → 删
    cleaned = remove_repeated_edges(blocks)
    texts = [b.text for b in cleaned]
    assert "公司报告" not in texts
    assert not any(x.startswith("内部资料") for x in texts)
    assert "普通正文。" in texts
    assert texts.count("重要结论：战略不变") == 8  # 中间重复正文保留


# ---------------------------------------------------------------------------
# 待办7：元数据提取
# ---------------------------------------------------------------------------


def test_detect_language():
    assert detect_language("你好世界，这是中文。") == "zh"
    assert detect_language("hello world, this is english.") == "en"
    assert detect_language("こんにちは、世界。") == "ja"
    assert detect_language("안녕하세요 세계") == "ko"
    assert detect_language("1234 !!!,.") is None
    assert detect_language("") is None


def test_extract_title_from_heading():
    blocks = [make_heading("第一章 概述", level=1), make_paragraph("正文。")]
    meta = extract_metadata(blocks)
    assert meta["title"] == "第一章 概述"


def test_extract_title_fallback_paragraph():
    blocks = [make_paragraph("文章标题"), make_paragraph("正文内容。")]
    assert extract_title(blocks) == "文章标题"


def test_extract_metadata_merge_docling_meta():
    blocks = [make_heading("报告标题", level=1), make_paragraph("中文正文内容。")]
    meta = extract_metadata(blocks, {"title": "Docling标题", "author": "张三"})
    # 规则标题优先于 Docling 标题；author 取 Docling；lang 由文本判定
    assert meta["title"] == "报告标题"
    assert meta["author"] == "张三"
    assert meta["lang"] == "zh"


def test_extract_metadata_no_author_without_docling():
    blocks = [make_heading("标题", level=1), make_paragraph("正文。")]
    meta = extract_metadata(blocks)
    assert meta["author"] is None
    assert meta["lang"] == "zh"


def test_extract_metadata_empty_blocks():
    meta = extract_metadata([])
    assert meta["title"] is None
    assert meta["author"] is None
    assert meta["lang"] is None


def test_md_parse_with_meta(tmp_path):
    """纯文本/Markdown 也应提取元数据（title/lang），而非始终返回空。"""
    p = tmp_path / "doc.md"
    p.write_text("# 季度报告\n本文档是中文内容，用于测试。", encoding="utf-8")
    _blocks, meta = Parser().parse_with_meta(p)
    assert meta["title"] == "季度报告"
    assert meta["lang"] == "zh"


# ---------------------------------------------------------------------------
# P0：table_data 结构化提取（_extract_table_grid）
# ---------------------------------------------------------------------------


class _FakeCell:
    """docling TableCell 桩。"""

    def __init__(self, text, r, c, column_header=False):
        self.text = text
        self.start_row_offset_idx = r
        self.start_col_offset_idx = c
        self.column_header = column_header


class _FakeTableData:
    def __init__(self, cells, num_rows, num_cols):
        self.table_cells = cells
        self.num_rows = num_rows
        self.num_cols = num_cols


class _FakeTableItem:
    """docling TableItem 桩：df 为 export_to_dataframe 返回值（None 表示抛异常走兜底）。"""

    def __init__(self, df=None, data=None):
        self._df = df
        self.data = data

    def export_to_dataframe(self, doc=None):
        if self._df is None:
            raise RuntimeError("no dataframe")
        return self._df


def test_table_grid_dataframe_with_headers():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame([["a", "1"], ["b", "2"]], columns=["名称", "数量"])
    item = _FakeTableItem(df=df)
    headers, rows = Parser._extract_table_grid(item, doc=None)
    assert headers == ["名称", "数量"]
    assert rows == [["a", "1"], ["b", "2"]]


def test_table_grid_dataframe_range_index_not_pseudo_header():
    """无表头表格：docling 返回 RangeIndex 列名，不得当伪表头，首行必须是数据。"""
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame([["x", "10"], ["y", "20"], ["z", "30"]])  # columns=RangeIndex
    item = _FakeTableItem(df=df)
    headers, rows = Parser._extract_table_grid(item, doc=None)
    assert headers == []
    assert rows == [["x", "10"], ["y", "20"], ["z", "30"]]


def test_table_grid_fallback_cells_with_header_flags():
    """DataFrame 导出失败 → table_cells 网格兜底，column_header 标记行作表头。"""
    cells = [
        _FakeCell("名称", 0, 0, column_header=True),
        _FakeCell("数量", 0, 1, column_header=True),
        _FakeCell("a", 1, 0),
        _FakeCell("1", 1, 1),
    ]
    item = _FakeTableItem(df=None, data=_FakeTableData(cells, 2, 2))
    headers, rows = Parser._extract_table_grid(item, doc=None)
    assert headers == ["名称", "数量"]
    assert rows == [["a", "1"]]


def test_table_grid_fallback_no_header_flags_keeps_first_row_as_data():
    """网格兜底：无 column_header 标记时首行是数据，不得当伪表头吃掉。"""
    cells = [
        _FakeCell("x", 0, 0),
        _FakeCell("10", 0, 1),
        _FakeCell("y", 1, 0),
        _FakeCell("20", 1, 1),
    ]
    item = _FakeTableItem(df=None, data=_FakeTableData(cells, 2, 2))
    headers, rows = Parser._extract_table_grid(item, doc=None)
    assert headers == []
    assert rows == [["x", "10"], ["y", "20"]]


def test_table_grid_empty_table_returns_empty():
    item = _FakeTableItem(df=None, data=_FakeTableData([], 0, 0))
    assert Parser._extract_table_grid(item, doc=None) == ([], [])


def test_table_grid_cleans_bold_marks_and_empty_headers():
    """docx 加粗单元格的 `**` 应清洗；表头全空则置空表头。"""
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame([["**a**", "1"]], columns=[" ", "**"])
    item = _FakeTableItem(df=df)
    headers, rows = Parser._extract_table_grid(item, doc=None)
    assert headers == []
    assert rows == [["a", "1"]]


# ---------------------------------------------------------------------------
# 跨页续表表头继承（_inherit_continuation_table_headers）
# ---------------------------------------------------------------------------


def _make_table_block(page, headers, rows):
    return Block(
        type=BlockType.TABLE,
        text="",
        page=page,
        table_data={"markdown": "", "headers": headers, "rows": rows},
    )


def test_continuation_table_inherits_headers():
    from app.core.parser import _inherit_continuation_table_headers

    blocks = [
        _make_table_block(1, ["序号", "名称"], [["1", "a"]]),
        _make_table_block(2, [], [["2", "b"]]),  # 续表无表头
    ]
    _inherit_continuation_table_headers(blocks)
    assert blocks[1].table_data["headers"] == ["序号", "名称"]
    assert blocks[1].table_data["rows"] == [["2", "b"]]


def test_continuation_table_demotes_misjudged_data_header():
    from app.core.parser import _inherit_continuation_table_headers

    blocks = [
        _make_table_block(54, ["序号", "名称"], [["16", "a"]]),
        # 续表首行数据被误判为表头（序号列纯数字特征）
        _make_table_block(55, ["17", "b"], [["18", "c"]]),
    ]
    _inherit_continuation_table_headers(blocks)
    assert blocks[1].table_data["headers"] == ["序号", "名称"]
    assert blocks[1].table_data["rows"] == [["17", "b"], ["18", "c"]]


def test_continuation_table_not_applied_when_pages_not_adjacent():
    from app.core.parser import _inherit_continuation_table_headers

    blocks = [
        _make_table_block(1, ["序号", "名称"], [["1", "a"]]),
        _make_table_block(5, [], [["9", "z"]]),  # 页码不相邻，不继承
    ]
    _inherit_continuation_table_headers(blocks)
    assert blocks[1].table_data["headers"] == []


def test_continuation_table_keeps_legit_new_header():
    from app.core.parser import _inherit_continuation_table_headers

    blocks = [
        _make_table_block(1, ["序号", "名称"], [["1", "a"]]),
        _make_table_block(2, ["编号", "标题"], [["1", "x"]]),  # 新表的真表头
    ]
    _inherit_continuation_table_headers(blocks)
    assert blocks[1].table_data["headers"] == ["编号", "标题"]