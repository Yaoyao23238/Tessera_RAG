/**
 * LLM 设置面板：provider 列表（默认/启用/删除/测试）+ 添加弹窗入口。
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Zap } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { useTaskStore } from "../../stores/useTaskStore";
import { Button } from "../ui/button";
import { Switch } from "../ui/switch";
import { Badge } from "../ui/card";
import { ConfirmDialog, EmptyState } from "../common/States";
import { LLMProviderDialog } from "./LLMProviderDialog";
import type { LLMProvider } from "../../types";

export function LLMPanel({ providers }: { providers: LLMProvider[] }) {
  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<LLMProvider | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["settings"] });

  const updateMutation = useMutation({
    mutationFn: (p: LLMProvider) => api.put(`/settings/llm/${p.id}`, p),
    onSuccess: invalidate,
    onError: (err) =>
      pushToast({ kind: "error", title: "更新失败", message: friendlyError(err) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/settings/llm/${id}`),
    onSuccess: () => {
      pushToast({ kind: "success", title: "已删除" });
      invalidate();
      setDeleteTarget(null);
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "删除失败", message: friendlyError(err) }),
  });

  async function testProvider(p: LLMProvider) {
    setTestingId(p.id);
    try {
      await api.post("/settings/llm/test", p);
      pushToast({ kind: "success", title: `「${p.displayName || p.name}」连接正常` });
    } catch (err) {
      pushToast({ kind: "error", title: "连接失败", message: friendlyError(err) });
    } finally {
      setTestingId(null);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">
          配置 LLM 用于 LLM 辅助切分与测试集生成；密钥仅存本地 SQLite
        </p>
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="h-3.5 w-3.5" /> 添加模型
        </Button>
      </div>

      {providers.length === 0 ? (
        <EmptyState
          title="尚未配置 LLM"
          hint="LLM 为可选能力：无 LLM 时切分走规则/语义策略，评测测试集需手动上传"
          action={
            <Button size="sm" onClick={() => setAddOpen(true)}>
              <Plus className="h-3.5 w-3.5" /> 添加模型
            </Button>
          }
        />
      ) : (
        <ul className="space-y-2">
          {providers.map((p) => (
            <li
              key={p.id}
              className="flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-gray-100">
                <Zap
                  className={
                    p.providerType === "deepseek"
                      ? "h-4 w-4 text-blue-500"
                      : "h-4 w-4 text-slate-500"
                  }
                />
              </span>
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 text-sm font-medium text-slate-800">
                  {p.displayName || p.name}
                  {p.isDefault && <Badge>默认</Badge>}
                  <span className="text-xs font-normal text-slate-400">
                    {p.modelName}
                  </span>
                </p>
                <p className="truncate text-xs text-slate-400">{p.baseUrl}</p>
              </div>
              {!p.isDefault && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => updateMutation.mutate({ ...p, isDefault: true })}
                >
                  设为默认
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={testingId === p.id}
                onClick={() => testProvider(p)}
              >
                {testingId === p.id ? "测试中…" : "测试"}
              </Button>
              <div className="flex items-center gap-1.5 text-xs text-slate-500">
                启用
                <Switch
                  checked={p.enabled}
                  onCheckedChange={(v) => updateMutation.mutate({ ...p, enabled: v })}
                />
              </div>
              <Button
                variant="ghost"
                size="icon"
                title="删除"
                className="text-slate-400 hover:text-red-500"
                onClick={() => setDeleteTarget(p)}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <LLMProviderDialog open={addOpen} onOpenChange={setAddOpen} />

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(v) => !v && setDeleteTarget(null)}
        title={`删除「${deleteTarget?.displayName || deleteTarget?.name}」？`}
        confirmText="删除"
        danger
        loading={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
      />
    </div>
  );
}
