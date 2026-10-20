/**
 * 设置页：LLM / Embedding / Rerank / 向量库 / 通用（应用配置 + 缓存管理）。
 * 面板组件在 components/settings/，本页负责取数与组装。
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CloudDownload, HardDrive, Settings as SettingsIcon, Trash2 } from "lucide-react";
import { api, friendlyError } from "../lib/api";
import { useTaskStore } from "../stores/useTaskStore";
import { LLMPanel } from "../components/settings/LLMPanel";
import { EmbeddingPanel } from "../components/settings/EmbeddingPanel";
import { RerankPanel } from "../components/settings/RerankPanel";
import { VectorStorePanel } from "../components/settings/VectorStorePanel";
import { ModelRow } from "../components/settings/ModelRow";
import { useModels } from "../hooks/useModels";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Card } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Input, FieldLabel } from "../components/ui/input";
import { ListSkeleton } from "../components/ui/skeleton";
import { ConfirmDialog, ErrorState } from "../components/common/States";
import { cn, formatBytes } from "../lib/utils";
import type { AppConfig, CacheStatus, Settings } from "../types";

export default function SettingsPage() {
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<Settings>("/settings"),
  });

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6 thin-scroll">
      <h1 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
        <SettingsIcon className="h-5 w-5 text-slate-400" />
        设置
      </h1>

      {settingsQuery.isLoading ? (
        <ListSkeleton rows={4} />
      ) : settingsQuery.error ? (
        <ErrorState
          message={friendlyError(settingsQuery.error)}
          onRetry={() => settingsQuery.refetch()}
        />
      ) : settingsQuery.data ? (
        <Tabs defaultValue="llm">
          <TabsList>
            <TabsTrigger value="llm">LLM</TabsTrigger>
            <TabsTrigger value="embedding">Embedding</TabsTrigger>
            <TabsTrigger value="rerank">Rerank</TabsTrigger>
            <TabsTrigger value="vectorstore">向量库</TabsTrigger>
            <TabsTrigger value="general">通用</TabsTrigger>
          </TabsList>

          <TabsContent value="llm">
            <Card className="p-4">
              <LLMPanel providers={settingsQuery.data.llmProviders} />
            </Card>
          </TabsContent>
          <TabsContent value="embedding">
            <Card className="p-4">
              <EmbeddingPanel config={settingsQuery.data.embedding} />
            </Card>
          </TabsContent>
          <TabsContent value="rerank">
            <Card className="p-4">
              <RerankPanel config={settingsQuery.data.reranker} />
            </Card>
          </TabsContent>
          <TabsContent value="vectorstore">
            <Card className="p-4">
              <VectorStorePanel config={settingsQuery.data.vectorStore} />
            </Card>
          </TabsContent>
          <TabsContent value="general" className="space-y-4">
            <DownloadSourcePanel />
            <DoclingPanel />
            <GeneralPanel config={settingsQuery.data.app} />
            <CachePanel />
          </TabsContent>
        </Tabs>
      ) : null}
    </div>
  );
}

/** 下载源：huggingface 直连 / hf-mirror / ModelScope，对 Embedding/Rerank/Docling 统一生效。 */
const DOWNLOAD_SOURCES: Array<{ value: string; label: string; desc: string }> = [
  {
    value: "hf_mirror",
    label: "hf-mirror（推荐）",
    desc: "huggingface 国内镜像，本机实测可达；Embedding/Rerank/Docling 模型统一走镜像",
  },
  {
    value: "modelscope",
    label: "ModelScope（推荐）",
    desc: "阿里魔搭国内源，速度快；仅适用于 Embedding/Rerank 模型（Docling 包无镜像，固定走 hf-mirror）",
  },
  {
    value: "huggingface",
    label: "HuggingFace 直连",
    desc: "官方源，国内网络一般无法连接（会超时），有代理时可选",
  },
];

