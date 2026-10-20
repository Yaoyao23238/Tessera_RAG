"""切分统计（直方图 / 类型占比 / 均值中位数）。

core 层只提供纯计算；快照落库（ChunkStatsSnapshot，每文档保留最近 5 版）
由 workers/api 层调用本模块结果完成。直方图默认 20 桶等宽（min~max token）。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.core.types import Chunk


@dataclass
class ChunkStats:
    """统计结果（字段与 ChunkStatsSnapshot ORM 对应）。"""

    histogram: list[dict] = field(default_factory=list)
    type_counts: dict[str, int] = field(default_factory=dict)
    mean_tokens: float = 0.0
    median_tokens: float = 0.0
    chunk_count: int = 0


def compute_stats(chunks: list[Chunk], bins: int = 20) -> ChunkStats:
    """计算 chunk 列表的统计快照。

    Args:
        chunks: 参与统计的 chunk（通常同时含父块与子块，由调用方决定口径）。
        bins: token 直方图桶数（默认 20，等宽）。
    """
    stats = ChunkStats(chunk_count=len(chunks))
    if not chunks:
        return stats

    token_counts = [c.token_count for c in chunks]
    stats.mean_tokens = float(statistics.fmean(token_counts))
    stats.median_tokens = float(statistics.median(token_counts))

    # 类型占比
    for chunk in chunks:
        key = chunk.chunk_type.value
        stats.type_counts[key] = stats.type_counts.get(key, 0) + 1

    # 等宽直方图
    lo = min(token_counts)
    hi = max(token_counts)
    if lo == hi:
        stats.histogram = [
            {"bin_start": float(lo), "bin_end": float(hi), "count": len(token_counts)}
        ]
        return stats

    width = (hi - lo) / bins
    bucket_counts = [0] * bins
    for value in token_counts:
        idx = int((value - lo) / width)
        idx = min(idx, bins - 1)  # 最大值落入最后一桶
        bucket_counts[idx] += 1
    stats.histogram = [
        {
            "bin_start": lo + i * width,
            "bin_end": lo + (i + 1) * width,
            "count": bucket_counts[i],
        }
        for i in range(bins)
    ]
    return stats


def compare_stats(current: ChunkStats, previous: ChunkStats | None) -> dict:
    """与上一版快照对比（均值/中位数/块数增量）。"""
    delta = {
        "mean_tokens_delta": 0.0,
        "median_tokens_delta": 0.0,
        "chunk_count_delta": 0,
    }
    if previous is not None:
        delta["mean_tokens_delta"] = round(
            current.mean_tokens - previous.mean_tokens, 4
        )
        delta["median_tokens_delta"] = round(
            current.median_tokens - previous.median_tokens, 4
        )
        delta["chunk_count_delta"] = current.chunk_count - previous.chunk_count
    return delta
