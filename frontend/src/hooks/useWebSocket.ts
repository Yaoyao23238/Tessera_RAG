/**
 * 全局 WebSocket hook：应用挂载时建立单连接，三频道消息分发到
 * useTaskStore，并对终态事件弹 toast。
 */

import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { WsClient, type WsMessage } from "../lib/ws";
import { WS_MSG, DOC_STATUS_META, type DocStatus } from "../lib/constants";
import { useTaskStore } from "../stores/useTaskStore";
import type {
  ExperimentProgressPayload,
  ModelDownloadPayload,
  TaskProgressPayload,
} from "../types";

// 终态状态集合（到达后刷新相关查询并弹 toast）
// cleaned：LLM 清洗完成（非 DocStatus 枚举值，仅 WS 推送态）
const FINAL_STATUS = new Set([
  "parsed",
  "chunked",
  "indexed",
  "parse_failed",
  "chunk_failed",
  "embed_failed",
  "store_failed",
  "encrypted",
  "cleaned",
]);

// 非 DocStatus 枚举的 WS 状态兜底文案
const EXTRA_STATUS_LABEL: Record<string, string> = {
  cleaned: "清洗完成",
};

export function useWebSocket() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  useEffect(() => {
    const client = new WsClient("/tasks");
    client.connect();
    const store = useTaskStore.getState;

    const offTask = client.on(WS_MSG.TASK_PROGRESS, (msg: WsMessage) => {
      const p = msg.payload as unknown as TaskProgressPayload;
      if (msg.document_id) {
        store().setTaskProgress(msg.document_id, p);
      }
      if (FINAL_STATUS.has(p.status)) {
        // 解析完成 → 自动跳转解析预览页验收（动线：上传 → 解析 → 验收 → 切分）
        if (p.status === "parsed" && msg.document_id) {
          const redirectId = useTaskStore.getState().parseRedirectId;
          if (redirectId === msg.document_id) {
            useTaskStore.getState().setParseRedirect(null);
            navigate(`/parse?doc=${msg.document_id}`);
          }
        }
        // 切分完成 → 自动跳转切分预览页（动线：解析预览 → 前往切分 → 切分预览）
        if (p.status === "chunked" && msg.document_id) {
          const redirectId = useTaskStore.getState().chunkRedirectId;
          if (redirectId === msg.document_id) {
            useTaskStore.getState().setChunkRedirect(null);
            navigate(`/preview?doc=${msg.document_id}`);
          }
        }
        // 刷新文档/chunk/统计相关缓存
        queryClient.invalidateQueries({ queryKey: ["documents"] });
        if (msg.document_id) {
          queryClient.invalidateQueries({
            queryKey: ["chunks", msg.document_id],
          });
          queryClient.invalidateQueries({
            queryKey: ["chunk-stats", msg.document_id],
          });
        }
        // 清洗完成额外刷新解析预览查询（不依赖用户停留在解析预览页）
        if (p.status === "cleaned") {
          queryClient.invalidateQueries({ queryKey: ["parse-blocks"] });
          queryClient.invalidateQueries({ queryKey: ["parse-report"] });
        }
        const meta = DOC_STATUS_META[p.status as DocStatus];
        const failed = p.status.endsWith("_failed");
        store().pushToast({
          kind: failed ? "error" : "success",
          title: `${meta?.label ?? EXTRA_STATUS_LABEL[p.status] ?? p.status}`,
          message: p.message || undefined,
        });
      }
    });

    const offModel = client.on(WS_MSG.MODEL_DOWNLOAD, (msg: WsMessage) => {
      const p = msg.payload as unknown as ModelDownloadPayload;
      store().setModelDownload(p);
      if (p.downloadedMb < 0) {
        store().pushToast({
          kind: "error",
          title: "模型下载失败",
          message: p.model,
        });
      } else if (p.totalMb > 0 && p.downloadedMb >= p.totalMb) {
        store().pushToast({
          kind: "success",
          title: "模型下载完成",
          message: p.model,
        });
        queryClient.invalidateQueries({ queryKey: ["models"] });
      }
    });

    const offExp = client.on(WS_MSG.EXPERIMENT_PROGRESS, (msg: WsMessage) => {
      const p = msg.payload as unknown as ExperimentProgressPayload;
      if (msg.experiment_id) {
        store().setExperimentProgress(msg.experiment_id, p);
        if (p.total > 0 && p.current >= p.total) {
          queryClient.invalidateQueries({ queryKey: ["experiments"] });
        }
      }
    });

    return () => {
      offTask();
      offModel();
      offExp();
      client.close();
    };
  }, [queryClient]);
}
