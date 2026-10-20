/**
 * JSONL 导出对话框：模板选择（alpaca / Dify）+ 章节勾选过滤（单文档时可用）。
 */

import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { useTaskStore } from "../../stores/useTaskStore";
import { useAllChunks } from "../../hooks/useChunks";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { FieldLabel } from "../ui/input";
import { cn } from "../../lib/utils";

type ExportFormat = "alpaca" | "dify";

export function ExportDialog({
  documentIds,
  open,
  onOpenChange,
}: {
  documentIds: string[];
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [format, setFormat] = useState<ExportFormat>("alpaca");
  const [excluded, setExcluded] = useState<string[]>([]);
  // 剥离 AI 对话存档思维链（默认开；导出物是训练数据，思维链混入代价高，可取消）
  const [stripCot, setStripCot] = useState(true);
  const [result, setResult] = useState<{
    filePath: string;
    lineCount: number;
    strippedChunks: number;
  } | null>(null);
  const [copied, setCopied] = useState(false);
  const pushToast = useTaskStore((s) => s.pushToast);

  // 单文档时可按章节过滤：加载全部 chunk 提取一级章节路径
  const singleDocId = documentIds.length === 1 ? documentIds[0] : null;
  const { data: chunks } = useAllChunks(open ? singleDocId : null);

  const sections = useMemo(() => {
    if (!chunks) return [];
    const set = new Set<string>();
    for (const c of chunks) {
      if (c.breadcrumb.length > 0) set.add(c.breadcrumb[0]);
    }
    return Array.from(set);
  }, [chunks]);

  const mutation = useMutation({
    mutationFn: () =>
      api.post<{ filePath: string; lineCount: number; strippedChunks: number }>(
        "/export/jsonl",
        {
          documentIds,
          format,
          stripCot,
          // 全部勾选时传 null（不过滤）
          sections:
            excluded.length === 0
              ? null
              : sections.filter((s) => !excluded.includes(s)),
        }
      ),
    onSuccess: (data) => {
      // 结果弹窗展示保存路径，不自动关闭
      setResult({
        filePath: data.filePath,
        lineCount: data.lineCount,
        strippedChunks: data.strippedChunks ?? 0,
      });
      onOpenChange(false);
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "导出失败", message: friendlyError(err) }),
  });

  function toggleSection(s: string) {
    setExcluded((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    );
  }

  async function copyPath() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.filePath);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      pushToast({ kind: "error", title: "复制失败", message: "请手动复制路径" });
    }
  }

  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogTitle>导出 JSONL（{documentIds.length} 个文档）</DialogTitle>

        <div className="mt-4">
          <FieldLabel required>导出模板</FieldLabel>
          <div className="grid grid-cols-2 gap-2">
            {(
              [
                { v: "alpaca", label: "alpaca", desc: "LLaMA-Factory" },
                { v: "dify", label: "Dify", desc: "Dify 数据集" },
              ] as const
            ).map((opt) => (
              <button
                key={opt.v}
                type="button"
                className={cn(
                  "rounded-lg border p-3 text-left transition-colors",
                  format === opt.v
                    ? "border-slate-900 bg-slate-50"
                    : "border-gray-200 hover:border-gray-300"
                )}
                onClick={() => setFormat(opt.v)}
              >
                <p className="text-sm font-medium text-slate-800">{opt.label}</p>
                <p className="text-xs text-slate-400">{opt.desc}</p>
              </button>
            ))}
          </div>
        </div>

        <div className="mt-4">
          <FieldLabel>内容处理</FieldLabel>
          <label className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
            <Checkbox
              checked={stripCot}
              onCheckedChange={(v) => setStripCot(v === true)}
            />
            <span>
              去除 AI 对话存档思维链（【xx 推理过程】…【xx 回复】），仅保留回复正文
              <span className="block text-xs text-slate-400">
                不含此类结构的文档不受影响
              </span>
            </span>
          </label>
        </div>

        {singleDocId && sections.length > 0 && (
          <div className="mt-4">
            <FieldLabel>章节过滤（取消勾选则不导出该章节）</FieldLabel>
            <div className="max-h-44 space-y-1.5 overflow-y-auto rounded-lg border border-gray-100 p-3 thin-scroll">
              {sections.map((s) => (
                <label key={s} className="flex cursor-pointer items-center gap-2 text-sm text-slate-700">
                  <Checkbox
                    checked={!excluded.includes(s)}
                    onCheckedChange={() => toggleSection(s)}
                  />
                  <span className="truncate">{s}</span>
                </label>
              ))}
            </div>
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending ? "导出中…" : "导出"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>

    {/* 导出结果：显示完整保存路径，可一键复制 */}
      <Dialog open={!!result} onOpenChange={(v) => !v && setResult(null)}>
        <DialogContent className="max-w-md">
          <DialogTitle>
            导出完成（{result?.lineCount ?? 0} 行）
            <span className="ml-2 text-xs font-normal text-slate-400">
              模板：{format}
            </span>
          </DialogTitle>
          <div className="mt-2">
            <p className="mb-1 text-xs text-slate-400">文件已保存到：</p>
            <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 p-2.5">
              <code className="min-w-0 flex-1 break-all font-mono text-xs text-slate-700">
                {result?.filePath}
              </code>
              <Button variant="outline" size="sm" onClick={copyPath} className="shrink-0">
                {copied ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-emerald-600" /> 已复制
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" /> 复制
                  </>
                )}
              </Button>
            </div>
            <p className="mt-2 text-xs text-slate-400">
              也可在文件管理器中打开 {format === "alpaca" ? "export_alpaca_*.jsonl" : "export_dify_*.jsonl"} 文件
            </p>
            {(result?.strippedChunks ?? 0) > 0 && (
              <p className="mt-1 text-xs text-emerald-600">
                已对 {result?.strippedChunks} 个块去除思维链
              </p>
            )}
          </div>
          <div className="mt-6 flex justify-end">
            <Button onClick={() => setResult(null)}>关闭</Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
