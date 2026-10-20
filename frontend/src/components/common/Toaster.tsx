/**
 * 全局 toast 通知（右下角浮层，读取 useTaskStore.toasts）。
 */

import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useTaskStore } from "../../stores/useTaskStore";
import { ProgressBar } from "../ui/skeleton";
import { cn } from "../../lib/utils";

const ICONS = {
  info: <Info className="h-4 w-4 text-slate-500" />,
  success: <CheckCircle2 className="h-4 w-4 text-emerald-500" />,
  error: <XCircle className="h-4 w-4 text-red-500" />,
  progress: <Info className="h-4 w-4 animate-pulse text-blue-500" />,
};

export function Toaster() {
  const toasts = useTaskStore((s) => s.toasts);
  const dismiss = useTaskStore((s) => s.dismissToast);

  if (toasts.length === 0) return null;
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-80 flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cn(
            "pointer-events-auto rounded-xl border bg-white p-3 shadow-lg",
            t.kind === "error" ? "border-red-200" : "border-gray-200"
          )}
        >
          <div className="flex items-start gap-2">
            <div className="mt-0.5 shrink-0">{ICONS[t.kind]}</div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-slate-800">{t.title}</p>
              {t.message && (
                <p className="mt-0.5 break-all text-xs text-slate-500">{t.message}</p>
              )}
              {t.kind === "progress" && t.progress !== undefined && (
                <ProgressBar value={t.progress} className="mt-2" />
              )}
            </div>
            <button
              className="shrink-0 rounded p-0.5 text-slate-300 hover:text-slate-500"
              onClick={() => dismiss(t.id)}
              aria-label="关闭通知"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
