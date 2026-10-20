/**
 * Reranker 设置面板：开关（bge-reranker-v2-m3）、top_n、
 * 模型下载（WS 进度）、加载测试。
 */

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { useModels } from "../../hooks/useModels";
import { useTaskStore } from "../../stores/useTaskStore";
import { ModelRow } from "./ModelRow";
import { Button } from "../ui/button";
import { Input, FieldLabel } from "../ui/input";
import { Switch } from "../ui/switch";
import type { RerankerConfig } from "../../types";

export function RerankPanel({ config }: { config: RerankerConfig }) {
  const [draft, setDraft] = useState<RerankerConfig>(config);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);
  const { data: models } = useModels();

  useEffect(() => setDraft(config), [config]);

  const rerankerModel = (models?.items ?? []).find((m) => m.kind === "reranker");

  const saveMutation = useMutation({
    mutationFn: (next: RerankerConfig) =>
      api.put("/settings", { reranker: next }),
    onSuccess: () => {
      pushToast({ kind: "success", title: "Reranker 配置已保存" });
      queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "保存失败", message: friendlyError(err) }),
  });

  const testMutation = useMutation({
    mutationFn: () =>
      api.post<{ ready: boolean; reason?: string }>("/settings/reranker/test", draft),
    onSuccess: (data) => {
      if (data.ready) {
        pushToast({ kind: "success", title: "Reranker 加载正常" });
      } else {
        pushToast({ kind: "info", title: "未就绪", message: data.reason ?? "模型未下载" });
      }
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "测试失败", message: friendlyError(err) }),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-4 py-3">
        <div>
          <p className="text-sm font-medium text-slate-800">启用 Rerank</p>
          <p className="text-xs text-slate-400">
            位于 RRF 融合之后、父块展开之前，仅精排 top N；关闭时检索链路不受影响
          </p>
          {draft.enabled && rerankerModel && !rerankerModel.downloaded && (
            <p className="mt-1 inline-flex items-center gap-1 rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">
              模型未下载：启用状态下检索会自动跳过精排（秒回，不等待下载超时），
              下载完成前建议关闭或保持开启均可
            </p>
          )}
        </div>
        <Switch
          checked={draft.enabled}
          onCheckedChange={(v) => {
            const next = { ...draft, enabled: v };
            setDraft(next);
            saveMutation.mutate(next);
          }}
        />
      </div>

      {rerankerModel && <ModelRow model={rerankerModel} />}

      <div className="flex flex-wrap gap-6">
        <div className="w-48">
          <FieldLabel>精排条数（top_n）</FieldLabel>
          <Input
            type="number"
            min={1}
            max={100}
            value={draft.topN}
            onChange={(e) =>
              setDraft({ ...draft, topN: Number(e.target.value) || 20 })
            }
          />
        </div>
        <div className="w-48">
          <FieldLabel>最低分阈值</FieldLabel>
          <Input
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={draft.minScore}
            onChange={(e) =>
              setDraft({ ...draft, minScore: Number(e.target.value) || 0 })
            }
          />
          <p className="mt-1 text-xs text-slate-400">
            0 = 不过滤；建议 0.1（校准于 bge-reranker-v2-m3 的双峰分布，换 reranker
            模型需重新校准）。只作用于精排分；RRF 分是排名倒数和、无绝对语义，不参与判断
          </p>
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={testMutation.isPending}
          onClick={() => testMutation.mutate()}
        >
          {testMutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          加载测试
        </Button>
        <Button
          size="sm"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate(draft)}
        >
          保存
        </Button>
      </div>
    </div>
  );
}
