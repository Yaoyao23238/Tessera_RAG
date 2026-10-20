/**
 * 解析质量面板：统计条（块数/字符/页数/类型占比）+ 自动质量警告列表。
 * 每条警告可带一键操作（去清洗 / 重新解析）。
 */

import { AlertTriangle, Info, OctagonAlert } from "lucide-react";
import { BLOCK_TYPES, BLOCK_TYPE_META } from "../../lib/constants";
import { cn } from "../../lib/utils";
import type { ParseReport, QualityWarning } from "../../types";
import { Button } from "../ui/button";

const SEVERITY_META = {
  info: { icon: Info, classes: "bg-blue-50 text-blue-700 border-blue-100" },
  warning: {
    icon: AlertTriangle,
    classes: "bg-amber-50 text-amber-700 border-amber-100",
  },
  danger: {
    icon: OctagonAlert,
    classes: "bg-red-50 text-red-700 border-red-100",
  },
} as const;

export function QualityPanel({
  report,
  onAction,
}: {
  report: ParseReport;
  onAction: (action: string) => void;
}) {
  const totalTyped = BLOCK_TYPES.reduce(
    (sum, t) => sum + (report.typeCounts[t] ?? 0),
    0
  );

  return (
    <div className="space-y-3 rounded-xl border border-gray-200 bg-white px-4 py-3">
      {/* 统计条 */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <Stat label="总块数" value={String(report.totalBlocks)} />
        <Stat label="总字符" value={formatChars(report.totalChars)} />
        <Stat label="页数" value={String(report.pageCount)} />
        {/* 类型占比迷你条 */}
        <div className="flex min-w-40 flex-1 items-center gap-2">
          <div className="flex h-2 min-w-24 flex-1 overflow-hidden rounded-full bg-gray-100">
            {BLOCK_TYPES.map((t) => {
              const count = report.typeCounts[t] ?? 0;
              if (count === 0 || totalTyped === 0) return null;
              return (
                <div
                  key={t}
                  title={`${BLOCK_TYPE_META[t].label} ${count}`}
                  style={{
                    width: `${(count / totalTyped) * 100}%`,
                    backgroundColor: BLOCK_TYPE_META[t].color,
                  }}
                />
              );
            })}
          </div>
          <span className="whitespace-nowrap text-[10px] text-slate-400">
            {BLOCK_TYPES.filter((t) => (report.typeCounts[t] ?? 0) > 0)
              .map((t) => `${BLOCK_TYPE_META[t].label} ${report.typeCounts[t]}`)
              .join(" · ")}
          </span>
        </div>
      </div>

      {/* 警告列表 */}
      {report.warnings.length > 0 && (
        <div className="space-y-2 border-t border-gray-100 pt-2.5">
          {report.warnings.map((warning) => (
            <WarningRow key={warning.kind} warning={warning} onAction={onAction} />
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="text-xs text-slate-500">
      {label}
      <span className="ml-1.5 font-medium text-slate-800">{value}</span>
    </span>
  );
}

function WarningRow({
  warning,
  onAction,
}: {
  warning: QualityWarning;
  onAction: (action: string) => void;
}) {
  const meta = SEVERITY_META[warning.severity] ?? SEVERITY_META.info;
  const Icon = meta.icon;
  const examples = Array.isArray(warning.detail?.examples)
    ? (warning.detail.examples as string[])
    : [];

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2 text-xs",
        meta.classes
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="font-medium">{warning.message}</span>
      <span className="opacity-80">{warning.suggestion}</span>
      {examples.length > 0 && (
        <span
          className="w-full truncate pl-6 opacity-60"
          title={examples.join("\n")}
        >
          示例：{examples.join("、")}
        </span>
      )}
      {warning.action && (
        <Button
          size="sm"
          variant="outline"
          className="ml-auto h-6 bg-white/60 px-2 text-[11px]"
          onClick={() => onAction(warning.action)}
        >
          {warning.action === "open_clean_dialog" ? "LLM 清洗" : "重新解析"}
        </Button>
      )}
    </div>
  );
}

function formatChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
