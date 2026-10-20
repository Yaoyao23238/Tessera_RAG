/**
 * 向量库设置面板：四档选择（sqlite_vec / lancedb / qdrant / pgvector）
 * + 连接信息（qdrant/pgvector 需要）。
 */

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, friendlyError } from "../../lib/api";
import { useTaskStore } from "../../stores/useTaskStore";
import { Button } from "../ui/button";
import { Input, FieldLabel } from "../ui/input";
import { VECTOR_BACKEND_META, type VectorBackend } from "../../lib/constants";
import { cn } from "../../lib/utils";
import type { VectorStoreConfig } from "../../types";

export function VectorStorePanel({ config }: { config: VectorStoreConfig }) {
  const [draft, setDraft] = useState<VectorStoreConfig>(config);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);

  useEffect(() => setDraft(config), [config]);

  const saveMutation = useMutation({
    mutationFn: () => api.put("/settings", { vectorStore: draft }),
    onSuccess: () => {
      pushToast({ kind: "success", title: "向量库配置已保存" });
      queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "保存失败", message: friendlyError(err) }),
  });

  const needConnection = draft.backend === "qdrant" || draft.backend === "pgvector";

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500">
        LanceDB 初始化失败时自动降级 sqlite-vec 并提示；切换后端后需重新入库。
        验证状态：LanceDB / sqlite-vec 已真实集成测试覆盖，Qdrant / pgvector
        仅代码路径实现、未做真实服务联调
      </p>

      <div className="grid grid-cols-2 gap-2">
        {(Object.keys(VECTOR_BACKEND_META) as VectorBackend[]).map((b) => (
          <button
            key={b}
            type="button"
            className={cn(
              "rounded-xl border p-3 text-left transition-colors",
              draft.backend === b
                ? "border-slate-900 bg-slate-50"
                : "border-gray-200 hover:border-gray-300"
            )}
            onClick={() => setDraft({ ...draft, backend: b })}
          >
            <p className="text-sm font-medium text-slate-800">
              {VECTOR_BACKEND_META[b].label}
              {b === "lancedb" && (
                <span className="ml-1.5 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-600">
                  默认
                </span>
              )}
            </p>
            <p className="text-xs text-slate-400">{VECTOR_BACKEND_META[b].desc}</p>
          </button>
        ))}
      </div>

      {needConnection && (
        <p className="flex items-start gap-1.5 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
          该档位未做真实服务联调（仅代码路径实现）。切换前建议先用测试数据
          验证检索质量；LanceDB / sqlite-vec 是已验证档位
        </p>
      )}

      {needConnection && (
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <FieldLabel required>连接地址</FieldLabel>
            <Input
              placeholder={
                draft.backend === "qdrant"
                  ? "http://localhost:6333"
                  : "postgresql://user:pass@localhost:5432/dbname"
              }
              value={draft.connectionUrl ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, connectionUrl: e.target.value })
              }
            />
          </div>
          <div>
            <FieldLabel>集合/表名</FieldLabel>
            <Input
              value={draft.collectionName}
              onChange={(e) =>
                setDraft({ ...draft, collectionName: e.target.value })
              }
            />
          </div>
        </div>
      )}

      <div className="flex justify-end">
        <Button
          size="sm"
          disabled={saveMutation.isPending || (needConnection && !draft.connectionUrl)}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? "保存中…" : "保存"}
        </Button>
      </div>
    </div>
  );
}
