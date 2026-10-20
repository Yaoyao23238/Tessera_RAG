"""递归字符切分（阶段 4c 兜底）。

按分隔符优先级递归拆分：
双换行 → 单换行 → 句末符（。！？；.!?;）→ 逗号（，、,）→ 空格 → 字符级。
"""

from __future__ import annotations

from app.core.tokenizer import count_tokens

# 分隔符优先级（空串表示字符级切分）
SEPARATORS: list[str] = ["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", "，", "、", ",", " ", ""]


def _split_keep_separator(text: str, sep: str) -> list[str]:
    """按分隔符切分并把分隔符保留在片段末尾（保证重组不丢字符）。"""
    if sep == "":
        return list(text)
    parts = text.split(sep)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i < len(parts) - 1:
            part = part + sep
        if part:
            out.append(part)
    return out


def _merge_pieces(pieces: list[str], max_tokens: int) -> list[str]:
    """把有序片段贪心合并为不超过 max_tokens 的块。"""
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        candidate = buf + piece
        if buf and count_tokens(candidate) > max_tokens:
            chunks.append(buf)
            buf = piece
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


def recursive_split(text: str, max_tokens: int, _sep_index: int = 0) -> list[str]:
    """递归兜底拆分。

    Args:
        text: 待拆分文本。
        max_tokens: 每块 token 上限。
        _sep_index: 内部递归游标（当前分隔符优先级下标）。
    """
    text = text.strip()
    if not text:
        return []
    if count_tokens(text) <= max_tokens:
        return [text]

    for i in range(_sep_index, len(SEPARATORS)):
        sep = SEPARATORS[i]
        if sep and sep not in text:
            continue
        pieces = _split_keep_separator(text, sep)
        if len(pieces) <= 1:
            continue
        # 对仍超限的片段用下一级分隔符递归
        refined: list[str] = []
        for piece in pieces:
            if count_tokens(piece) > max_tokens:
                refined.extend(recursive_split(piece, max_tokens, i + 1))
            else:
                refined.append(piece)
        return _merge_pieces(refined, max_tokens)

    # 理论上不会到这里（空串分隔符总能切），防御性返回
    return [text]
