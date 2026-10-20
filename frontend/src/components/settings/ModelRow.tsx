/**
 * 本地模型行：名称/描述/体积/下载状态 + 下载按钮 + WS 实时进度条。
 * 下载中可取消；下载失败（downloadedMb=-1）可清理残留后重试。
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Download, XCircle } from "lucide-react";
import { friendlyError } from "../../lib/api";
import {
  cancelModelDownload,
  downloadModel,
} from "../../hooks/useModels";
import { useTaskStore } from "../../stores/useTaskStore";
import { Button } from "../ui/button";
import { ProgressBar } from "../ui/skeleton";
import { formatBytes } from "../../lib/utils";
import type { ModelStatus } from "../../types";

export function ModelRow({
  model,
  selected,
  onSelect,
}: {
  model: ModelStatus;
  /** 单选模式（Embedding 面板） */
  selected?: boolean;
  onSelect?: () => void;
}) {
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);
  const progress = useTaskStore((s) => s.modelDownloads[model.modelId]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["models"] });

  const mutation = useMutation({
    mutationFn: () => downloadModel(model.modelId),
    onSuccess: () => invalidate(),
    onError: (err) =>
      pushToast({ kind: "error", title: "下载触发失败", message: friendlyError(err) }),
  });

  const cancelMutation = useMutation({
    mutationFn: () => cancelModelDownload(model.modelId),
    onSuccess: () => {
      pushToast({ kind: "info", title: "已取消下载并清理残留" });
      invalidate();
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "取消失败", message: friendlyError(err) }),
  });

  // downloadedMb=-1 表示下载失败（后端 WS 推送约定）
  const failed =
    progress && progress.downloadedMb < 0 && !model.downloaded;
  const downloading =
    model.downloading ||
    (progress && progress.totalMb > 0 && progress.downloadedMb >= 0 &&
      progress.downloadedMb < progress.totalMb);
  const pct =
    progress && progress.totalMb > 0
      ? Math.round((Math.max(progress.downloadedMb, 0) / progress.totalMb) * 100)
      : 0;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-3">
      <div className="flex items-center gap-3">
        {onSelect && (
          <input
            type="radio"
            checked={selected}
            onChange={onSelect}
            className="h-4 w-4 accent-slate-900"
          />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-slate-800">{model.modelId}</p>
          <p className="text-xs text-slate-400">
            {model.description} · {formatBytes(model.sizeMb * 1024 * 1024)}
          </p>
        </div>
        {model.downloaded ? (
          <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
            <CheckCircle2 className="h-3.5 w-3.5" /> 已下载
          </span>
        ) : failed ? (
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 text-xs text-red-500">
              <AlertTriangle className="h-3.5 w-3.5" /> 下载失败
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={cancelMutation.isPending}
              onClick={() => cancelMutation.mutate()}
              title="清理残留文件"
            >
              <XCircle className="h-3.5 w-3.5" /> 清理
            </Button>
          </div>
        ) : downloading ? (
          <div className="flex items-center gap-2">
            <span className="text-xs text-blue-500">下载中 {pct}%</span>
            <Button
              variant="ghost"
              size="sm"
              className="text-slate-400 hover:text-red-500"
              disabled={cancelMutation.isPending}
              onClick={() => cancelMutation.mutate()}
              title="取消下载并清理残留"
            >
              取消
            </Button>
          </div>
        ) : (
          <Button
            variant="outline"
            size="sm"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            <Download className="h-3.5 w-3.5" /> 下载
          </Button>
        )}
      </div>
      {downloading && progress && progress.totalMb > 0 && (
        <div className="mt-2 flex items-center gap-2">
          <ProgressBar value={pct} className="flex-1" barClassName="bg-blue-500" />
          <span className="text-[10px] tabular-nums text-slate-400">
            {Math.max(progress.downloadedMb, 0).toFixed(0)}/{progress.totalMb.toFixed(0)} MB
          </span>
        </div>
      )}
    </div>
  );
}
