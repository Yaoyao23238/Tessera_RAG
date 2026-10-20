"""LLM 清洗后处理器（解析预览页 LLM 辅助解析）。

档位模型（各自独立选择/勾选）：
- 文本清洗 text_scope：off / problem（仅问题块：超短/乱码/页眉页脚
  残留，省 token）/ full（全文清洗，修 OCR 错字、去页眉页脚、合并断行）
- 表格增强 clean_table：table_data 的 markdown 结构校正

设计要点：
- 块 1:1 映射：prompt 内以 <<<BLOCK N>>> 标记块边界，响应按标记切回，
  不做跨块重组（块数量不变，下游 chunk/embedding 缓存语义保持稳定）
- 单批失败保留该批原文并继续（尽力而为），failed_batches 记录失败数
- 真实 token 消耗经 LLMClient.chat_with_usage 累计，写入清洗缓存 meta
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.quality import find_problem_block_indexes
from app.core.tokenizer import count_tokens
from app.core.types import Block, BlockType

logger = logging.getLogger(__name__)

#: 清洗 prompt 版本号（写入清洗缓存 meta，prompt 调整后旧缓存可识别）
PROMPT_VERSION = "clean-v1"

_DEFAULT_MAX_BATCH_TOKENS = 3000

_BLOCK_MARK_RE = re.compile(r"<<<\s*BLOCK\s+(\d+)\s*>>>")

_TEXT_CLEAN_SYSTEM = """你是文档清洗助手。输入是从 PDF/OCR 解析出的若干文本块，每块以 <<<BLOCK N>>> 标记开头。

对每块依次执行：
1. 修复明显的 OCR 错字（形近字误识别等）
2. 删除页眉页脚残留（重复出现的书名/章节名/孤立页码行）
3. 合并块内被错误拆分的断行（中文句子中间不该有的换行）
4. 修正如「1,234」被拆行等格式问题

严格要求：
- 不改写、不总结、不翻译内容本身，不增删语义
- 原样保留每块的 <<<BLOCK N>>> 标记，逐块输出
- 无需修改的块原样输出
- 代码、公式、URL 保持原样"""

_TABLE_CLEAN_SYSTEM = """你是表格修复助手。输入是从 PDF 解析出的一个 Markdown 表格，可能存在列错位、单元格合并错误、表头识别异常。

