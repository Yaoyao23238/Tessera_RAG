/**
 * URL 导入：输入网址抓取为文档（复用现有解析链路）。
 */

import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link2 } from "lucide-react";
import { api, friendlyError } from "../../lib/api";
import { useTaskStore } from "../../stores/useTaskStore";
import { ConfirmDialog } from "../common/States";
import type { UploadResultItem } from "../../types";

export function UrlImport() {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);

  async function doImport(overwrite: boolean) {
    const target = url.trim();
    if (!target) return;
    setLoading(true);
    try {
      const data = await api.post<{ results: UploadResultItem[] }>(
        `/documents/import-url${overwrite ? "?overwrite=true" : ""}`,
        { url: target }
      );
      const result = data.results[0];
      if (result?.status === "created") {
        pushToast({
          kind: "success",
          title: "已导入网页",
          message: "请在列表中点「开始解析」或勾选后点「重解析」",
        });
        setUrl("");
      } else if (result?.status === "exists" && !overwrite) {
        setConfirmOpen(true);
      } else if (result?.status === "rejected") {
        pushToast({ kind: "error", title: "导入失败", message: result.reason });
      }
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    } catch (err) {
      pushToast({ kind: "error", title: "导入失败", message: friendlyError(err) });
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void doImport(false);
  }

  return (
    <>
      <form
        onSubmit={onSubmit}
        className="flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2"
      >
        <Link2 className="h-4 w-4 shrink-0 text-slate-400" />
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="粘贴网页或文件链接，如 https://example.com/article"
          className="min-w-0 flex-1 bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400"
        />
        <button
          type="submit"
          disabled={loading || !url.trim()}
          className="shrink-0 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {loading ? "抓取中…" : "导入"}
        </button>
      </form>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="页面已导入"
        description={
          <>
            该页面内容与已有文档相同（hash 一致）：
            <span className="mt-1 block font-medium text-slate-700">{url.trim()}</span>
            是否覆盖重新导入？
          </>
        }
        confirmText="覆盖导入"
        onConfirm={() => {
          setConfirmOpen(false);
          void doImport(true);
        }}
      />
    </>
  );
}