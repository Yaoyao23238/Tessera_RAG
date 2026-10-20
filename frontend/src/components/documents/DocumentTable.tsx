/**
 * 文档列表表格：状态圆点、大小/页数/chunk 数、行内操作（重试/解锁/预览/删除）、
 * 进行中任务显示 WS 进度。
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, KeyRound, Play, RotateCcw, Scissors, Trash2 } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { formatBytes, formatTime, cn } from "../../lib/utils";
import { useTaskStore } from "../../stores/useTaskStore";
import { StatusDot } from "../common/StatusDot";
import { Checkbox } from "../ui/checkbox";
import { Button } from "../ui/button";
import { ProgressBar } from "../ui/skeleton";
import { ConfirmDialog } from "../common/States";
import { PasswordDialog } from "./PasswordDialog";
import type { Document } from "../../types";

const ACTIVE_STATUS = new Set(["uploaded", "parsing", "chunking", "embedding"]);
const FAILED_RETRY: Record<string, { label: string; action: string }> = {
  parse_failed: { label: "重试解析", action: "reparse" },
  chunk_failed: { label: "重试切分", action: "chunk" },
  embed_failed: { label: "重试向量化", action: "index" },
  store_failed: { label: "重试写库", action: "index" },
};

export function DocumentTable({
  documents,
  selectedIds,
  onSelectionChange,
}: {
  documents: Document[];
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);
  const taskProgress = useTaskStore((s) => s.taskProgress);
  const [unlockDoc, setUnlockDoc] = useState<Document | null>(null);
  const [deleteDoc, setDeleteDoc] = useState<Document | null>(null);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["documents"] });

  const retryMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) =>
      action === "reparse"
        ? api.post(`/documents/${id}/reparse`)
        : action === "chunk"
          ? api.post(`/documents/${id}/chunk`)
          : api.post(`/documents/${id}/index`),
    onSuccess: () => {
      pushToast({ kind: "info", title: "重试任务已提交" });
      invalidate();
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "重试失败", message: friendlyError(err) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/documents/${id}`),
    onSuccess: () => {
      pushToast({ kind: "success", title: "已删除" });
      invalidate();
      setDeleteDoc(null);
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "删除失败", message: friendlyError(err) }),
  });

  const allChecked =
    documents.length > 0 && selectedIds.length === documents.length;

  function toggleAll() {
    onSelectionChange(allChecked ? [] : documents.map((d) => d.id));
  }

  function toggleOne(id: string) {
    onSelectionChange(
      selectedIds.includes(id)
        ? selectedIds.filter((x) => x !== id)
        : [...selectedIds, id]
    );
  }

  return (
    <>
      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50 text-left text-xs text-slate-500">
              <th className="w-10 px-3 py-2.5">
                <Checkbox checked={allChecked} onCheckedChange={toggleAll} />
              </th>
              <th className="px-3 py-2.5 font-medium">文件名</th>
              <th className="w-28 px-3 py-2.5 font-medium">状态</th>
              <th className="w-20 px-3 py-2.5 font-medium">大小</th>
              <th className="w-16 px-3 py-2.5 font-medium">页数</th>
              <th className="w-20 px-3 py-2.5 font-medium">Chunks</th>
              <th className="w-36 px-3 py-2.5 font-medium">上传时间</th>
              <th className="w-40 px-3 py-2.5 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => {
              const failed = doc.status.endsWith("_failed");
              const retry = FAILED_RETRY[doc.status];
              const progress = taskProgress[doc.id];
              const active = ACTIVE_STATUS.has(doc.status);
              return (
                <tr
                  key={doc.id}
                  className={cn(
                    "group border-b border-gray-50 last:border-0 hover:bg-gray-50/60",
                    failed && "bg-red-50/40"
                  )}
                >
                  <td className="px-3 py-2.5">
                    <Checkbox
                      checked={selectedIds.includes(doc.id)}
                      onCheckedChange={() => toggleOne(doc.id)}
                    />
                  </td>
                  <td className="max-w-0 px-3 py-2.5">
                    <p className="truncate font-medium text-slate-800" title={doc.filename}>
                      {doc.filename}
                    </p>
                    {failed && doc.errorMessage && (
                      <p
                        className="mt-0.5 truncate text-xs text-red-500"
                        title={doc.errorMessage}
                      >
                        {doc.errorMessage}
                      </p>
                    )}
                    {active && progress && (
                      <div className="mt-1 flex items-center gap-2">
                        <ProgressBar value={progress.progress} className="w-32" />
                        <span className="text-xs text-slate-400">
                          {progress.progress}%{progress.message ? ` · ${progress.message}` : ""}
                        </span>
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusDot status={doc.status} />
                  </td>
                  <td className="px-3 py-2.5 text-slate-500">
                    {formatBytes(doc.fileSize)}
                  </td>
                  <td className="px-3 py-2.5 text-slate-500">
                    {doc.pageCount || "-"}
                  </td>
                  <td className="px-3 py-2.5 text-slate-500">
                    {doc.chunkCount || "-"}
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-400">
                    {formatTime(doc.createdAt)}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                      {["chunked", "indexed"].includes(doc.status) && (
                        <Button
                          variant="ghost"
                          size="icon"
                          title="切分预览"
                          onClick={() => navigate(`/preview?doc=${doc.id}`)}
                        >
                          <Eye className="h-4 w-4" />
                        </Button>
                      )}
                      {doc.status === "parsed" && (
                        <Button
                          variant="ghost"
                          size="icon"
                          title="开始切分"
                          className="text-blue-600"
                          onClick={() =>
                            retryMutation.mutate({ id: doc.id, action: "chunk" })
                          }
                        >
                          <Scissors className="h-4 w-4" />
                        </Button>
                      )}
                      {doc.status === "uploaded" && (
                        <Button
                          variant="ghost"
                          size="icon"
                          title="开始解析"
                          className="text-blue-600"
                          onClick={() => {
                            // 解析完成后自动跳转解析预览页验收
                            useTaskStore.getState().setParseRedirect(doc.id);
                            retryMutation.mutate({ id: doc.id, action: "reparse" });
                          }}
                        >
                          <Play className="h-4 w-4" />
                        </Button>
                      )}
                      {doc.status === "encrypted" && (
                        <Button
                          variant="ghost"
                          size="icon"
                          title="输入密码"
                          className="text-amber-600"
                          onClick={() => setUnlockDoc(doc)}
                        >
                          <KeyRound className="h-4 w-4" />
                        </Button>
                      )}
                      {retry && (
                        <Button
                          variant="ghost"
                          size="icon"
                          title={retry.label}
                          className="text-red-500"
                          onClick={() =>
                            retryMutation.mutate({ id: doc.id, action: retry.action })
                          }
                        >
                          <RotateCcw className="h-4 w-4" />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        title="删除"
                        className="text-slate-400 hover:text-red-500"
                        onClick={() => setDeleteDoc(doc)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <PasswordDialog
        documentId={unlockDoc?.id ?? null}
        filename={unlockDoc?.filename ?? ""}
        open={!!unlockDoc}
        onOpenChange={(v) => !v && setUnlockDoc(null)}
      />

      <ConfirmDialog
        open={!!deleteDoc}
        onOpenChange={(v) => !v && setDeleteDoc(null)}
        title={`删除「${deleteDoc?.filename}」？`}
        description="将级联删除 chunks、向量与统计快照，文件从磁盘移除，不可恢复。"
        confirmText="删除"
        danger
        loading={deleteMutation.isPending}
        onConfirm={() => deleteDoc && deleteMutation.mutate(deleteDoc.id)}
      />
    </>
  );
}
