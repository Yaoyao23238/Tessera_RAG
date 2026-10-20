/**
 * 全局任务状态 store：WS 三频道事件落地 + toast 通知。
 *
 * - taskProgress: documentId → 最新任务进度（task_progress 频道）
 * - modelDownloads: model → 下载进度（model_download 频道）
 * - experimentProgress: experimentId → 进度（experiment_progress 频道）
 * - toasts: 轻量全局通知（进度完成/失败、操作结果）
 */

import { create } from "zustand";
import type {
  ExperimentProgressPayload,
  ModelDownloadPayload,
  TaskProgressPayload,
} from "../types";

export interface Toast {
  id: number;
  kind: "info" | "success" | "error" | "progress";
  title: string;
  message?: string;
  /** 0-100，仅 kind=progress 使用 */
  progress?: number;
}

interface TaskState {
  taskProgress: Record<string, TaskProgressPayload>;
  modelDownloads: Record<string, ModelDownloadPayload>;
  experimentProgress: Record<string, ExperimentProgressPayload>;
  toasts: Toast[];
  /** 正在等待解析完成的文档 id：完成后自动跳转解析预览页验收 */
  parseRedirectId: string | null;
  /** 正在等待切分完成的文档 id：完成后自动跳转切分预览页 */
  chunkRedirectId: string | null;

  setTaskProgress: (documentId: string, p: TaskProgressPayload) => void;
  setModelDownload: (p: ModelDownloadPayload) => void;
  setExperimentProgress: (experimentId: string, p: ExperimentProgressPayload) => void;
  setParseRedirect: (documentId: string | null) => void;
  setChunkRedirect: (documentId: string | null) => void;
  pushToast: (t: Omit<Toast, "id">) => number;
  updateToast: (id: number, patch: Partial<Toast>) => void;
  dismissToast: (id: number) => void;
}

let toastSeq = 1;

export const useTaskStore = create<TaskState>((set) => ({
  taskProgress: {},
  modelDownloads: {},
  experimentProgress: {},
  toasts: [],
  parseRedirectId: null,
  chunkRedirectId: null,

  setTaskProgress: (documentId, p) =>
    set((s) => ({ taskProgress: { ...s.taskProgress, [documentId]: p } })),

  setParseRedirect: (documentId) =>
    set(() => ({ parseRedirectId: documentId })),

  setChunkRedirect: (documentId) =>
    set(() => ({ chunkRedirectId: documentId })),

  setModelDownload: (p) =>
    set((s) => ({ modelDownloads: { ...s.modelDownloads, [p.model]: p } })),

  setExperimentProgress: (experimentId, p) =>
    set((s) => ({
      experimentProgress: { ...s.experimentProgress, [experimentId]: p },
    })),

  pushToast: (t) => {
    const id = toastSeq++;
    set((s) => ({ toasts: [...s.toasts.slice(-4), { ...t, id }] }));
    // 非进度类 toast 自动消失
    if (t.kind !== "progress") {
      setTimeout(() => {
        useTaskStore.getState().dismissToast(id);
      }, t.kind === "error" ? 6000 : 3500);
    }
    return id;
  },

  updateToast: (id, patch) =>
    set((s) => ({
      toasts: s.toasts.map((t) => (t.id === id ? { ...t, ...patch } : t)),
    })),

  dismissToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
