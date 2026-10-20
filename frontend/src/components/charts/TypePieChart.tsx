/**
 * chunk 类型占比环图（Recharts）。
 */

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { CHUNK_TYPE_META, type ChunkType } from "../../lib/constants";

export function TypePieChart({ typeCounts }: { typeCounts: Record<string, number> }) {
  const data = Object.entries(typeCounts)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({
      name: CHUNK_TYPE_META[k as ChunkType]?.label ?? k,
      value: v,
      color: CHUNK_TYPE_META[k as ChunkType]?.color ?? "#94A3B8",
    }));

  if (data.length === 0) {
    return <p className="py-8 text-center text-xs text-slate-400">暂无数据</p>;
  }

  return (
    <div className="flex items-center">
      <ResponsiveContainer width="55%" height={180}>
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius={44}
            outerRadius={70}
            paddingAngle={2}
            strokeWidth={0}
          >
            {data.map((d) => (
              <Cell key={d.name} fill={d.color} />
            ))}
          </Pie>
          <Tooltip
            formatter={(value: number, name: string) => [value, name]}
            contentStyle={{ fontSize: 12, borderRadius: 8 }}
          />
        </PieChart>
      </ResponsiveContainer>
      {/* 自绘图例 */}
      <ul className="flex-1 space-y-1.5">
        {data.map((d) => (
          <li key={d.name} className="flex items-center gap-2 text-xs text-slate-600">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: d.color }} />
            <span>{d.name}</span>
            <span className="ml-auto text-slate-400">{d.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
