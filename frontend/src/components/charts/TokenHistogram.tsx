/**
 * chunk token 分布直方图（Recharts）。
 * 当前版本深色柱；有上一版快照时叠加灰色对照层（X 轴按当前分桶）。
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChunkStats } from "../../types";

export function TokenHistogram({
  current,
  previous,
}: {
  current: ChunkStats;
  previous?: ChunkStats | null;
}) {
  // 以当前分桶为基准；上一版按桶中心映射到最近桶（分桶口径一致时天然对齐）
  const prevCounts = alignPrevious(current, previous ?? null);
  const data = current.histogram.map((bin, i) => ({
    label: `${Math.round(bin.binStart)}-${Math.round(bin.binEnd)}`,
    current: bin.count,
    previous: prevCounts[i] ?? null,
  }));

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: -16 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 10, fill: "#94A3B8" }}
          interval="preserveStartEnd"
          tickLine={false}
        />
        <YAxis tick={{ fontSize: 10, fill: "#94A3B8" }} tickLine={false} axisLine={false} />
        <Tooltip
          formatter={(value: number, name: string) => [
            value,
            name === "current" ? "当前版本" : "上一版",
          ]}
          labelFormatter={(l) => `token 区间 ${l}`}
          contentStyle={{ fontSize: 12, borderRadius: 8 }}
        />
        {/* 上一版灰色对照层（先渲染垫底） */}
        {previous && (
          <Bar dataKey="previous" fill="#CBD5E1" radius={[3, 3, 0, 0]} name="上一版" />
        )}
        <Bar dataKey="current" fill="#334155" radius={[3, 3, 0, 0]} name="当前版本" />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** 把上一版直方图按桶中心对齐到当前分桶（分桶数一致时即原样返回）。 */
function alignPrevious(current: ChunkStats, previous: ChunkStats | null): (number | null)[] {
  if (!previous) return current.histogram.map(() => null);
  if (
    previous.histogram.length === current.histogram.length &&
    previous.histogram.every(
      (b, i) =>
        Math.abs(b.binStart - current.histogram[i].binStart) < 1e-6 &&
        Math.abs(b.binEnd - current.histogram[i].binEnd) < 1e-6
    )
  ) {
    return previous.histogram.map((b) => b.count);
  }
  // 口径不同：按桶中心最近邻聚合
  return current.histogram.map((bin) => {
    const center = (bin.binStart + bin.binEnd) / 2;
    let best: number | null = null;
    let bestDist = Infinity;
    for (const pb of previous.histogram) {
      const pc = (pb.binStart + pb.binEnd) / 2;
      const d = Math.abs(pc - center);
      if (d < bestDist) {
        bestDist = d;
        best = pb.count;
      }
    }
    return best;
  });
}
