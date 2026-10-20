"""token 计数工具。

统一口径使用 tiktoken（cl100k_base）。tiktoken 未安装时退化为启发式估算
（CJK 字符 1 token/字，其余约 4 字符/token），保证核心模块在无重依赖
环境下仍可运行与测试。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_encoding = None
_tiktoken_checked = False


def _get_encoding():
    """懒加载 tiktoken 编码器。"""
    global _encoding, _tiktoken_checked
    if not _tiktoken_checked:
        _tiktoken_checked = True
        try:
            import tiktoken

            _encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 - 任意失败都走估算回退
            logger.warning("tiktoken 不可用，token 计数退化为启发式估算")
            _encoding = None
    return _encoding


def count_tokens(text: str) -> int:
    """计算文本 token 数。"""
    if not text:
        return 0
    enc = _get_encoding()
    if enc is not None:
        return len(enc.encode(text))
    return _estimate_tokens(text)


def _estimate_tokens(text: str) -> int:
    """启发式估算：CJK 一字一 token，其余按 4 字符/token。"""
    cjk = 0
    other = 0
    for ch in text:
        if "一" <= ch <= "鿿" or "　" <= ch <= "〿" or "＀" <= ch <= "￯":
            cjk += 1
        elif not ch.isspace():
            other += 1
    return cjk + max(1, (other + 3) // 4)


def truncate_to_tokens(text: str, max_tokens: int, from_tail: bool = False) -> str:
    """按 token 数截断文本。

    Args:
        text: 原始文本。
        max_tokens: 允许的最大 token 数。
        from_tail: True 时保留末尾（用于 overlap 取前 chunk 末尾）。
    """
    if count_tokens(text) <= max_tokens:
        return text
    enc = _get_encoding()
    if enc is not None:
        tokens = enc.encode(text)
        tokens = tokens[-max_tokens:] if from_tail else tokens[:max_tokens]
        decoded = enc.decode(tokens)
        # 切点落在多字节 token（byte-fallback）中间时，decode 会把残缺
        # 字节序列还原为 U+FFFD；只可能出现在切点一侧的边缘，剥掉即可
        if from_tail:
            decoded = decoded.lstrip("\ufffd")
        else:
            decoded = decoded.rstrip("\ufffd")
        return decoded
    # 无 tiktoken 时按字符近似截断
    approx_chars = max_tokens * 2  # 中文场景 1 token ≈ 1~2 字符，取保守值
    return text[-approx_chars:] if from_tail else text[:approx_chars]
