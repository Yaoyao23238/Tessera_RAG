/**
 * 本地模型清单 hook（设置页 Embedding/Reranker 面板共用）。
 * 响应结构：{items: ModelStatus[], download_source: string}。
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useTaskStore } from "../stores/useTaskStore";
import type { ModelStatus } from "../types";

export interface ModelListResponse {
  items: ModelStatus[];
  downloadSource: string;
}

export function useModels() {
  const query = useQuery({
    queryKey: ["models"],
    queryFn: () => api.get<ModelListResponse>("/settings/models"),
    refetchInterval: (q) => {
      // 有下载中的模型时轮询兜底（WS 断连时进度不中断）
      return (q.state.data?.items ?? []).some((m) => m.downloading) ? 2000 : false;
    },
  });
  const downloads = useTaskStore((s) => s.modelDownloads);
  return { ...query, downloads };
}

/** 触发模型下载（可选附带下载源，切换后立即生效）。 */
export function downloadModel(modelId: string, source?: string) {
  return api.post("/settings/models/download", { modelId, source });
}

/** 中断下载并清理残留目录。 */
export function cancelModelDownload(modelId: string) {
  return api.post("/settings/models/download/cancel", { modelId });
}

/** 下载 Docling 模型包（PDF 解析必需）。 */
export function downloadDoclingBundle() {
  return api.post("/settings/models/download/docling");
}
