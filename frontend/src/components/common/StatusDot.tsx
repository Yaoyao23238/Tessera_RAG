/**
 * 文档状态彩色圆点标签（12 态状态机）。
 */

import { DOC_STATUS_META, type DocStatus } from "../../lib/constants";
import { cn } from "../../lib/utils";

export function StatusDot({
  status,
  showLabel = true,
  className,
}: {
  status: string;
  showLabel?: boolean;
  className?: string;
}) {
  const meta = DOC_STATUS_META[status as DocStatus] ?? {
    label: status,
    color: "#94A3B8",
  };
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs text-slate-600", className)}>
      <span className="relative flex h-2 w-2">
        {meta.spin && (
          <span
            className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
            style={{ backgroundColor: meta.color }}
          />
        )}
        <span
          className="relative inline-flex h-2 w-2 rounded-full"
          style={{ backgroundColor: meta.color }}
        />
      </span>
      {showLabel && meta.label}
    </span>
  );
}
