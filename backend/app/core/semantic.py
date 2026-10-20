"""语义断崖切分（阶段 4b）。

流程：按句子切分 → 批量编码 → 相邻句余弦相似度 →
断崖点 sim[i] < max(μ - σ, semantic_threshold) 处断开 →
组内仍超限找次优断崖 → 无明显断崖返回 None（由调用方走递归兜底）。

编码函数以参数注入，便于测试与模型替换。
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable

from app.core.tokenizer import count_tokens

logger = logging.getLogger(__name__)

# 中文句末符 + 英文句末符
_SENTENCE_RE = re.compile(r"(?<=[。！？；.!?;])\s*|(?<=[。！？；])")

# 嵌入函数类型：texts -> 稠密向量列表
EmbedFn = Callable[[list[str]], list[list[float]]]


def split_sentences(text: str) -> list[str]:
    """按中英文句末符切句，保留非空句子。"""
    parts = _SENTENCE_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度（纯 Python 实现，避免 core 强依赖 numpy）。"""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def adjacent_similarities(vectors: list[list[float]]) -> list[float]:
    """相邻句子向量余弦相似度序列 sim[i] = cos(sent[i], sent[i+1])。"""
    return [
        cosine_similarity(vectors[i], vectors[i + 1])
        for i in range(len(vectors) - 1)
    ]


def find_cliff_points(sims: list[float], threshold: float) -> list[int]:
    """找断崖点：sim[i] < max(μ - σ, threshold)，返回断开的句子下标 i+1。

    返回的下标表示「在 sentences[i] 与 sentences[i+1] 之间断开」，
    按相似度升序排列（最优断崖在前）。
    """
    if not sims:
        return []
    mu = sum(sims) / len(sims)
    var = sum((s - mu) ** 2 for s in sims) / len(sims)
    sigma = math.sqrt(var)
    cutoff = max(mu - sigma, threshold)
    cliffs = [i for i, s in enumerate(sims) if s < cutoff]
    # 最优断崖（相似度最低）优先
    cliffs.sort(key=lambda i: sims[i])
    return cliffs


def _join_sentences(sentences: list[str]) -> str:
    """重组句子为文本（中文句末符后不加空格）。"""
    out: list[str] = []
    for sent in sentences:
        if out and re.search(r"[A-Za-z0-9]$", out[-1]) and re.match(
            r"^[A-Za-z0-9]", sent
        ):
            out.append(" ")
        out.append(sent)
    return "".join(out)


def semantic_split(
    text: str,
    embed_fn: EmbedFn | None,
    threshold: float,
    max_tokens: int,
) -> list[str] | None:
    """语义断崖切分。

    Args:
        text: 待拆分长文本。
        embed_fn: 句子编码函数；None 时直接放弃（走递归兜底）。
        threshold: semantic_threshold 配置。
        max_tokens: 每个分段的目标上限 token 数。

    Returns:
        分段列表；句子过少 / 无断崖可用时返回 None。
    """
    sentences = split_sentences(text)
    if len(sentences) < 3 or embed_fn is None:
        return None
    try:
        vectors = embed_fn(sentences)
    except Exception:  # noqa: BLE001 - 编码失败静默回退
        logger.warning("语义切分编码失败，回退递归切分", exc_info=True)
        return None
    if not vectors or len(vectors) != len(sentences):
        return None

    sims = adjacent_similarities(vectors)
    cliffs = find_cliff_points(sims, threshold)
    if not cliffs:
        return None

    # 先取最优断崖（相似度最低），组内仍超限再补次优断崖
    chosen: set[int] = {cliffs[0] + 1}  # sims[i] → 在 i 与 i+1 间断开
    remaining = [c + 1 for c in cliffs[1:]]

    def segment_ok(bounds: list[int]) -> bool:
        """检查当前断点集合下所有分段是否满足上限。"""
        prev = 0
        for b in sorted(bounds):
            if count_tokens(_join_sentences(sentences[prev:b])) > max_tokens:
                return False
            prev = b
        return count_tokens(_join_sentences(sentences[prev:])) <= max_tokens

    while remaining and not segment_ok(sorted(chosen)):
        chosen.add(remaining.pop(0))

    bounds = sorted(chosen)
    parts: list[str] = []
    prev = 0
    for b in bounds:
        seg = _join_sentences(sentences[prev:b])
        if seg.strip():
            parts.append(seg)
        prev = b
    tail = _join_sentences(sentences[prev:])
    if tail.strip():
        parts.append(tail)
    return parts if len(parts) > 1 else None