function DownloadSourcePanel() {
  const { data: models } = useModels();
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<Settings>("/settings"),
  });
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);
  const current = (settingsQuery.data?.download as { source?: string } | undefined)?.source ?? "hf_mirror";
  const [draft, setDraft] = useState<string>(current);

  useEffect(() => setDraft(current), [current]);

  const saveMutation = useMutation({
    mutationFn: () => api.put("/settings", { download: { source: draft } }),
    onSuccess: () => {
      pushToast({ kind: "success", title: "下载源已保存" });
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "保存失败", message: friendlyError(err) }),
  });

  return (
    <Card className="space-y-3 p-4">
      <h2 className="flex items-center gap-2 text-sm font-medium text-slate-700">
        <CloudDownload className="h-4 w-4 text-slate-400" />
        模型下载源
      </h2>
      <div className="grid gap-2 md:grid-cols-3">
        {DOWNLOAD_SOURCES.map((opt) => (
          <button
            key={opt.value}
            type="button"
            className={cn(
              "rounded-lg border p-3 text-left transition-colors",
              draft === opt.value
                ? "border-slate-900 bg-slate-50"
                : "border-gray-200 hover:border-gray-300"
            )}
            onClick={() => setDraft(opt.value)}
          >
            <p className="text-sm font-medium text-slate-800">{opt.label}</p>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">{opt.desc}</p>
          </button>
        ))}
      </div>
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-400">
          当前生效：{DOWNLOAD_SOURCES.find((s) => s.value === current)?.label}
          {models?.downloadSource ? `（后端 ${models.downloadSource}）` : ""}
        </p>
        <Button size="sm" disabled={saveMutation.isPending || draft === current} onClick={() => saveMutation.mutate()}>
          {saveMutation.isPending ? "保存中…" : "保存"}
        </Button>
      </div>
    </Card>
  );
}

/** Docling 模型包：PDF 解析必需（layout/表格/OCR 模型），经 hf-mirror 下载。 */
function DoclingPanel() {
  const { data: models } = useModels();
  const doclingModel = (models?.items ?? []).find((m) => m.kind === "docling");
  if (!doclingModel) return null;
  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-medium text-slate-700">
          <HardDrive className="h-4 w-4 text-slate-400" />
          Docling 解析模型包
        </h2>
        {doclingModel.downloaded && (
          <span className="text-xs text-emerald-600">已就绪，PDF 解析不再联网</span>
        )}
      </div>
      <ModelRow model={doclingModel} />
      <p className="text-xs text-slate-400">
        PDF 解析（含扫描件 OCR）首次运行需下载 Docling 模型包，约 2~6GB；下载后完全离线可用。
        模型包固定经 hf-mirror 下载（ModelScope 无此模型镜像）。
      </p>
    </Card>
  );
}

/** 应用配置：数据目录（只读）+ 上传大小上限 + 缓存容量上限。 */
function GeneralPanel({ config }: { config: AppConfig }) {
  const [draft, setDraft] = useState<AppConfig>(config);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);

  useEffect(() => setDraft(config), [config]);

  const saveMutation = useMutation({
    mutationFn: () => api.put("/settings", { app: draft }),
    onSuccess: () => {
      pushToast({ kind: "success", title: "应用配置已保存" });
      queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "保存失败", message: friendlyError(err) }),
  });

  return (
    <Card className="space-y-4 p-4">
      <h2 className="flex items-center gap-2 text-sm font-medium text-slate-700">
        <HardDrive className="h-4 w-4 text-slate-400" />
        应用配置
      </h2>

      <div>
        <FieldLabel>数据目录</FieldLabel>
        <Input value={draft.dataDir} readOnly disabled />
        <p className="mt-1 text-xs text-slate-400">
          文档、索引、模型与缓存均存于此目录；如需迁移请改配置文件后重启后端
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <FieldLabel>单文件大小上限（MB）</FieldLabel>
          <Input
            type="number"
            min={1}
            value={draft.maxFileSizeMb}
            onChange={(e) =>
              setDraft({ ...draft, maxFileSizeMb: Number(e.target.value) || 100 })
            }
          />
        </div>
        <div>
          <FieldLabel>缓存容量上限（GB）</FieldLabel>
          <Input
            type="number"
            min={1}
            value={draft.cacheSizeLimitGb}
            onChange={(e) =>
              setDraft({ ...draft, cacheSizeLimitGb: Number(e.target.value) || 10 })
            }
          />
        </div>
      </div>

      <div className="flex justify-end">
        <Button
          size="sm"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? "保存中…" : "保存"}
        </Button>
      </div>
    </Card>
  );
}

