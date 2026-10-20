"""JSONL 导出测试：alpaca / Dify 模板 + 章节勾选过滤 + AI 思维链清洗。"""

from __future__ import annotations

import json

import pytest

from app.core.exporter import (
    Exporter,
    document_flow_order,
    match_sections,
    strip_cot_from_chunks,
)
from app.core.types import Chunk


def _chunks() -> list[Chunk]:
    return [
        Chunk(content="第一章内容", breadcrumb=["第一章"], heading_text="第一章"),
        Chunk(
            content="1.1节内容",
            breadcrumb=["第一章", "1.1 节"],
            heading_text="1.1 节",
        ),
        Chunk(content="第二章内容", breadcrumb=["第二章"], heading_text="第二章"),
    ]


class TestMatchSections:
    def test_no_sections_matches_all(self):
        assert match_sections(_chunks()[0], None)
        assert match_sections(_chunks()[0], [])

    def test_prefix_match(self):
        chunk = _chunks()[1]  # 第一章/1.1 节
        assert match_sections(chunk, ["第一章"])
        assert match_sections(chunk, ["第一章/1.1 节"])
        assert not match_sections(chunk, ["第二章"])


class TestExportJsonl:
    def test_alpaca_format(self, tmp_path):
        exporter = Exporter(tmp_path)
        path, count = exporter.export_jsonl(_chunks(), fmt="alpaca")
        assert count == 3
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        first = json.loads(lines[0])
        assert first["instruction"] == "第一章"
        assert first["input"] == ""
        assert first["output"] == "第一章内容"
        second = json.loads(lines[1])
        assert second["instruction"] == "第一章 > 1.1 节"

    def test_dify_format(self, tmp_path):
        exporter = Exporter(tmp_path)
        path, count = exporter.export_jsonl(_chunks(), fmt="dify")
        assert count == 3
        first = json.loads(path.read_text(encoding="utf-8").split("\n")[0])
        assert first["question"] == "第一章"
        assert first["answer"] == "第一章内容"

    def test_section_filter(self, tmp_path):
        exporter = Exporter(tmp_path)
        path, count = exporter.export_jsonl(
            _chunks(), fmt="alpaca", sections=["第一章"]
        )
        assert count == 2  # 第一章 + 第一章/1.1 节
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        contents = [json.loads(line)["output"] for line in lines]
        assert "第二章内容" not in contents

    def test_invalid_format(self, tmp_path):
        exporter = Exporter(tmp_path)
        with pytest.raises(ValueError):
            exporter.export_jsonl(_chunks(), fmt="sharegpt")

    def test_explicit_output_path(self, tmp_path):
        exporter = Exporter(tmp_path)
        target = tmp_path / "out" / "custom.jsonl"
        path, count = exporter.export_jsonl(_chunks(), fmt="alpaca", output_path=target)
        assert path == target
        assert target.exists()
        assert count == 3


class TestDocumentFlowOrder:
    """chunk_index 两套编号空间（顶层/子块）混排的顺序重建。"""

    def test_children_expand_at_parent_position(self):
        """子块在其父块位置处展开，独立块按顶层编号插位。"""
        # 顶层：独立块 idx=0、父块 idx=1、独立块 idx=2；父块下子块 idx=0/1
        solo_a = Chunk(id="sa", document_id="d", chunk_index=0, content="a")
        parent = Chunk(id="p", document_id="d", chunk_index=1, content="p")
        solo_b = Chunk(id="sb", document_id="d", chunk_index=2, content="b")
        child1 = Chunk(
            id="c1", document_id="d", chunk_index=0, parent_id="p", content="c1"
        )
        child2 = Chunk(
            id="c2", document_id="d", chunk_index=1, parent_id="p", content="c2"
        )
        # 故意乱序输入
        out = document_flow_order([child2, solo_b, parent, child1, solo_a])
        assert [c.id for c in out] == ["sa", "c1", "c2", "p", "sb"]

    def test_multi_doc_grouped(self):
        a1 = Chunk(id="a1", document_id="doc_a", chunk_index=1, content="")
        b1 = Chunk(id="b1", document_id="doc_b", chunk_index=0, content="")
        a0 = Chunk(id="a0", document_id="doc_a", chunk_index=0, content="")
        out = document_flow_order([a1, b1, a0])
        assert [c.id for c in out] == ["a0", "a1", "b1"]


