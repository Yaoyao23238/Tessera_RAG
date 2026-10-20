/**
 * 拖拽上传区：多选上传、格式过滤、重复文件确认覆盖。
 */

import { useRef, useState, type DragEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { UploadCloud } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { SUPPORTED_FORMATS } from "../../lib/constants";
import { useTaskStore } from "../../stores/useTaskStore";
import { ConfirmDialog } from "../common/States";
import { cn } from "../../lib/utils";
import type { UploadResultItem } from "../../types";

export function UploadZone() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [duplicates, setDuplicates] = useState<string[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);

  /** 实际上传逻辑；overwrite=true 时覆盖同 hash 文档。 */
  async function doUpload(files: File[], overwrite: boolean) {
    if (files.length === 0) return;
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    try {
      const data = await api.upload<{ results: UploadResultItem[] }>(
        `/documents/upload${overwrite ? "?overwrite=true" : ""}`,
        form
      );
      const results = data.results;
      const created = results.filter((r) => r.status === "created");
      const rejected = results.filter((r) => r.status === "rejected");
      const exists = results.filter((r) => r.status === "exists");

      if (created.length > 0) {
        pushToast({
          kind: "success",
          title: `已上传 ${created.length} 个文件`,
          message: "请在列表中点「开始解析」或批量勾选后点「重解析」",
        });
      }
      for (const r of rejected) {
        pushToast({ kind: "error", title: `拒绝：${r.filename}`, message: r.reason });
      }
      if (exists.length > 0 && !overwrite) {
        // 有重复文件：保留原文件列表，弹确认
        setPendingFiles(files.filter((f) => exists.some((e) => e.filename === f.name)));
        setDuplicates(exists.map((e) => e.filename));
        setConfirmOpen(true);
      }
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    } catch (err) {
      pushToast({ kind: "error", title: "上传失败", message: friendlyError(err) });
    }
  }

  function acceptFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const files = Array.from(list);
    // 前端先过滤一轮不支持格式
    const ok = files.filter((f) =>
      SUPPORTED_FORMATS.some((ext) => f.name.toLowerCase().endsWith(ext))
    );
    const bad = files.filter((f) => !ok.includes(f));
    for (const f of bad) {
      pushToast({
        kind: "error",
        title: `不支持的格式：${f.name}`,
        message: `支持：${SUPPORTED_FORMATS.join(" ")}`,
      });
    }
    void doUpload(ok, false);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    acceptFiles(e.dataTransfer.files);
  }

  return (
    <>
      <div
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors",
          dragging
            ? "border-slate-500 bg-slate-50"
            : "border-gray-300 bg-white hover:border-slate-400"
        )}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <UploadCloud className="h-8 w-8 text-slate-400" />
        <p className="text-sm font-medium text-slate-700">
          拖拽文件到此处，或点击选择（支持多选）
        </p>
        <p className="text-xs text-slate-400">
          支持格式：{SUPPORTED_FORMATS.join(" / ")}
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={SUPPORTED_FORMATS.join(",")}
          className="hidden"
          onChange={(e) => {
            acceptFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {/* 重复文件覆盖确认 */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="文件已存在"
        description={
          <>
            以下文件与已有文档内容相同（hash 一致）：
            <span className="mt-1 block font-medium text-slate-700">
              {duplicates.join("、")}
            </span>
            是否覆盖重新上传？
          </>
        }
        confirmText="覆盖上传"
        onConfirm={() => {
          setConfirmOpen(false);
          void doUpload(pendingFiles, true);
          setPendingFiles([]);
        }}
      />
    </>
  );
}
