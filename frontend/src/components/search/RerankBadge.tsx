/**
 * Rerank 开关状态徽标：读取全局设置展示当前 Rerank 状态；
 * 支持单次检索临时覆盖（use_rerank）。
 */

import { Sparkles } from "lucide-react";
import { Switch } from "../ui/switch";
import { cn } from "../../lib/utils";

export function RerankBadge({
  globalEnabled,
  override,
  onOverrideChange,
}: {
  /** 全局设置中的 reranker.enabled */
  globalEnabled: boolean;
  /** 单次覆盖：null=跟随全局 */
  override: boolean | null;
  onOverrideChange: (v: boolean | null) => void;
}) {
  const effective = override ?? globalEnabled;
  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs",
        effective
          ? "border-purple-200 bg-purple-50 text-purple-700"
          : "border-gray-200 bg-white text-slate-400"
      )}
    >
      <Sparkles className="h-3.5 w-3.5" />
      Rerank {effective ? "开" : "关"}
      {override !== null && (
        <span className="text-[10px] opacity-70">（本次覆盖）</span>
      )}
      <Switch
        checked={effective}
        onCheckedChange={(v) =>
          onOverrideChange(v === globalEnabled ? null : v)
        }
        className="scale-75 data-[state=checked]:bg-purple-600"
      />
    </div>
  );
}
