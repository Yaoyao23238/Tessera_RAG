/**
 * 测试集管理面板：列表、上传 JSON/JSONL、LLM 自动生成（默认 20 条）、删除。
 */

import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Trash2, Upload } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { useTaskStore } from "../../stores/useTaskStore";
import { useDocuments } from "../../hooks/useDocuments";
import { Button } from "../ui/button";
import { Input, FieldLabel } from "../ui/input";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";
import { ConfirmDialog } from "../common/States";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { formatTime } from "../../lib/utils";
import type { QASet } from "../../types";

export function TestSetPanel({ qasets }: { qasets: QASet[] }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [generateOpen, setGenerateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<QASet | null>(null);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["qasets"] });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.upload<QASet>("/evaluation/qasets", form);
    },
    onSuccess: (data) => {
      pushToast({
        kind: "success",
        title: `测试集「${data.name}」已上传`,
        message: `${data.itemCount} 条 Q&A`,
      });
      invalidate();
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "上传失败", message: friendlyError(err) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/evaluation/qasets/${id}`),
    onSuccess: () => {
      pushToast({ kind: "success", title: "测试集已删除" });
      invalidate();
      setDeleteTarget(null);
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "删除失败", message: friendlyError(err) }),
  });

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium text-slate-700">
          测试集 <span className="text-xs text-slate-400">（{qasets.length}）</span>
        </h2>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()}>
            <Upload className="h-3.5 w-3.5" /> 上传 JSON/JSONL
          </Button>
          <Button variant="outline" size="sm" onClick={() => setGenerateOpen(true)}>
            <Sparkles className="h-3.5 w-3.5" /> LLM 生成
          </Button>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".json,.jsonl"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) uploadMutation.mutate(f);
            e.target.value = "";
          }}
        />
      </div>

      {qasets.length === 0 ? (
        <p className="py-4 text-center text-xs text-slate-400">
          暂无测试集：上传 JSON/JSONL（含 question 字段）或用 LLM 按文档生成
        </p>
      ) : (
        <ul className="max-h-44 space-y-1.5 overflow-y-auto thin-scroll">
          {qasets.map((qs) => (
            <li
              key={qs.id}
              className="group flex items-center gap-2 rounded-lg border border-gray-100 px-3 py-2 text-sm"
            >
              <span className="min-w-0 flex-1 truncate font-medium text-slate-700">
                {qs.name}
              </span>
              <span className="text-xs text-slate-400">{qs.itemCount} 条</span>
              <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                {qs.source === "upload" ? "上传" : "LLM 生成"}
              </span>
              <span className="text-[10px] text-slate-300">
                {formatTime(qs.createdAt)}
              </span>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 opacity-0 group-hover:opacity-100"
                title="删除"
                onClick={() => setDeleteTarget(qs)}
              >
                <Trash2 className="h-3.5 w-3.5 text-slate-400 hover:text-red-500" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <GenerateDialog open={generateOpen} onOpenChange={setGenerateOpen} />

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(v) => !v && setDeleteTarget(null)}
        title={`删除测试集「${deleteTarget?.name}」？`}
        description="将删除全部 Q&A 条目，已运行的实验结果保留。"
        confirmText="删除"
        danger
        loading={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
      />
    </div>
  );
}

/** LLM 生成测试集对话框：选文档 + 条数（默认 20）。 */
function GenerateDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [docId, setDocId] = useState("");
  const [count, setCount] = useState("20");
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);
  const { data: docsData } = useDocuments(1, 200);
  // 仅已切分的文档可生成（后端要求文档有 chunk）
  const candidates = (docsData?.items ?? []).filter((d) =>
    ["chunked", "indexed"].includes(d.status)
  );

  const mutation = useMutation({
    mutationFn: () =>
      api.post<QASet>("/evaluation/qasets/generate", {
        documentId: docId,
        count: Number(count) || 20,
      }),
    onSuccess: (data) => {
      pushToast({
        kind: "success",
        title: "生成完成",
        message: `「${data.name}」共 ${data.itemCount} 条`,
      });
      queryClient.invalidateQueries({ queryKey: ["qasets"] });
      onOpenChange(false);
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "生成失败", message: friendlyError(err) }),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogTitle>LLM 自动生成测试集</DialogTitle>
        <p className="mt-1 text-xs text-slate-400">
          按 chunk 均匀抽样（跳过表格/图片块），调用已配置的默认 LLM 生成问答对
        </p>
        <div className="mt-4 space-y-3">
          <div>
            <FieldLabel required>来源文档</FieldLabel>
            <Select value={docId} onValueChange={setDocId}>
              <SelectTrigger>
                <SelectValue placeholder="选择已切分文档" />
              </SelectTrigger>
              <SelectContent>
                {candidates.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.filename}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <FieldLabel>生成条数</FieldLabel>
            <Input
              type="number"
              min={1}
              max={100}
              value={count}
              onChange={(e) => setCount(e.target.value)}
            />
          </div>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!docId || mutation.isPending}
          >
            {mutation.isPending ? "生成中（可能较慢）…" : "开始生成"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
