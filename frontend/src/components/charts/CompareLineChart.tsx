/**
 * 多实验 Recall@k 对比折线图（X 轴 = k，Y 轴 = Recall）。
 */

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Experiment } from "../../types";

const PALETTE = ["#3B82F6", "#10B981", "#F59E0B", "#8B5CF6", "#EF4444", "#0EA5E9"];

export function CompareLineChart({ experiments }: { experiments: Experiment[] }) {
  // 每个 k 一个数据点，每条线一个实验
  const ks = [1, 5, 10] as const;
  const data = ks.map((k) => {
    const point: Record<string, number | string> = { k: `Recall@${k}` };
    for (const exp of experiments) {
      const v = k === 1 ? exp.recallAt1 : k === 5 ? exp.recallAt5 : exp.recallAt10;
      point[exp.name] = v == null ? NaN : Number(v.toFixed(4));
    }
    return point;
  });

  if (experiments.length === 0) return null;

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
        <XAxis dataKey="k" tick={{ fontSize: 11, fill: "#94A3B8" }} tickLine={false} />
        <YAxis
          domain={[0, 1]}
          tick={{ fontSize: 11, fill: "#94A3B8" }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v: number) => v.toFixed(1)}
        />
        <Tooltip
          formatter={(value: number, name: string) => [
            Number.isNaN(value) ? "未完成" : value.toFixed(3),
            name,
          ]}
          contentStyle={{ fontSize: 12, borderRadius: 8 }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {experiments.map((exp, i) => (
          <Line
            key={exp.id}
            type="monotone"
            dataKey={exp.name}
            stroke={PALETTE[i % PALETTE.length]}
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
