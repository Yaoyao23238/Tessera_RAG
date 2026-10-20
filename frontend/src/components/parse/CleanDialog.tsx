/**
 * LLM 辅助解析弹窗：文本清洗三档（关/仅问题块/全文）+ 表格增强开关，
 * 实时 token 预估（按当前质量报告口径）。
 * 档位选择经 useLLMAssistStore 持久化，重开弹窗不重置。
 */

import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { useLLMAssistStore, type CleanScope } from "../../stores/useLLMAssistStore";
import { useTaskStore } from "../../stores/useTaskStore";
import { cn } from "../../lib/utils";
import type { ParseReport } from "../../types";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";
import { Switch } from "../ui/switch";

const SCOPE_OPTIONS: { value: CleanScope; label: string; desc: string }[] = [
  { value: "off", label: "关闭", desc: "不清洗文本" },
  { value: "problem", label: "仅问题块", desc: "只处理超短/乱码/页眉页脚残留块，省 token" },
  { value: "full", label: "全文清洗", desc: "全部文本块修错字、去页眉页脚、合并断行" },
];

export function CleanDialog({
  documentId,
  report,
  open,
  onOpenChange,
}: {
  documentId: string;
  /** 质量报告（取 token 预估）；可能尚未加载完成 */
  report: ParseReport | undefined;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const cleanScope = useLLMAssistStore((s) => s.cleanScope);
  const cleanTable = useLLMAssistStore((s) => s.cleanTable);
  const setCleanScope = useLLMAssistStore((s) => s.setCleanScope);
  const setCleanTable = useLLMAssistStore((s) => s.setCleanTable);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);

  const estimated = useMemo(() => {
    let total = 0;
    if (cleanScope === "problem") total += report?.estTokensProblem ?? 0;
    if (cleanScope === "full") total += report?.estTokensText ?? 0;
    if (cleanTable) total += report?.estTokensTable ?? 0;
    return total;
  }, [cleanScope, cleanTable, report]);

  const mutation = useMutation({
    mutationFn: () =>
      api.post(`/documents/${documentId}/clean`, {
        textScope: cleanScope,
        cleanTable,
      }),
    onSuccess: () => {
      pushToast({ kind: "info", title: "LLM 清洗任务已提交" });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      onOpenChange(false);
    },
    onError: (err) =>
      pushToast({
        kind: "error",
        title: "清洗启动失败",
        message: friendlyError(err),
      }),
  });

  const canSubmit = cleanScope !== "off" || cleanTable;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogTitle className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-violet-500" />
          LLM 辅助解析
        </DialogTitle>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          基于原始解析结果清洗，原始版始终保留，可随时切回。
          清洗完成后自动重新切分使结果生效。
        </p>

        {/* 文本清洗三档 */}
        <div className="mt-4">
          <p className="mb-1.5 text-xs font-medium text-slate-600">文本清洗</p>
          <div className="space-y-1.5">
            {SCOPE_OPTIONS.map((opt) => {
              const active = cleanScope === opt.value;
              const est =
                opt.value === "problem"
                  ? report?.estTokensProblem
                  : opt.value === "full"
                    ? report?.estTokensText
                    : 0;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setCleanScope(opt.value)}
                  className={cn(
                    "flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-colors",
                    active
                      ? "border-slate-900 bg-slate-50 ring-1 ring-slate-900"
                      : "border-gray-200 bg-white hover:bg-gray-50"
                  )}
                >
                  <span>
                    <span className="text-sm font-medium text-slate-800">
                      {opt.label}
                    </span>
                    <span className="mt-0.5 block text-xs text-slate-400">
                      {opt.desc}
                    </span>
                  </span>
                  {opt.value !== "off" && report && (
                    <span className="whitespace-nowrap text-[11px] text-slate-400">
                      约 {(est ?? 0).toLocaleString()}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* 表格增强开关 */}
        <label className="mt-3 flex items-start justify-between gap-3 rounded-lg border border-gray-200 px-3 py-2.5">
          <span>
            <span className="text-sm font-medium text-slate-800">表格增强</span>
            <span className="mt-0.5 block text-xs text-slate-400">
              校正表格 Markdown 结构（逐表调用，消耗较多 token）
            </span>
          </span>
          <Switch checked={cleanTable} onCheckedChange={setCleanTable} />
        </label>

        <div className="mt-4 flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-xs">
          <span className="text-slate-500">预估输入 token</span>
          <span className="font-medium text-slate-800">
            {report ? `约 ${estimated.toLocaleString()}` : "估算中…"}
          </span>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            size="sm"
            disabled={!canSubmit || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "提交中…" : "开始清洗"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
