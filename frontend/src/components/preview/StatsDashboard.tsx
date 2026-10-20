/**
 * 切分统计仪表盘：token 直方图（含上一版灰色对照）+ 类型占比环图
 * + 均值/中位数数字卡片。
 */

import { TokenHistogram } from "../charts/TokenHistogram";
import { TypePieChart } from "../charts/TypePieChart";
import { Card } from "../ui/card";
import type { ChunkStatsCompare } from "../../types";

export function StatsDashboard({ stats }: { stats: ChunkStatsCompare }) {
  const { current, previous } = stats;

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
      <Card className="p-4 lg:col-span-2">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-medium text-slate-700">
            token 分布
            <span className="ml-2 text-xs font-normal text-slate-400">
              v{current.version}
              {previous ? ` · 灰色为 v${previous.version} 对照` : ""}
            </span>
          </h3>
          <span className="text-xs text-slate-400">{current.chunkCount} chunks</span>
        </div>
        <TokenHistogram current={current} previous={previous} />
      </Card>

      <div className="flex flex-col gap-3">
        <Card className="p-4">
          <h3 className="mb-1 text-sm font-medium text-slate-700">类型占比</h3>
          <TypePieChart typeCounts={current.typeCounts} />
        </Card>
        <div className="grid grid-cols-2 gap-3">
          <StatCard
            label="平均 token"
            value={current.meanTokens}
            prev={previous?.meanTokens}
          />
          <StatCard
            label="中位 token"
            value={current.medianTokens}
            prev={previous?.medianTokens}
          />
        </div>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  prev,
}: {
  label: string;
  value: number;
  prev?: number | null;
}) {
  const delta = prev != null ? value - prev : null;
  return (
    <Card className="p-3">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900">
        {value.toFixed(0)}
      </p>
      {delta != null && delta !== 0 && (
        <p
          className={
            delta > 0 ? "text-xs text-emerald-600" : "text-xs text-red-500"
          }
        >
          {delta > 0 ? "+" : ""}
          {delta.toFixed(0)} vs 上一版
        </p>
      )}
    </Card>
  );
}
