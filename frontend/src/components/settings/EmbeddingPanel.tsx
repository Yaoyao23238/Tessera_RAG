/**
 * Embedding 设置面板：本地模型（BGE-M3 默认 / Qwen3-Embedding 0.6B/4B）
 * 或 OpenAI 兼容 API；切换模型强确认（全量重建向量）；连通性测试。
 */

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { api, ApiError, friendlyError } from "../../lib/api";
import { useModels } from "../../hooks/useModels";
import { useTaskStore } from "../../stores/useTaskStore";
import { ModelRow } from "./ModelRow";
import { Button } from "../ui/button";
import { Input, FieldLabel } from "../ui/input";
import { ConfirmDialog } from "../common/States";
import { cn } from "../../lib/utils";
import type { EmbeddingConfig } from "../../types";

export function EmbeddingPanel({ config }: { config: EmbeddingConfig }) {
  const [draft, setDraft] = useState<EmbeddingConfig>(config);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [rebuildAffected, setRebuildAffected] = useState<number | null>(null);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);
  const { data: models } = useModels();

  // 外部配置变化（首次加载）时同步草稿
  useEffect(() => setDraft(config), [config]);

  const embeddingModels = (models?.items ?? []).filter((m) => m.kind === "embedding");

  const saveMutation = useMutation({
    mutationFn: ({ confirm }: { confirm?: boolean } = {}) =>
      api.put("/settings", {
        embedding: draft,
        _confirm_rebuild: confirm ?? false,
      }),
    onSuccess: () => {
      pushToast({ kind: "success", title: "Embedding 配置已保存" });
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      setConfirmOpen(false);
      setRebuildAffected(null);
    },
    onError: (err) => {
      // 后端返回「需确认重建向量」：带受影响文档数，弹二次确认
      if (err instanceof ApiError && err.code === 4100) {
        const affected = Number((err.data as { affected?: number } | null)?.affected ?? 0);
        if (affected > 0) {
          setRebuildAffected(affected);
          setConfirmOpen(true);
          return;
        }
      }
      pushToast({ kind: "error", title: "保存失败", message: friendlyError(err) });
    },
  });

  const testMutation = useMutation({
    mutationFn: () => api.post<{ ready: boolean; reason?: string }>(
      "/settings/embedding/test",
      draft
    ),
    onSuccess: (data) => {
      if (data.ready) {
        pushToast({ kind: "success", title: "Embedding 就绪" });
      } else {
        pushToast({ kind: "info", title: "未就绪", message: data.reason });
      }
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "测试失败", message: friendlyError(err) }),
  });

  function handleSave() {
    // 模型变化时先发请求：后端据「是否存在已入库文档」决定是否要求二次确认
    saveMutation.mutate({});
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500">
        默认本地 BGE-M3 零配置可用；切换模型将触发全量向量重建
      </p>

      {/* 提供方选择 */}
      <div className="grid grid-cols-2 gap-2">
        {(
          [
            { v: "local", label: "本地模型", desc: "离线运行，按需下载" },
            { v: "openai_compatible", label: "OpenAI 兼容 API", desc: "外部服务" },
          ] as const
        ).map((opt) => (
          <button
            key={opt.v}
            type="button"
            className={cn(
              "rounded-xl border p-3 text-left transition-colors",
              draft.providerType === opt.v
                ? "border-slate-900 bg-slate-50"
                : "border-gray-200 hover:border-gray-300"
            )}
            onClick={() =>
              setDraft({
                ...draft,
                providerType: opt.v,
                modelName:
                  opt.v === "local" ? "BAAI/bge-m3" : draft.modelName,
              })
            }
          >
            <p className="text-sm font-medium text-slate-800">{opt.label}</p>
            <p className="text-xs text-slate-400">{opt.desc}</p>
          </button>
        ))}
      </div>

      {draft.providerType === "local" ? (
        <div className="space-y-2">
          {embeddingModels.map((m) => (
            <ModelRow
              key={m.modelId}
              model={m}
              selected={draft.modelName === m.modelId}
              onSelect={() => setDraft({ ...draft, modelName: m.modelId })}
            />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <FieldLabel required>Base URL</FieldLabel>
            <Input
              placeholder="https://api.openai.com/v1"
              value={draft.baseUrl ?? ""}
              onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
            />
          </div>
          <div>
            <FieldLabel required>模型名</FieldLabel>
            <Input
              placeholder="text-embedding-3-small"
              value={draft.modelName}
              onChange={(e) => setDraft({ ...draft, modelName: e.target.value })}
            />
          </div>
          <div>
            <FieldLabel required>API Key</FieldLabel>
            <Input
              type="password"
              placeholder="sk-..."
              value={draft.apiKey ?? ""}
              onChange={(e) => setDraft({ ...draft, apiKey: e.target.value })}
            />
          </div>
          <div>
            <FieldLabel>向量维度</FieldLabel>
            <Input
              type="number"
              value={draft.dimensions}
              onChange={(e) =>
                setDraft({ ...draft, dimensions: Number(e.target.value) || 1024 })
              }
            />
          </div>
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={testMutation.isPending}
          onClick={() => testMutation.mutate()}
        >
          {testMutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          连通性测试
        </Button>
        <Button size="sm" onClick={handleSave} disabled={saveMutation.isPending}>
          {saveMutation.isPending ? "保存中…" : "保存"}
        </Button>
      </div>

      {/* 切换模型强确认（后端确认存在已入库文档后弹出） */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="切换 Embedding 模型？"
        description={
          <>
            新模型（{draft.modelName}）向量维度/语义空间与旧模型不同，将清空
            <span className="font-medium text-red-600">
              {rebuildAffected ?? 0} 个已入库文档
            </span>
            的向量，需重新入库，期间检索不可用。确认切换？
          </>
        }
        confirmText="确认清除并切换"
        danger
        loading={saveMutation.isPending}
        onConfirm={() => saveMutation.mutate({ confirm: true })}
      />
    </div>
  );
}