class TestStripCot:
    """AI 对话存档思维链清洗（保守：宁可不删也不错删）。"""

    @staticmethod
    def _cot_chunk(content: str, doc: str = "d1", idx: int = 0) -> Chunk:
        return Chunk(id=f"{doc}-{idx}", document_id=doc, chunk_index=idx, content=content)

    def test_same_chunk_full_structure(self):
        """同块内完整结构：分隔 + 推理过程 + 思维链 + 回复 + 正文。"""
        content = (
            "前言\n"
            "==========\n"
            "【DeepSeek 推理过程】\n"
            "==========\n"
            "思考步骤一\n"
            "思考步骤二\n"
            "==========\n"
            "【DeepSeek 回复】\n"
            "==========\n"
            "正文回复"
        )
        out, touched = strip_cot_from_chunks([self._cot_chunk(content)])
        assert touched == 1
        assert len(out) == 1
        assert out[0].content == "前言\n正文回复"

    def test_across_chunks(self):
        """思维链横跨多块：起始块只留前缀，回复块只留回复后的正文。"""
        c1 = self._cot_chunk("前言\n【AI 推理过程】\n思考甲", idx=0)
        c2 = self._cot_chunk("思考乙\n【AI 回复】\n正文回复", idx=1)
        out, touched = strip_cot_from_chunks([c1, c2])
        assert touched == 2
        assert [c.content for c in out] == ["前言", "正文回复"]

    def test_pure_cot_chunk_removed(self):
        """整块皆思维链则整条剔除，不留空 output。"""
        c1 = self._cot_chunk("【AI 推理过程】\n思考甲", idx=0)
        c2 = self._cot_chunk("思考乙\n【AI 回复】\n正文", idx=1)
        out, _ = strip_cot_from_chunks([c1, c2])
        assert [c.content for c in out] == ["正文"]

    def test_normal_doc_untouched(self):
        """普通文档零影响：内容逐字节不变、不计入清洗数。"""
        c = self._cot_chunk("第一章 概述\n本文讨论参考文献著录规则。")
        out, touched = strip_cot_from_chunks([c])
        assert out[0] is c
        assert out[0].content == c.content
        assert touched == 0

    def test_orphan_end_marker_untouched(self):
        """孤立「回复」标记（未见过起始）与其分隔行均不动。"""
        content = "前言\n==========\n【AI 回复】\n==========\n正文"
        out, touched = strip_cot_from_chunks([self._cot_chunk(content)])
        assert out[0].content == content
        assert touched == 0

    def test_orphan_end_marker_consumed_when_doc_has_start(self):
        """文档出现过起始标记时，孤立回复标记消费其前内容（overlap 残余）。

        切分 overlap 前缀会让下一 chunk 重复上一 chunk 尾部已消费的
        「回复」标记；不消费就残留整段思维链。其前内容视为思维链区间。
        """
        c1 = self._cot_chunk("【AI 推理过程】\n思考甲", idx=0)
        c2 = self._cot_chunk(
            "思考甲残余（overlap 前缀）\n【AI 回复】\n正文回复", idx=1
        )
        out, _ = strip_cot_from_chunks([c1, c2])
        assert [c.content for c in out] == ["正文回复"]

    def test_doc_boundary_resets_state(self):
        """换文档重置状态：前一文档结尾停在推理中，不影响下一文档开头。"""
        c1 = self._cot_chunk("【AI 推理过程】\n思考甲", doc="d1")
        c2 = self._cot_chunk("另一篇文档的正文", doc="d2")
        out, _ = strip_cot_from_chunks([c1, c2])
        assert [c.content for c in out] == ["另一篇文档的正文"]

    def test_double_start_reconsumed(self):
        """推理中再遇起始标记：继续留在推理区间（不产出半截内容）。"""
        c1 = self._cot_chunk("【AI 推理过程】\n思考甲\n【AI 推理过程】\n思考乙", idx=0)
        c2 = self._cot_chunk("【AI 回复】\n正文", idx=1)
        out, _ = strip_cot_from_chunks([c1, c2])
        assert [c.content for c in out] == ["正文"]

    def test_export_with_strip_cot(self, tmp_path):
        """端到端：清洗后的 chunks 导出不含思维链。"""
        c = self._cot_chunk(
            "前言\n【AI 推理过程】\n思考\n【AI 回复】\n正文回复", idx=0
        )
        cleaned, _ = strip_cot_from_chunks([c])
        exporter = Exporter(tmp_path)
        path, count = exporter.export_jsonl(cleaned, fmt="alpaca")
        assert count == 1
        first = json.loads(path.read_text(encoding="utf-8").split("\n")[0])
        assert "推理过程" not in first["output"]
        assert "思考" not in first["output"]
        assert first["output"] == "前言\n正文回复"
