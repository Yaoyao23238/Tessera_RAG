"""LLM 辅助切分（阶段五，可选）。

对相邻 chunk 边界，取前 chunk 末尾 200 字 + 后 chunk 开头 200 字交 LLM
判断是否应合并；返回 JSON {"should_merge": bool, "confidence": float,
"summary": "..."}。should_merge=true 且合并不超限则合并；summary 追加到
chunk content 前增强检索。

LLM 调用任何失败都静默回退到规则结果（不阻断主流程）。
LLM 开启时不走切分缓存（输出有随机性，见缓存策略）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from app.core.tokenizer import count_tokens
from app.core.types import Chunk, ChunkingConfig, ChunkType

logger = logging.getLogger(__name__)

# 边界判断上下文窗口（前后各取字符数）
_BOUNDARY_CTX_CHARS = 200


class ChatClient(Protocol):
    """LLM 客户端协议（services.llm.LLMClient 实现）。"""

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str: ...


_MERGE_PROMPT = """你是文档切分质量审查器。下面是同一章节中相邻的两个文本片段的衔接处。

【前一片段末尾】
{prev_tail}

【后一片段开头】
{next_head}

请判断这两个片段是否应合并为一个 chunk（同一主题/语义连续应合并；话题转变保持分开）。
只返回 JSON，格式：{{"should_merge": true/false, "confidence": 0.0-1.0, "summary": "一句话概括该衔接处上下文（10-40字）"}}"""


def _extract_json(text: str) -> dict[str, Any] | None:
    """从 LLM 输出中提取 JSON 对象（容忍 markdown 代码块包裹）。"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def review_boundary(
    prev: Chunk, nxt: Chunk, llm_client: ChatClient
) -> tuple[bool, str, float]:
    """询问 LLM 是否应合并相邻 chunk。

    Returns:
        (should_merge, summary, confidence)；失败时返回 (False, "", 0.0)。
    """
    prompt = _MERGE_PROMPT.format(
        prev_tail=prev.content[-_BOUNDARY_CTX_CHARS:],
        next_head=nxt.content[:_BOUNDARY_CTX_CHARS],
    )
    try:
        raw = llm_client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
        )
    except Exception:  # noqa: BLE001 - LLM 失败静默回退
        logger.warning("LLM 边界审查调用失败，保留规则切分结果", exc_info=True)
        return False, "", 0.0
    data = _extract_json(raw or "")
    if not data:
        return False, "", 0.0
    should_merge = bool(data.get("should_merge", False))
    summary = str(data.get("summary", "") or "").strip()
    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return should_merge, summary, confidence


def _merge_pair(prev: Chunk, nxt: Chunk, summary: str) -> Chunk:
    """合并相邻 chunk；summary 作为前缀增强检索。"""
    content = prev.content.rstrip() + "\n\n" + nxt.content.lstrip()
    if summary:
        content = f"【摘要】{summary}\n" + content
    prev.content = content
    prev.end_page = nxt.end_page
    prev.token_count = count_tokens(content)
    prev.char_count = len(content)
    prev.strategy = "llm"
    prev.block_ids = [*prev.block_ids, *nxt.block_ids]
    return prev


def llm_review(
    chunks: list[Chunk],
    llm_client: ChatClient | None,
    config: ChunkingConfig,
) -> list[Chunk]:
    """阶段五：LLM 边界合并 + 摘要增强。

    仅处理同 section（breadcrumb 相同）相邻的 text 类 chunk；
    table/code/image 不参与。任何异常都返回原规则结果。
    config.llm_merge_threshold > 0 时仅合并置信度达标的边界（保守档）。
    """
    if llm_client is None or not chunks:
        return chunks
    merged: list[Chunk] = []
    i = 0
    limit = max(config.parent_chunk_size, config.child_chunk_size * 2)
    threshold = max(0.0, config.llm_merge_threshold)
    while i < len(chunks):
        current = chunks[i]
        if (
            i + 1 < len(chunks)
            and current.chunk_type == ChunkType.TEXT
            and chunks[i + 1].chunk_type == ChunkType.TEXT
            and current.breadcrumb == chunks[i + 1].breadcrumb
        ):
            nxt = chunks[i + 1]
            if count_tokens(current.content) + count_tokens(nxt.content) <= limit:
                should_merge, summary, confidence = review_boundary(
                    current, nxt, llm_client
                )
                if should_merge and confidence >= threshold:
                    merged.append(_merge_pair(current, nxt, summary))
                    i += 2
                    continue
        merged.append(current)
        i += 1
    return merged
