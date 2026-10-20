/**
 * 质量评测页：测试集管理（上传/LLM 生成）+ 实验列表（指标/进度/删除）
 * + 多实验 Recall@k 对比折线图。
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FlaskConical, Play } from "lucide-react";
import { api, friendlyError } from "../lib/api";
import { TestSetPanel } from "../components/evaluation/TestSetPanel";
import { ExperimentTable } from "../components/evaluation/ExperimentTable";
import { RunExperimentDialog } from "../components/evaluation/RunExperimentDialog";
import { CompareLineChart } from "../components/charts/CompareLineChart";
import { ErrorState } from "../components/common/States";
import { ListSkeleton } from "../components/ui/skeleton";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import type { Experiment, QASet } from "../types";

export default function EvaluationPage() {
  const [runOpen, setRunOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const qasetsQuery = useQuery({
    queryKey: ["qasets"],
    queryFn: () => api.get<QASet[]>("/evaluation/qasets"),
  });
  const experimentsQuery = useQuery({
    queryKey: ["experiments"],
    queryFn: () => api.get<Experiment[]>("/evaluation/experiments"),
    refetchInterval: (query) => {
      // 有运行中的实验时轮询兜底
      const items = query.state.data ?? [];
      return items.some((e) => e.status === "running" || e.status === "pending")
        ? 3000
        : false;
    },
  });

  const selectedExperiments = useMemo(() => {
    const all = experimentsQuery.data ?? [];
    return selectedIds
      .map((id) => all.find((e) => e.id === id))
      .filter((e): e is Experiment => !!e && e.status === "completed");
  }, [experimentsQuery.data, selectedIds]);

  const loading = qasetsQuery.isLoading || experimentsQuery.isLoading;
  const error = qasetsQuery.error ?? experimentsQuery.error;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6 thin-scroll">
      <div className="flex items-center justify-between">
        <h1 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
          <FlaskConical className="h-5 w-5 text-slate-400" />
          质量评测
        </h1>
        <Button size="sm" onClick={() => setRunOpen(true)}>
          <Play className="h-3.5 w-3.5" /> 运行实验
        </Button>
      </div>

      {loading ? (
        <ListSkeleton rows={3} />
      ) : error ? (
        <ErrorState
          message={friendlyError(error)}
          onRetry={() => {
            qasetsQuery.refetch();
            experimentsQuery.refetch();
          }}
        />
      ) : (
        <>
          <TestSetPanel qasets={qasetsQuery.data ?? []} />

          <div>
            <h2 className="mb-2 text-sm font-medium text-slate-700">
              实验列表
              <span className="ml-2 text-xs font-normal text-slate-400">
                勾选已完成实验可在下方对比 Recall@k 曲线
              </span>
            </h2>
            <ExperimentTable
              experiments={experimentsQuery.data ?? []}
              selectedIds={selectedIds}
              onSelectionChange={setSelectedIds}
            />
          </div>

          {selectedExperiments.length > 0 && (
            <Card className="p-4">
              <h2 className="mb-2 text-sm font-medium text-slate-700">
                多实验对比（{selectedExperiments.length} 组）
              </h2>
              <CompareLineChart experiments={selectedExperiments} />
            </Card>
          )}
        </>
      )}

      <RunExperimentDialog
        qasets={qasetsQuery.data ?? []}
        open={runOpen}
        onOpenChange={setRunOpen}
      />
    </div>
  );
}
