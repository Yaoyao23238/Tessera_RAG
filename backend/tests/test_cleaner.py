"""core.cleaner LLM 清洗测试：两档开关 / 标记解析 / 失败回退 / usage 累计。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from conftest import make_heading, make_paragraph, make_table  # noqa: E402

from app.core.cleaner import PROMPT_VERSION, BlockCleaner  # noqa: E402


class FakeUsageLLM:
    """带 usage 的伪造 LLM：队列式返回 (响应文本, usage)。"""

    def __init__(self, responses: list[tuple[str, dict]] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[list[dict[str, str]]] = []
        self.fail_next = False

    def chat_with_usage(self, messages, **kwargs):
        self.calls.append(messages)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("模拟 LLM 故障")
        if self.responses:
            return self.responses.pop(0)
        return "", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


class TestTextClean:
    def test_blocks_rewritten_and_original_untouched(self):
        blocks = [make_paragraph("原始文本一"), make_paragraph("原始文本二")]
        llm = FakeUsageLLM(
            [("<<<BLOCK 1>>>\n清洗文本一\n\n<<<BLOCK 2>>>\n清洗文本二",
              {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})]
        )
        result = BlockCleaner(llm).clean(blocks, text_scope="full")
        assert result.blocks[0].text == "清洗文本一"
        assert result.blocks[1].text == "清洗文本二"
        # 原始块不被修改（保护调用方缓存）
        assert blocks[0].text == "原始文本一"
        assert result.usage["total_tokens"] == 30
        assert result.batches == 1
        assert result.failed_batches == 0
        assert result.prompt_version == PROMPT_VERSION

    def test_missing_mark_falls_back_to_original(self):
        blocks = [make_paragraph("文本一"), make_paragraph("文本二")]
        # 响应只含 BLOCK 1，BLOCK 2 标记缺失
        llm = FakeUsageLLM([("<<<BLOCK 1>>>\n清洗一", {})])
        result = BlockCleaner(llm).clean(blocks, text_scope="full")
        assert result.blocks[0].text == "清洗一"
        assert result.blocks[1].text == "文本二"  # 回退原文

    def test_batch_failure_keeps_original(self):
        blocks = [make_paragraph("文本一")]
        llm = FakeUsageLLM()
        llm.fail_next = True
        result = BlockCleaner(llm).clean(blocks, text_scope="full")
        assert result.failed_batches == 1
        assert result.blocks[0].text == "文本一"

    def test_batch_split_by_token_budget(self):
        # 每块约 30+ token，预算 60 → 多块拆批
        blocks = [
            make_paragraph("这是一个长度适中的段落，用来占用批次额度。" * 2)
            for _ in range(6)
        ]
        llm = FakeUsageLLM()
        result = BlockCleaner(llm, max_batch_tokens=60).clean(
            blocks, text_scope="full"
        )
        assert result.batches >= 2

    def test_progress_callback(self):
        blocks = [make_paragraph("文本一"), make_paragraph("文本二")]
        llm = FakeUsageLLM()
        calls: list[tuple[int, int]] = []
        BlockCleaner(llm).clean(
            blocks, text_scope="full", progress_cb=lambda d, t: calls.append((d, t))
        )
        assert calls and calls[-1][0] == calls[-1][1] >= 1

    def test_headings_and_list_items_included(self):
        blocks = [make_heading("标题"), make_paragraph("正文")]
        llm = FakeUsageLLM(
            [("<<<BLOCK 1>>>\n新标题\n\n<<<BLOCK 2>>>\n新正文", {})]
        )
        result = BlockCleaner(llm).clean(blocks, text_scope="full")
        assert result.blocks[0].text == "新标题"
        assert result.blocks[1].text == "新正文"


class TestTableClean:
    def test_markdown_replaced(self):
        table = make_table("| a | b |\n|---|---|\n| 1 | 2 |")
        fixed = "| 名称 | 数量 |\n|---|---|\n| a | 2 |"
        llm = FakeUsageLLM(
            [(f"```markdown\n{fixed}\n```", {"total_tokens": 25})]
        )
        result = BlockCleaner(llm).clean([table], text_scope="off", clean_table=True)
        assert result.blocks[0].table_data["markdown"] == fixed
        assert result.blocks[0].text == fixed
        assert result.usage["total_tokens"] == 25

    def test_explanatory_text_stripped(self):
        table = make_table()
        llm = FakeUsageLLM(
            [("已为您修复表格：\n| a | b |\n|---|---|\n| 1 | 2 |\n希望有帮助", {})]
        )
        result = BlockCleaner(llm).clean([table], text_scope="off", clean_table=True)
        assert "希望有帮助" not in result.blocks[0].text
        assert result.blocks[0].text.startswith("| a | b |")

    def test_table_untouched_when_disabled(self):
        table = make_table()
        original = table.table_data["markdown"]
        llm = FakeUsageLLM()
        result = BlockCleaner(llm).clean([table], text_scope="full", clean_table=False)
        assert result.blocks[0].table_data["markdown"] == original
        assert len(llm.calls) == 0  # 纯文本档不处理表格

    def test_table_failure_keeps_original(self):
        table = make_table()
        original = table.table_data["markdown"]
        llm = FakeUsageLLM()
        llm.fail_next = True
        result = BlockCleaner(llm).clean(
            [table], text_scope="off", clean_table=True
        )
        assert result.failed_batches == 1
        assert result.blocks[0].table_data["markdown"] == original


class TestSwitches:
    def test_clean_text_disabled(self):
        blocks = [make_paragraph("正文")]
        llm = FakeUsageLLM()
        result = BlockCleaner(llm).clean(blocks, text_scope="off", clean_table=False)
        assert result.blocks[0].text == "正文"
        assert result.batches == 0
        assert len(llm.calls) == 0

    def test_problem_scope_only_cleans_problem_blocks(self):
        """problem 档：只有问题块（超短/乱码）进入清洗，正常块不调用 LLM。"""
        normal = "这是一段完全正常、长度足够、没有任何问题的正文内容，不需要清洗处理。"
        blocks = [
            make_paragraph(normal, page=1),
            make_paragraph("碎块", page=1),  # 超短块 → 问题块
            make_paragraph(normal, page=2),
        ]
        llm = FakeUsageLLM([("<<<BLOCK 1>>>\n修复后的碎块文本", {})])
        result = BlockCleaner(llm).clean(blocks, text_scope="problem")
        # 只有 1 个块进入清洗（1 次调用）
        assert len(llm.calls) == 1
        assert result.blocks[0].text == normal  # 正常块不动
        assert result.blocks[1].text == "修复后的碎块文本"
        assert result.blocks[2].text == normal

    def test_invalid_scope_rejected(self):
        llm = FakeUsageLLM()
        try:
            BlockCleaner(llm).clean([make_paragraph("x")], text_scope="everything")
            raise AssertionError("应抛 ValueError")
        except ValueError:
            pass

    def test_meta_structure(self):
        blocks = [make_paragraph("正文")]
        llm = FakeUsageLLM()
        result = BlockCleaner(llm).clean(blocks, text_scope="full")
        meta = result.meta
        assert meta["prompt_version"] == PROMPT_VERSION
        assert "usage" in meta and "batches" in meta and "failed_batches" in meta
