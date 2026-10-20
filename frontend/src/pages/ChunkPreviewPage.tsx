/**
 * 切分预览页（项目核心）：顶部工具栏（文档选择器 + 参数面板 + 重切/入库）
 * + 左右分栏（右→左单向联动）+ 章节树 + 统计仪表盘。
 *
 * 左侧原文面板直接用解析 Blocks（/documents/{id}/blocks）渲染，
 * 与解析预览页同源同组件，保证表格/图片/代码渲染一致。
 * 联动方向（用户要求）：右侧点击/悬停 → 左侧定位高亮；
 * 左侧滚动不驱动右侧（已移除），右侧列表仅章节树跳转时滚动。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  ChevronDown,
  ChevronUp,
  Database,
  Download,
  RefreshCw,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { useDocuments } from "../hooks/useDocuments";
import { useAllChunks, useChunkStats } from "../hooks/useChunks";
import { useParseBlocks } from "../hooks/useParsePreview";
import { usePreviewStore } from "../stores/usePreviewStore";
import {
  CONSERVATIVE_MERGE_THRESHOLD,
  useLLMAssistStore,
} from "../stores/useLLMAssistStore";
import { useTaskStore } from "../stores/useTaskStore";
import { api, friendlyError } from "../lib/api";
import { ParamPanel } from "../components/preview/ParamPanel";
import { SourcePanel } from "../components/preview/SourcePanel";
import { ChunkList } from "../components/preview/ChunkList";
import { StatsDashboard } from "../components/preview/StatsDashboard";
import { ChunkLLMDialog } from "../components/preview/ChunkLLMDialog";
import { ExportDialog } from "../components/export/ExportDialog";
import { Button } from "../components/ui/button";
import { ProgressBar, ListSkeleton } from "../components/ui/skeleton";
import { EmptyState } from "../components/common/States";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import type { Chunk } from "../types";

// 可预览的文档状态
const PREVIEWABLE = new Set(["chunked", "indexed", "embed_failed", "store_failed"]);

export default function ChunkPreviewPage() {
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const pushToast = useTaskStore((s) => s.pushToast);
  const taskProgress = useTaskStore((s) => s.taskProgress);

  const documentId = usePreviewStore((s) => s.documentId);
  const setDocumentId = usePreviewStore((s) => s.setDocumentId);
  const config = usePreviewStore((s) => s.config);
  const excludedSections = usePreviewStore((s) => s.excludedSections);
  // LLM 辅助（边界合并增强）档位：独立弹窗控制，重切时混入提交
  const chunkLlmMode = useLLMAssistStore((s) => s.chunkLlmMode);

  // 联动状态：hover（右侧卡片悬停，瞬时）与 reading（锚定：右侧点击/章节跳转）分离
  const [hoverChunkId, setHoverChunkId] = useState<string | null>(null);
  const [hoverBlockId, setHoverBlockId] = useState<string | null>(null);
  const [readingChunkId, setReadingChunkId] = useState<string | null>(null);
  const [flashLeftId, setFlashLeftId] = useState<string | null>(null);
  const [scrollToRightId, setScrollToRightId] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [llmOpen, setLlmOpen] = useState(false);
  // 参数面板 / 统计仪表盘折叠状态（默认折叠，顶栏按钮控制）
  const [paramOpen, setParamOpen] = useState(false);
  const [statsOpen, setStatsOpen] = useState(false);

  // 文档列表（取前 200 条，可预览状态过滤在下拉中完成）
  const { data: docsData } = useDocuments(1, 200);
  const previewableDocs = useMemo(
    () => (docsData?.items ?? []).filter((d) => PREVIEWABLE.has(d.status)),
    [docsData]
  );
  const currentDoc = useMemo(
    () => docsData?.items.find((d) => d.id === documentId) ?? null,
    [docsData, documentId]
  );

  // URL ?doc= 参数初始化 / 默认选第一个可预览文档
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

  const { data: chunks, isLoading: chunksLoading } = useAllChunks(documentId);
  const { data: blocksData } = useParseBlocks(documentId);
  const { data: stats } = useChunkStats(
    currentDoc && ["chunked", "indexed"].includes(currentDoc.status) ? documentId : null,
    true
  );

  const allChunks = useMemo(() => chunks ?? [], [chunks]);
  // 左侧原文面板：解析 Blocks（与解析预览页同源）
  const leftBlocks = useMemo(() => blocksData?.blocks ?? [], [blocksData]);

  // chunk → 首个 block id（右侧点 chunk 定位左侧原文）
  const blockIdByChunkId = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of allChunks) {
      if (c.blockIds.length > 0) m.set(c.id, c.blockIds[0]);
    }
    return m;
  }, [allChunks]);

  // block → chunk id（左栏悬停区间高亮 + 章节树跳转用；一个 block 只属于一个 chunk）
  const chunkIdByBlockId = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of allChunks) {
      for (const bid of c.blockIds) {
        if (!m.has(bid)) m.set(bid, c.id);
      }
    }
    return m;
  }, [allChunks]);

  // 右侧卡片显示态 = hover 优先，否则阅读锚定位置
  const displayChunkId = hoverChunkId ?? readingChunkId;

  // 左栏高亮集合：仅由鼠标悬停驱动（用户要求：悬停才高亮，滚动/驻留不高亮）
  // - 悬停左栏某 block：高亮该 block + 其所属 chunk 覆盖的全部 block（区间高亮）
  // - 悬停右侧 chunk 卡片：高亮该 chunk 覆盖的全部 block
  // - 都不悬停：无高亮（章节跳转/点击走 flash 闪烁动画，不留驻高亮）
  const highlightBlockIds = useMemo(() => {
    const ids = new Set<string>();
    if (hoverBlockId) {
      ids.add(hoverBlockId);
      const cid = chunkIdByBlockId.get(hoverBlockId);
      const chunk = cid ? allChunks.find((c) => c.id === cid) : null;
      if (chunk) for (const bid of chunk.blockIds) ids.add(bid);
    } else if (hoverChunkId) {
      const chunk = allChunks.find((c) => c.id === hoverChunkId);
      if (chunk) for (const bid of chunk.blockIds) ids.add(bid);
    }
    return ids;
  }, [hoverBlockId, hoverChunkId, allChunks, chunkIdByBlockId]);

  // 右侧 hover → 左侧区间高亮
  const handleHoverRight = useCallback((id: string | null) => {
    setHoverChunkId(id);
  }, []);

  // 左栏 block hover → 仅左栏该 block 所属 chunk 区间高亮（不驱动右侧，用户要求）
  const handleHoverBlock = useCallback((blockId: string | null) => {
    setHoverBlockId(blockId);
  }, []);

  // 右侧点击 → 阅读锚定 + 左侧原文滚动闪烁定位
  const handleClickRight = useCallback(
    (id: string) => {
      setReadingChunkId(id);
      const blockId = blockIdByChunkId.get(id);
      if (blockId) setFlashLeftId(`${blockId}#${Date.now()}`);
    },
    [blockIdByChunkId]
  );

  // 章节树跳转 → 左右两栏都跳到对应章节：
  // 左栏滚动+闪烁到章节第一个 block；右栏滚动到该章节第一个有 chunk 映射的块对应的卡片
  // （章节首块常是 heading，不在任何 chunk 的 blockIds 里，需向后扫描兜底）
  const handleJumpTo = useCallback(
    (blockId: string) => {
      setFlashLeftId(blockId + `#${Date.now()}`);
      let chunkId = chunkIdByBlockId.get(blockId);
      if (!chunkId) {
        const start = leftBlocks.findIndex((b) => b.id === blockId);
        if (start >= 0) {
          for (let i = start + 1; i < leftBlocks.length; i++) {
            const cid = chunkIdByBlockId.get(leftBlocks[i].id);
            if (cid) {
              chunkId = cid;
              break;
            }
          }
        }
      }
      if (chunkId) {
        setReadingChunkId(chunkId);
        setScrollToRightId(chunkId + `#${Date.now()}`);
      }
    },
    [chunkIdByBlockId, leftBlocks]
  );

  // 重切 / 入库（LLM 档位混入提交：enableLlm + 合并置信度阈值）
  const chunkMutation = useMutation({
    mutationFn: () =>
      api.post(`/documents/${documentId}/chunk`, {
        config: {
          ...config,
          enableLlm: chunkLlmMode !== "off",
          llmMergeThreshold:
            chunkLlmMode === "conservative"
              ? CONSERVATIVE_MERGE_THRESHOLD
              : 0,
        },
      }),
    onSuccess: () => {
      pushToast({ kind: "info", title: "重新切分已提交" });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "切分失败", message: friendlyError(err) }),
  });

  const indexMutation = useMutation({
    mutationFn: () => api.post(`/documents/${documentId}/index`),
    onSuccess: () => {
      pushToast({ kind: "info", title: "入库任务已提交" });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err) =>
      pushToast({ kind: "error", title: "入库失败", message: friendlyError(err) }),
  });

  const busy =
    currentDoc && ["chunking", "embedding", "parsing"].includes(currentDoc.status);
  const progress = documentId ? taskProgress[documentId] : undefined;

  if (previewableDocs.length === 0 && docsData) {
    return (
      <div className="flex h-full flex-col p-6">
        <h1 className="text-lg font-semibold text-slate-900">切分预览</h1>
        <EmptyState
          title="暂无可预览的文档"
          hint="请先在「文档管理」上传文档并完成解析切分"
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      {/* 顶栏（固定一行）：标题 + 文档选择器 + 参数摘要 + 统计/操作按钮 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-lg font-semibold text-slate-900">切分预览</h1>
        <Select
          value={documentId ?? ""}
          onValueChange={(v) => setDocumentId(v)}
        >
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

        {/* 参数摘要（常驻顶栏） */}
        <span
          className="inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1.5 text-xs text-slate-500 ring-1 ring-gray-200"
          title="当前切分参数"
        >
          <SlidersHorizontal className="h-3.5 w-3.5 text-slate-400" />
          chunk {config.chunkSize} / overlap {config.chunkOverlap}
          {config.enableSemantic ? " / 语义" : ""}
          {chunkLlmMode !== "off" &&
            ` / LLM·${chunkLlmMode === "conservative" ? "保守" : "标准"}`}
          {config.enableParentChild ? " / 父子" : ""}
          {stats && (
            <>
              <span className="mx-0.5 text-gray-200">|</span>
              {stats.current.chunkCount} chunks
            </>
          )}
        </span>

        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            className="text-slate-500"
            onClick={() => setParamOpen((v) => !v)}
            title={paramOpen ? "收起参数面板" : "展开参数面板"}
          >
            {paramOpen ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
            切分参数
          </Button>
          {stats && (
            <Button
              variant="ghost"
              size="sm"
              className="text-slate-500"
              onClick={() => setStatsOpen((v) => !v)}
              title={statsOpen ? "收起统计" : "展开统计"}
            >
              <BarChart3 className="h-3.5 w-3.5" />
              {statsOpen ? "收起统计" : "统计"}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setLlmOpen(true)}
            title="LLM 边界合并增强（关/保守/标准）"
          >
            <Sparkles
              className={`h-3.5 w-3.5 ${chunkLlmMode !== "off" ? "text-violet-500" : ""}`}
            />
            LLM 辅助
            {chunkLlmMode !== "off" && (
              <span className="rounded-full bg-violet-100 px-1.5 text-[10px] text-violet-600">
                {chunkLlmMode === "conservative" ? "保守" : "标准"}
              </span>
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setExportOpen(true)}
            disabled={!documentId}
          >
            <Download className="h-3.5 w-3.5" /> 导出 JSONL
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => chunkMutation.mutate()}
            disabled={!documentId || !!busy || chunkMutation.isPending}
          >
            <RefreshCw className="h-3.5 w-3.5" /> 重新切分
          </Button>
          <Button
            size="sm"
            onClick={() => indexMutation.mutate()}
            disabled={!documentId || !!busy || indexMutation.isPending}
          >
            <Database className="h-3.5 w-3.5" /> 确认入库
          </Button>
        </div>
      </div>

      {/* 任务进度条 */}
      {busy && progress && (
        <div className="flex items-center gap-3 rounded-xl border border-blue-100 bg-blue-50 px-4 py-2">
          <ProgressBar value={progress.progress} className="flex-1" barClassName="bg-blue-500" />
          <span className="text-xs text-blue-600">
            {progress.progress}%{progress.message ? ` · ${progress.message}` : ""}
          </span>
        </div>
      )}
      {excludedSections.length > 0 && (
        <p className="rounded-lg bg-amber-50 px-3 py-1.5 text-xs text-amber-700">
          已在章节树中排除 {excludedSections.length} 个章节（作用于 JSONL 导出过滤；入库范围为整篇文档）
        </p>
      )}

      {/* 参数面板（默认折叠，顶栏「切分参数」按钮控制） */}
      {paramOpen && <ParamPanel open onToggle={() => setParamOpen(false)} />}

      {/* 统计仪表盘（默认折叠，顶栏「统计」按钮控制） */}
      {statsOpen && stats && <StatsDashboard stats={stats} />}

      {/* 左右分栏 */}
      <div className="grid min-h-0 flex-1 grid-cols-2 gap-3">
        {chunksLoading || (busy && currentDoc?.status === "chunking") ? (
          <>
            <ListSkeleton rows={4} />
            <ListSkeleton rows={4} />
          </>
        ) : (
          <>
            <SourcePanel
              blocks={leftBlocks}
              highlightBlockIds={highlightBlockIds}
              flashBlockId={flashLeftId}
              onJumpTo={handleJumpTo}
              onHoverBlock={handleHoverBlock}
            />
            <ChunkList
              chunks={allChunks}
              activeChunkId={displayChunkId}
              scrollToChunkId={scrollToRightId}
              onHover={handleHoverRight}
              onClick={handleClickRight}
            />
          </>
        )}
      </div>

      <ExportDialog
        documentIds={documentId ? [documentId] : []}
        open={exportOpen}
        onOpenChange={setExportOpen}
      />
      <ChunkLLMDialog
        chunkCount={stats?.current.chunkCount}
        open={llmOpen}
        onOpenChange={setLlmOpen}
      />
    </div>
  );
}
