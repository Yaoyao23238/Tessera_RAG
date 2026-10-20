/**
 * 实验列表：配置摘要 + Recall@1/5/10 + MRR + 运行时间 + 状态/进度 +
 * 勾选（用于对比折线图）+ 删除确认。
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { useTaskStore } from "../../stores/useTaskStore";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { ProgressBar } from "../ui/skeleton";
import { ConfirmDialog } from "../common/States";
import { formatTime, cn } from "../../lib/utils";
import type { Experiment } from "../../types";

export function ExperimentTable({
  experiments,
  selectedIds,
  onSelectionChange,
}: {
  experiments: Experiment[];
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
}) {
  const [deleteTarget, setDeleteTarget] = useState<Experiment | null>(null);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);
  const expProgress = useTaskStore((s) => s.experimentProgress);

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      api.delete<{ droppedTable?: string }>(`/evaluation/experiments/${id}`),
    onSuccess: () => {
      pushToast({ kind: "success", title: "实验已删除", message: "独立索引表已级联清理" });
      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      setDeleteTarget(null);
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "删除失败", message: friendlyError(err) }),
  });

  function toggle(id: string) {
    onSelectionChange(
      selectedIds.includes(id)
        ? selectedIds.filter((x) => x !== id)
        : [...selectedIds, id]
    );
  }

  if (experiments.length === 0) {
    return (
      <p className="rounded-xl border border-gray-200 bg-white py-8 text-center text-xs text-slate-400">
        暂无实验：点击右上「运行实验」选择测试集与切分配置开始评测
      </p>
    );
  }

  return (
    <>
      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50 text-left text-xs text-slate-500">
              <th className="w-10 px-3 py-2.5" />
              <th className="px-3 py-2.5 font-medium">实验</th>
              <th className="px-3 py-2.5 font-medium">切分配置</th>
              <th className="w-20 px-3 py-2.5 text-right font-medium">R@1</th>
              <th className="w-20 px-3 py-2.5 text-right font-medium">R@5</th>
              <th className="w-20 px-3 py-2.5 text-right font-medium">R@10</th>
              <th className="w-20 px-3 py-2.5 text-right font-medium">MRR</th>
              <th className="w-32 px-3 py-2.5 font-medium">运行时间</th>
              <th className="w-14 px-3 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {experiments.map((exp) => {
              const running = exp.status === "running" || exp.status === "pending";
              const prog = expProgress[exp.id];
              const completed = exp.status === "completed";
              return (
                <tr
                  key={exp.id}
                  className={cn(
                    "border-b border-gray-50 last:border-0",
                    exp.status === "failed" && "bg-red-50/40"
                  )}
                >
                  <td className="px-3 py-2.5">
                    <Checkbox
                      checked={selectedIds.includes(exp.id)}
                      onCheckedChange={() => toggle(exp.id)}
                      disabled={!completed}
                    />
                  </td>
                  <td className="max-w-0 px-3 py-2.5">
                    <p className="truncate font-medium text-slate-800">{exp.name}</p>
                    <p className="text-[10px] text-slate-400">
                      {exp.embeddingModel || "-"} · {exp.documentIds.length} 篇文档
                    </p>
                    {running && prog && (
                      <div className="mt-1 flex items-center gap-2">
                        <ProgressBar
                          value={prog.total > 0 ? (prog.current / prog.total) * 100 : 0}
                          className="w-28"
                          barClassName="bg-blue-500"
                        />
                        <span className="text-[10px] text-slate-400">
                          {prog.current}/{prog.total}
                        </span>
                      </div>
                    )}
                    {exp.status === "failed" && (
                      <p className="mt-0.5 truncate text-xs text-red-500" title={exp.errorMessage ?? ""}>
                        {exp.errorMessage || "运行失败"}
                      </p>
                    )}
                  </td>
                  <td className="max-w-0 px-3 py-2.5">
                    <ConfigSummary config={exp.chunkingConfig} />
                  </td>
                  <MetricCell value={exp.recallAt1} />
                  <MetricCell value={exp.recallAt5} />
                  <MetricCell value={exp.recallAt10} />
                  <MetricCell value={exp.mrr} />
                  <td className="px-3 py-2.5 text-xs text-slate-400">
                    {formatTime(exp.completedAt ?? exp.createdAt)}
                  </td>
                  <td className="px-3 py-2.5">
                    <Button
                      variant="ghost"
                      size="icon"
                      title="删除实验"
                      className="h-7 w-7 text-slate-400 hover:text-red-500"
                      onClick={() => setDeleteTarget(exp)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(v) => !v && setDeleteTarget(null)}
        title={`删除实验「${deleteTarget?.name}」？`}
        description="将级联删除该实验的独立向量索引表，不可恢复。"
        confirmText="删除"
        danger
        loading={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
      />
    </>
  );
}

function MetricCell({ value }: { value: number | null }) {
  return (
    <td className="px-3 py-2.5 text-right text-xs tabular-nums text-slate-600">
      {value == null ? "-" : value.toFixed(3)}
    </td>
  );
}

/** 切分配置摘要（chunking_config JSON → 短文案）。 */
function ConfigSummary({ config }: { config: Record<string, unknown> }) {
  const size = config.chunkSize ?? config.chunk_size;
  const overlap = config.chunkOverlap ?? config.chunk_overlap;
  const tags: string[] = [];
  if (config.enableSemantic ?? config.enable_semantic) tags.push("语义");
  if (config.enableLlm ?? config.enable_llm) tags.push("LLM");
  if (config.enableParentChild ?? config.enable_parent_child) tags.push("父子");
  return (
    <p className="truncate text-xs text-slate-500" title={JSON.stringify(config)}>
      chunk {String(size ?? "-")} / ov {String(overlap ?? "-")}
      {tags.length > 0 ? ` / ${tags.join("+")}` : ""}
    </p>
  );
}