校正表格结构，输出合法的 Markdown 表格。
严格要求：
- 不改动单元格内的数据内容，无法确定的内容保留原样
- 只输出表格本身，不要任何解释文字"""


class ChatWithUsage(Protocol):
    """清洗器依赖的 LLM 客户端协议（便于测试注入 fake）。"""

    def chat_with_usage(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> tuple[str, dict[str, int]]: ...


@dataclass
class CleanResult:
    """清洗结果：清洗后 Block 流 + 执行元信息。"""

    blocks: list[Block] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)  # 累计 token 消耗
    batches: int = 0  # 实际调用批数
    failed_batches: int = 0  # 失败批数（对应块保留原文）
    prompt_version: str = PROMPT_VERSION

    @property
    def meta(self) -> dict[str, Any]:
        """写入清洗缓存的元信息。"""
        return {
            "prompt_version": self.prompt_version,
            "usage": self.usage,
            "batches": self.batches,
            "failed_batches": self.failed_batches,
        }


class BlockCleaner:
    """LLM 清洗器：clean(blocks, text_scope=, clean_table=) -> CleanResult。"""

    def __init__(
        self,
        llm_client: ChatWithUsage,
        max_batch_tokens: int = _DEFAULT_MAX_BATCH_TOKENS,
    ) -> None:
        self._llm = llm_client
        self._max_batch_tokens = max_batch_tokens

    def clean(
        self,
        blocks: list[Block],
        *,
        text_scope: str = "full",
        clean_table: bool = False,
        progress_cb: Any = None,
    ) -> CleanResult:
        """对 Block 流执行所选档位清洗，返回新 Block 流（原列表不被修改）。

        Args:
            text_scope: off（不洗文本）/ problem（仅问题块：超短/乱码/
                页眉页脚残留，省 token）/ full（全文清洗）。
            clean_table: 是否表格增强（逐表调用校正 markdown）。
            progress_cb(done, total)：每批（成功或失败）完成后回调。
        """
        if text_scope not in ("off", "problem", "full"):
            raise ValueError(f"未知 text_scope：{text_scope}")
        result = CleanResult()
        # 复制块，避免污染调用方/缓存中的原始块
        cleaned = [Block.from_dict(b.to_dict()) for b in blocks]

        # 预计算总批数（文本批 + 表格逐块），供进度回调
        total = 0
        text_batches: list[list[tuple[int, Block]]] = []
        if text_scope != "off":
            problem_indexes = (
                find_problem_block_indexes(cleaned)
                if text_scope == "problem"
                else None
            )
            targets = [
                (idx, b)
                for idx, b in enumerate(cleaned)
                if b.type
                in (BlockType.HEADING, BlockType.PARAGRAPH, BlockType.LIST_ITEM)
                and b.text.strip()
                and (problem_indexes is None or idx in problem_indexes)
            ]
            text_batches = self._make_batches(targets)
            total += len(text_batches)
        if clean_table:
            total += sum(
                1
                for b in cleaned
                if b.type == BlockType.TABLE
                and ((b.table_data or {}).get("markdown") or b.text).strip()
            )

        def _tick() -> None:
            if progress_cb is not None:
                try:
                    progress_cb(result.batches, total)
                except Exception:  # noqa: BLE001 - 进度回调不影响主流程
                    logger.debug("清洗进度回调失败", exc_info=True)

        for batch in text_batches:
            self._run_text_batch(cleaned, batch, result)
            _tick()
        if clean_table:
            self._clean_table_blocks(cleaned, result, on_batch=_tick)
        result.blocks = cleaned
        return result

    # ------------------------------------------------------------------
    # 文本清洗：标记组批 → 逐块回写
    # ------------------------------------------------------------------

    def _make_batches(
        self, targets: list[tuple[int, Block]]
    ) -> list[list[tuple[int, Block]]]:
        """按 token 预算把目标块切成连续批。"""
        batches: list[list[tuple[int, Block]]] = []
        current: list[tuple[int, Block]] = []
        current_tokens = 0
        for idx, block in targets:
            tokens = count_tokens(block.text)
            # 单块超预算也单独成批（不截断，交给模型处理）
            if current and current_tokens + tokens > self._max_batch_tokens:
                batches.append(current)
                current, current_tokens = [], 0
            current.append((idx, block))
            current_tokens += tokens
        if current:
            batches.append(current)
        return batches

    def _run_text_batch(
        self,
        blocks: list[Block],
        batch: list[tuple[int, Block]],
        result: CleanResult,
    ) -> None:
        # 批内序号从 1 开始，标记只保证批内唯一
        numbered = list(enumerate(batch, start=1))
        payload = "\n\n".join(
            f"<<<BLOCK {n}>>>\n{block.text}" for n, (_, block) in numbered
        )
        result.batches += 1
        try:
            response, usage = self._llm.chat_with_usage(
                [
                    {"role": "system", "content": _TEXT_CLEAN_SYSTEM},
                    {"role": "user", "content": payload},
                ]
            )
            self._accumulate(result.usage, usage)
        except Exception:  # noqa: BLE001 - 单批失败保留原文继续
            result.failed_batches += 1
            logger.warning("文本清洗批次失败，保留原文", exc_info=True)
            return

        cleaned_map = self._parse_marked_blocks(response)
        for n, (idx, block) in numbered:
            new_text = cleaned_map.get(n)
            if new_text is None:
                # 标记缺失：回退原文，不算整批失败
                logger.warning("清洗响应缺少 BLOCK %d 标记，该块保留原文", n)
                continue
            blocks[idx].text = new_text

    @staticmethod
    def _parse_marked_blocks(response: str) -> dict[int, str]:
        """按 <<<BLOCK N>>> 标记切分响应为 {序号: 文本}。"""
        parts = _BLOCK_MARK_RE.split(response)
        # split 结果：[前缀, 编号1, 文本1, 编号2, 文本2, ...]
        cleaned: dict[int, str] = {}
        for i in range(1, len(parts) - 1, 2):
            try:
                number = int(parts[i])
            except ValueError:
                continue
            cleaned[number] = parts[i + 1].strip()
        return cleaned

    # ------------------------------------------------------------------
    # 表格增强：逐表调用，校正 markdown
    # ------------------------------------------------------------------

    def _clean_table_blocks(
        self, blocks: list[Block], result: CleanResult, on_batch: Any = None
    ) -> None:
        for block in blocks:
            if block.type != BlockType.TABLE:
                continue
            markdown = (block.table_data or {}).get("markdown") or block.text
            if not markdown.strip():
                continue
            result.batches += 1
            try:
                response, usage = self._llm.chat_with_usage(
                    [
                        {"role": "system", "content": _TABLE_CLEAN_SYSTEM},
                        {"role": "user", "content": markdown},
                    ]
                )
                self._accumulate(result.usage, usage)
            except Exception:  # noqa: BLE001
                result.failed_batches += 1
                logger.warning("表格增强调用失败，保留原表", exc_info=True)
                if on_batch is not None:
                    on_batch()
                continue
            new_markdown = self._extract_table_markdown(response)
            if not new_markdown:
                logger.warning("表格增强响应无有效表格，保留原表")
            else:
                if block.table_data is not None:
                    block.table_data["markdown"] = new_markdown
                block.text = new_markdown
            if on_batch is not None:
                on_batch()

    @staticmethod
    def _extract_table_markdown(response: str) -> str:
        """从响应中提取表格 markdown（剥离可能的代码围栏/解释文字）。"""
        text = response.strip()
        fence = re.search(r"```(?:markdown)?\s*\n(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        # 只保留含 | 的行，过滤模型可能附加的解释
        lines = [line for line in text.split("\n") if "|" in line]
        return "\n".join(lines).strip()

    # ------------------------------------------------------------------

    @staticmethod
    def _accumulate(total: dict[str, int], usage: dict[str, int]) -> None:
        for key, value in usage.items():
            total[key] = total.get(key, 0) + value