/** 缓存管理：占用统计 + 按类型/全部清理。 */
function CachePanel() {
  const [clearKind, setClearKind] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);

  const cacheQuery = useQuery({
    queryKey: ["settings", "cache"],
    queryFn: () => api.get<CacheStatus>("/settings/cache"),
  });

  const clearMutation = useMutation({
    mutationFn: (kind: string) =>
      api.delete<{ freedBytes: number }>(`/settings/cache?kind=${kind}`),
    onSuccess: (data) => {
      pushToast({
        kind: "success",
        title: "缓存已清理",
        message: `释放 ${formatBytes(data.freedBytes)}`,
      });
      queryClient.invalidateQueries({ queryKey: ["settings", "cache"] });
      setClearKind(null);
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "清理失败", message: friendlyError(err) }),
  });

  const cache = cacheQuery.data;
  const usageRatio =
    cache && cache.limitBytes > 0
      ? Math.min(cache.totalBytes / cache.limitBytes, 1)
      : 0;

  const KIND_LABELS: Array<{ kind: string; label: string; bytes: number }> = cache
    ? [
        { kind: "parse", label: "解析缓存", bytes: cache.parseBytes },
        { kind: "chunk", label: "切分缓存", bytes: cache.chunkBytes },
        { kind: "embed", label: "向量缓存", bytes: cache.embedBytes },
      ]
    : [];

  return (
    <Card className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-medium text-slate-700">
          <Trash2 className="h-4 w-4 text-slate-400" />
          缓存管理
        </h2>
        {cache && (
          <span className="text-xs text-slate-400">
            {formatBytes(cache.totalBytes)} / {formatBytes(cache.limitBytes)}
          </span>
        )}
      </div>

      {cacheQuery.isLoading ? (
        <ListSkeleton rows={1} />
      ) : cache ? (
        <>
          {/* 占用进度条：超 80% 变红警示 */}
          <div className="h-2 overflow-hidden rounded-full bg-gray-100">
            <div
              className={cn(
                "h-full rounded-full transition-all",
                usageRatio > 0.8 ? "bg-red-500" : "bg-slate-400"
              )}
              style={{ width: `${Math.max(usageRatio * 100, 1)}%` }}
            />
          </div>

          <ul className="space-y-2">
            {KIND_LABELS.map(({ kind, label, bytes }) => (
              <li
                key={kind}
                className="flex items-center justify-between rounded-lg border border-gray-100 px-3 py-2"
              >
                <span className="text-sm text-slate-600">{label}</span>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-slate-400">
                    {formatBytes(bytes)}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={bytes <= 0}
                    onClick={() => setClearKind(kind)}
                  >
                    清理
                  </Button>
                </div>
              </li>
            ))}
          </ul>

          <div className="flex justify-end">
            <Button
              variant="outline"
              size="sm"
              disabled={cache.totalBytes <= 0}
              onClick={() => setClearKind("all")}
            >
              全部清理
            </Button>
          </div>
        </>
      ) : (
        <ErrorState
          message={friendlyError(cacheQuery.error)}
          onRetry={() => cacheQuery.refetch()}
        />
      )}

      <ConfirmDialog
        open={!!clearKind}
        onOpenChange={(v) => !v && setClearKind(null)}
        title={
          clearKind === "all"
            ? "清理全部缓存？"
            : `清理${KIND_LABELS.find((k) => k.kind === clearKind)?.label ?? "缓存"}？`
        }
        description="清理后下次处理相同文档需重新计算，不影响已入库数据。"
        confirmText="清理"
        danger
        loading={clearMutation.isPending}
        onConfirm={() => clearKind && clearMutation.mutate(clearKind)}
      />
    </Card>
  );
}
