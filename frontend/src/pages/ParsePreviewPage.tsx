/**
 * 解析预览页：文档管理 → 解析预览 → 切分预览 动线的中间页。
 *
 * 回答三个问题：表格/代码有没有被正确识别？OCR 有没有乱码断行？
 * 标题层级对不对（决定切分章节树）？并提供 LLM 清洗补救手段。
 *
 * 版本模型：raw（Docling 原始）与 cleaned（LLM 清洗版）并存；
 * 「查看版本」（本页显示）与「切分版本」（doc.parseVariant，切分链路输入）
 * 分离——查看可自由切换，切分版本经「设为切分版本」显式切换并自动重切。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Download,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { useDocuments } from "../hooks/useDocuments";
import { useParseBlocks, useParseReport } from "../hooks/useParsePreview";
import { usePreviewStore } from "../stores/usePreviewStore";
import { useTaskStore } from "../stores/useTaskStore";
import { api, friendlyError } from "../lib/api";
import { ParseNavPanel } from "../components/parse/ParseNavPanel";
import { BlockCard } from "../components/parse/BlockCard";
import { QualityPanel } from "../components/parse/QualityPanel";
import { CleanDialog } from "../components/parse/CleanDialog";
import { Button } from "../components/ui/button";
import { ProgressBar, ListSkeleton } from "../components/ui/skeleton";
import { EmptyState } from "../components/common/States";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "../components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import type { ParseVariant } from "../types";

// 可预览的文档状态（解析缓存应已存在的状态集合）
const PREVIEWABLE = new Set([
  "parsed",
  "chunked",
  "indexed",
  "chunk_failed",
  "embed_failed",
  "store_failed",
]);

// 块流首屏渲染数与每次追加数（大文档防卡顿）
const INITIAL_LIMIT = 100;
const LOAD_MORE_STEP = 200;

export default function ParsePreviewPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);
  const taskProgress = useTaskStore((s) => s.taskProgress);

  const documentId = usePreviewStore((s) => s.documentId);
  const setDocumentId = usePreviewStore((s) => s.setDocumentId);

  // 查看版本：null = 跟随文档当前生效版本
  const [viewVariant, setViewVariant] = useState<ParseVariant | null>(null);
  const [typeFilter, setTypeFilter] = useState<Set<string>>(new Set());
  const [keyword, setKeyword] = useState("");
  const [qualityOpen, setQualityOpen] = useState(false);
  const [cleanOpen, setCleanOpen] = useState(false);
  const [reparseOpen, setReparseOpen] = useState(false);
  const [flashId, setFlashId] = useState<string | null>(null);
  const [limit, setLimit] = useState(INITIAL_LIMIT);
  const flowRef = useRef<HTMLDivElement>(null);
  const lastTaskStatus = useRef<string | null>(null);

  // 文档列表
  const { data: docsData } = useDocuments(1, 200);
  const previewableDocs = useMemo(
    () => (docsData?.items ?? []).filter((d) => PREVIEWABLE.has(d.status)),
    [docsData]
  );
  const currentDoc = useMemo(
    () => docsData?.items.find((d) => d.id === documentId) ?? null,
    [docsData, documentId]
  );

  // URL ?doc= 参数 / 默认选第一个可预览文档
  useEffect(() => {
    const fromUrl = searchParams.get("doc");
    if (fromUrl && fromUrl !== documentId) {
      setDocumentId(fromUrl);
      return;
    }
    if (!documentId && previewableDocs.length > 0) {
      setDocumentId(previewableDocs[0].id);
    }
  }, [searchParams, documentId, previewableDocs, setDocumentId]);

  // 换文档时重置页面状态
  useEffect(() => {
    setViewVariant(null);
    setTypeFilter(new Set());
    setKeyword("");
    setLimit(INITIAL_LIMIT);
    setQualityOpen(false);
  }, [documentId]);

  const effectiveVariant: ParseVariant | undefined = viewVariant ?? undefined;
  const { data: blocksData, isLoading: blocksLoading } = useParseBlocks(
    documentId,
    effectiveVariant
  );
  const { data: report } = useParseReport(documentId, effectiveVariant);

  const shownVariant: ParseVariant =
    blocksData?.variant ?? currentDoc?.parseVariant ?? "raw";
  const isEffective = shownVariant === (currentDoc?.parseVariant ?? "raw");

  // 清洗完成检测：status 变为 cleaned 时刷新数据并提示
  const progress = documentId ? taskProgress[documentId] : undefined;
  useEffect(() => {
    const status = progress?.status ?? null;
    if (status === "cleaned" && lastTaskStatus.current === "cleaning") {
      queryClient.invalidateQueries({ queryKey: ["parse-blocks"] });
      queryClient.invalidateQueries({ queryKey: ["parse-report"] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      pushToast({
        kind: "success",
        title: "LLM 清洗完成",
        message: progress?.message || "已自动触发重新切分",
      });
      setViewVariant(null); // 跟随新的生效版本（cleaned）
    }
    lastTaskStatus.current = status;
  }, [progress?.status, progress?.message, queryClient, pushToast]);

  // 清洗/解析忙碌状态（进度条显示）
  const busy =
    progress &&
    ["cleaning", "parsing", "chunking", "embedding"].includes(progress.status);

  // 过滤 + 搜索
  const allBlocks = useMemo(() => blocksData?.blocks ?? [], [blocksData]);
  const filteredBlocks = useMemo(() => {
    let list = allBlocks;
    if (typeFilter.size > 0) list = list.filter((b) => typeFilter.has(b.type));
    const kw = keyword.trim().toLowerCase();
    if (kw) list = list.filter((b) => b.text.toLowerCase().includes(kw));
    return list;
  }, [allBlocks, typeFilter, keyword]);
  const visibleBlocks = filteredBlocks.slice(0, limit);

  // 筛选/搜索变化时重置分批
  useEffect(() => {
    setLimit(INITIAL_LIMIT);
  }, [typeFilter, keyword, shownVariant]);

  // 滚动定位 + 闪烁
  useEffect(() => {
    const root = flowRef.current;
    const id = flashId?.split("#")[0];
    if (!root || !id) return;
    const el = root.querySelector(`[data-block-id="${id}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("block-flash");
    const timer = setTimeout(() => el.classList.remove("block-flash"), 1900);
    return () => {
      clearTimeout(timer);
      el.classList.remove("block-flash");
    };
  }, [flashId]);

  const handleJump = (blockId: string) => {
    // 目标块可能未渲染（在分页之外）：先放开分页再定位
    const index = filteredBlocks.findIndex((b) => b.id === blockId);
    if (index >= limit) setLimit(index + LOAD_MORE_STEP);
    // 等渲染完成后再闪烁定位
    setTimeout(() => setFlashId(`${blockId}#${Date.now()}`), 50);
  };

  const toggleType = (type: string) => {
    setTypeFilter((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  // 操作：设为切分版本（回退/启用清洗版）
  const variantMutation = useMutation({
    mutationFn: (variant: ParseVariant) =>
      api.post(`/documents/${documentId}/parse-variant`, { variant }),
    onSuccess: (_data, variant) => {
      pushToast({
        kind: "info",
        title: variant === "cleaned" ? "已切换为清洗版" : "已切回原始版",
        message: "已自动触发重新切分",
      });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      setViewVariant(null);
    },
    onError: (err) =>
      pushToast({
        kind: "error",
        title: "切换失败",
        message: friendlyError(err),
      }),
  });

  // 操作：重新解析（重跑 Docling，清清洗版）
  const reparseMutation = useMutation({
    mutationFn: () => api.post(`/documents/${documentId}/reparse`),
    onSuccess: () => {
      pushToast({ kind: "info", title: "重新解析已提交" });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      setReparseOpen(false);
    },
    onError: (err) =>
      pushToast({
        kind: "error",
        title: "解析失败",
        message: friendlyError(err),
      }),
  });

  // 操作：前往切分（提交切分任务，完成后由 WS 自动跳转切分预览页）
  const chunkMutation = useMutation({
    mutationFn: () => {
      if (!documentId) throw new Error("未选择文档");
      useTaskStore.getState().setChunkRedirect(documentId);
      return api.post(`/documents/${documentId}/chunk`);
    },
    onSuccess: () => {
      pushToast({ kind: "info", title: "切分任务已提交" });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err) => {
      // 提交失败：取消等待跳转，避免未来 chunked 事件误跳
      useTaskStore.getState().setChunkRedirect(null);
      pushToast({
        kind: "error",
        title: "切分提交失败",
        message: friendlyError(err),
      });
    },
  });

  // 操作：导出块 JSON（当前查看版本）
  const handleExport = () => {
    if (!blocksData) return;
    const payload = {
      document: currentDoc?.filename,
      variant: shownVariant,
      cleaned_meta: blocksData.cleanedMeta,
      exported_at: new Date().toISOString(),
      blocks: allBlocks,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${currentDoc?.title || "document"}.blocks.${shownVariant}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // 质量面板一键操作
  const handleQualityAction = (action: string) => {
    if (action === "open_clean_dialog") setCleanOpen(true);
    else if (action === "reparse") setReparseOpen(true);
  };

  if (previewableDocs.length === 0 && docsData) {
    return (
      <div className="flex h-full flex-col p-6">
        <h1 className="text-lg font-semibold text-slate-900">解析预览</h1>
        <EmptyState
          title="暂无可预览的文档"
          hint="请先在「文档管理」上传文档并完成解析"
        />
      </div>
    );
  }

  const warningCount = report?.warnings.length ?? 0;
  const hasDanger = report?.warnings.some((w) => w.severity === "danger");

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      {/* 顶栏 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-lg font-semibold text-slate-900">解析预览</h1>
        <Select value={documentId ?? ""} onValueChange={(v) => setDocumentId(v)}>
          <SelectTrigger className="h-9 w-64 bg-white">
            <SelectValue placeholder="选择文档" />
          </SelectTrigger>
          <SelectContent>
            {previewableDocs.map((d) => (
              <SelectItem key={d.id} value={d.id}>
                {d.filename}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* 版本徽标 + 切换 */}
        <span className="inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1.5 text-xs text-slate-500 ring-1 ring-gray-200">
          {shownVariant === "cleaned" ? (
            <>
              <Sparkles className="h-3.5 w-3.5 text-violet-500" />
              清洗版
              {blocksData?.cleanedMeta?.modelName && (
                <span className="text-slate-400">
                  · {blocksData.cleanedMeta.modelName}
                </span>
              )}
              {blocksData?.cleanedMeta?.usage?.totalTokens != null && (
                <span className="text-slate-300">
                  {blocksData.cleanedMeta.usage.totalTokens.toLocaleString()} tokens
                </span>
              )}
            </>
          ) : (
            "原始版"
          )}
          {report?.hasCleaned && (
            <button
              type="button"
              className="ml-1 text-blue-500 hover:text-blue-600"
              onClick={() =>
                setViewVariant(shownVariant === "cleaned" ? "raw" : "cleaned")
              }
            >
              查看{shownVariant === "cleaned" ? "原始版" : "清洗版"}
            </button>
          )}
        </span>
        {!isEffective && (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            disabled={variantMutation.isPending}
            onClick={() => variantMutation.mutate(shownVariant)}
          >
            设为切分版本
          </Button>
        )}

        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCleanOpen(true)}
            disabled={!documentId || !!busy}
          >
            <Sparkles className="h-3.5 w-3.5" /> LLM 辅助
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setReparseOpen(true)}
            disabled={!documentId || !!busy}
          >
            <RefreshCw className="h-3.5 w-3.5" /> 重新解析
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleExport}
            disabled={!blocksData}
          >
            <Download className="h-3.5 w-3.5" /> 导出块 JSON
          </Button>
          <Button
            size="sm"
            onClick={() => chunkMutation.mutate()}
            disabled={!documentId || chunkMutation.isPending || !!busy}
          >
            {chunkMutation.isPending ? "提交中…" : "前往切分"}{" "}
            <ArrowRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* 任务进度条 */}
      {busy && progress && (
        <div className="flex items-center gap-3 rounded-xl border border-blue-100 bg-blue-50 px-4 py-2">
          <ProgressBar
            value={progress.progress}
            className="flex-1"
            barClassName="bg-blue-500"
          />
          <span className="text-xs text-blue-600">
            {progress.progress}%{progress.message ? ` · ${progress.message}` : ""}
          </span>
        </div>
      )}

      {/* 质量警告条 */}
      {report && warningCount > 0 && (
        <button
          type="button"
          onClick={() => setQualityOpen((v) => !v)}
          className={`flex items-center gap-2 rounded-xl border px-4 py-2 text-left text-xs transition-colors ${
            hasDanger
              ? "border-red-200 bg-red-50 text-red-700 hover:bg-red-100"
              : "border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100"
          }`}
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          检测到 {warningCount} 处解析质量问题
          <span className="ml-auto inline-flex items-center gap-1 text-[11px] opacity-70">
            {qualityOpen ? (
              <>
                收起 <ChevronUp className="h-3 w-3" />
              </>
            ) : (
              <>
                展开质量面板 <ChevronDown className="h-3 w-3" />
              </>
            )}
          </span>
        </button>
      )}
      {(qualityOpen || warningCount === 0) && report && (
        <QualityPanel report={report} onAction={handleQualityAction} />
      )}

      {/* 主体：左栏导航 + 块流 */}
      <div className="grid min-h-0 flex-1 grid-cols-[240px_1fr] gap-3">
        <ParseNavPanel
          blocks={allBlocks}
          typeFilter={typeFilter}
          onToggleType={toggleType}
          keyword={keyword}
          onKeywordChange={setKeyword}
          onJump={handleJump}
        />

        <div
          ref={flowRef}
          className="min-h-0 overflow-y-auto rounded-xl thin-scroll"
        >
          {blocksLoading ? (
            <ListSkeleton rows={5} />
          ) : filteredBlocks.length === 0 ? (
            <div className="flex h-full items-center justify-center rounded-xl border border-gray-200 bg-white">
              <EmptyState
                title={allBlocks.length === 0 ? "暂无解析块" : "无匹配块"}
                hint={
                  allBlocks.length === 0
                    ? "该文档尚无解析缓存，请先解析"
                    : "调整类型筛选或搜索关键词"
                }
              />
            </div>
          ) : (
            <div className="space-y-2 pb-4">
              <p className="px-1 text-[11px] text-slate-400">
                {filteredBlocks.length === allBlocks.length
                  ? `共 ${allBlocks.length} 块`
                  : `筛选出 ${filteredBlocks.length} / ${allBlocks.length} 块`}
                {keyword.trim() && `，关键词「${keyword.trim()}」`}
              </p>
              {visibleBlocks.map((block) => (
                <BlockCard
                  key={block.id}
                  block={block}
                  highlight={keyword.trim() || undefined}
                />
              ))}
              {filteredBlocks.length > limit && (
                <div className="pt-1 text-center">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setLimit((v) => v + LOAD_MORE_STEP)}
                  >
                    显示更多（剩余 {filteredBlocks.length - limit} 块）
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* LLM 清洗弹窗 */}
      {documentId && (
        <CleanDialog
          documentId={documentId}
          report={report}
          open={cleanOpen}
          onOpenChange={setCleanOpen}
        />
      )}

      {/* 重新解析确认 */}
      <Dialog open={reparseOpen} onOpenChange={setReparseOpen}>
        <DialogContent className="max-w-sm">
          <DialogTitle className="flex items-center gap-2">
            <RefreshCw className="h-4 w-4 text-blue-500" />
            重新解析
          </DialogTitle>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            将重新运行 Docling 解析（耗时较长），并清除已有的 LLM
            清洗版。解析完成后会自动重新切分。确定继续？
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setReparseOpen(false)}
            >
              取消
            </Button>
            <Button
              size="sm"
              disabled={reparseMutation.isPending}
              onClick={() => reparseMutation.mutate()}
            >
              {reparseMutation.isPending ? "提交中…" : "确认重新解析"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
