/**
 * 批量操作工具栏：多选后出现，支持删除/重解析/入库/导出。
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw, Trash2, Database, X } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { useTaskStore } from "../../stores/useTaskStore";
import { Button } from "../ui/button";
import { ConfirmDialog } from "../common/States";
import { ExportDialog } from "../export/ExportDialog";

export function BatchToolbar({
  selectedIds,
  onClear,
}: {
  selectedIds: string[];
  onClear: () => void;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);

  const batch = useMutation({
    mutationFn: (action: "delete" | "reparse" | "index") =>
      api.post<{ affected: string[] }>("/documents/batch", {
        action,
        ids: selectedIds,
      }),
    onSuccess: (data, action) => {
      const labels = { delete: "删除", reparse: "重新解析", index: "入库" };
      pushToast({
        kind: "success",
        title: `批量${labels[action]}已提交`,
        message: `影响 ${data.affected.length} 个文档`,
      });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      onClear();
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "批量操作失败", message: friendlyError(err) }),
  });

  if (selectedIds.length === 0) return null;

  return (
    <>
      <div className="flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-4 py-2 shadow-sm">
        <span className="text-sm text-slate-600">已选 {selectedIds.length} 项</span>
        <div className="mx-1 h-4 w-px bg-gray-200" />
        <Button
          variant="ghost"
          size="sm"
          onClick={() => batch.mutate("index")}
          disabled={batch.isPending}
        >
          <Database className="h-3.5 w-3.5" /> 入库
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => batch.mutate("reparse")}
          disabled={batch.isPending}
        >
          <RefreshCw className="h-3.5 w-3.5" /> 重解析
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setExportOpen(true)}>
          <Download className="h-3.5 w-3.5" /> 导出 JSONL
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="text-red-600 hover:bg-red-50"
          onClick={() => setConfirmDelete(true)}
        >
          <Trash2 className="h-3.5 w-3.5" /> 删除
        </Button>
        <div className="flex-1" />
        <Button variant="ghost" size="icon" onClick={onClear} aria-label="清除选择">
          <X className="h-4 w-4" />
        </Button>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`删除 ${selectedIds.length} 个文档？`}
        description="将级联删除 chunks、向量与统计快照，文件从磁盘移除，不可恢复。"
        confirmText="删除"
        danger
        loading={batch.isPending}
        onConfirm={() => {
          setConfirmDelete(false);
          batch.mutate("delete");
        }}
      />

      <ExportDialog
        documentIds={selectedIds}
        open={exportOpen}
        onOpenChange={setExportOpen}
      />
    </>
  );
}
