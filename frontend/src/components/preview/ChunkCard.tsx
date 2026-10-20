/**
 * chunk 卡片（右侧面板）：类型色标、章节路径、token 数、内容、
 * 页码/策略来源/父块引用（子块可展开父块内容）。
 */

import { forwardRef, useState } from "react";
import { ChevronDown, ChevronRight, Layers } from "lucide-react";
import {
  CHUNK_TYPE_META,
  STRATEGY_LABELS,
  type ChunkType,
} from "../../lib/constants";
import { MarkdownTable } from "../common/MarkdownTable";
import { usePreviewStore } from "../../stores/usePreviewStore";
import { cn, truncate } from "../../lib/utils";
import type { Chunk } from "../../types";

interface ChunkCardProps {
  chunk: Chunk;
  /** 父块内容（子块卡片可展开查看） */
  parentChunk?: Chunk | null;
  active: boolean;
  onHover: (id: string | null) => void;
  onClick: (id: string) => void;
}

export const ChunkCard = forwardRef<HTMLDivElement, ChunkCardProps>(
  ({ chunk, parentChunk, active, onHover, onClick }, ref) => {
    const [parentOpen, setParentOpen] = useState(false);
    const meta = CHUNK_TYPE_META[chunk.chunkType as ChunkType] ?? {
      label: chunk.chunkType,
      color: "#94A3B8",
    };

    return (
      <div
        ref={ref}
        data-chunk-id={chunk.id}
        className={cn(
          "cursor-pointer rounded-lg border bg-white transition-all",
          active
            ? "border-slate-900 shadow-md ring-1 ring-slate-900/10"
            : "border-gray-200 hover:border-slate-300 hover:shadow-sm"
        )}
        onMouseEnter={() => onHover(chunk.id)}
        onMouseLeave={() => onHover(null)}
        onClick={() => onClick(chunk.id)}
      >
        <div className="flex">
          {/* 类型色条（与解析预览块流一致） */}
          <div
            className="w-1 shrink-0 rounded-l-lg"
            style={{ backgroundColor: meta.color }}
          />
          <div className="min-w-0 flex-1 px-3 py-2.5">
            {/* 元信息行：类型徽章 + 章节路径 + token 数 */}
            <div className="mb-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-slate-400">
              <span
                className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 font-medium"
                style={{ color: meta.color, backgroundColor: `${meta.color}14` }}
              >
                {meta.label}
              </span>
              <span className="truncate text-slate-300" title={chunk.breadcrumb.join(" / ")}>
                {chunk.breadcrumb.join(" / ") || "（无章节）"}
              </span>
              <span className="ml-auto shrink-0 rounded bg-gray-100 px-1.5 py-0.5 tabular-nums">
                {chunk.tokenCount} tok
              </span>
            </div>

            {/* 内容 */}
            <ChunkContent chunk={chunk} />

            {/* 底部：页码 + 策略 + 入库 + 父块引用 */}
            <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-400">
              <span>
                p.{chunk.pageNumber}
                {chunk.endPage > chunk.pageNumber ? `-${chunk.endPage}` : ""}
              </span>
              <span className="rounded bg-gray-100 px-1 py-0.5">
                {STRATEGY_LABELS[chunk.strategy] ?? chunk.strategy}
              </span>
              {chunk.isEmbedded && (
                <span className="rounded bg-emerald-50 px-1 py-0.5 text-emerald-600">已入库</span>
              )}
              {parentChunk && (
                <button
                  type="button"
                  className="ml-auto inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-slate-500 hover:bg-gray-100"
                  onClick={(e) => {
                    e.stopPropagation();
                    setParentOpen((v) => !v);
                  }}
                >
                  <Layers className="h-3 w-3" />
                  父块
                  {parentOpen ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )}
                </button>
              )}
            </div>

            {/* 父块展开引用 */}
            {parentOpen && parentChunk && (
              <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-2.5">
                <p className="mb-1 text-[10px] font-medium text-slate-500">
                  父块（{parentChunk.tokenCount} tok）
                </p>
                <p className="whitespace-pre-wrap text-xs leading-5 text-slate-600">
                  {truncate(parentChunk.content, 600)}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }
);
ChunkCard.displayName = "ChunkCard";

/** 卡片内容区：表格/代码特殊渲染，标题加粗放大，正文与解析预览一致。 */
function ChunkContent({ chunk }: { chunk: Chunk }) {
  // 切分预览右侧表格展示跟随参数配置的「表格处理」形式
  const tableHandling = usePreviewStore((s) => s.config.tableHandling);
  if (chunk.chunkType === "code") {
    return (
      <pre className="mt-2 max-h-40 overflow-hidden rounded-lg bg-slate-900 p-2.5 text-xs text-slate-100">
        <code>{truncate(chunk.content, 800)}</code>
      </pre>
    );
  }
  if (chunk.chunkType === "table") {
    const markdown =
      (chunk.tableData?.markdown as string | undefined) || chunk.content;
    // markdown 模式：表格以 markdown 文本形式参与切分，渲染源码文本
    if (tableHandling === "markdown") {
      return (
        <pre className="mt-2 max-h-40 overflow-hidden rounded-lg bg-gray-50 p-2.5 text-xs leading-5 text-slate-600">
          {truncate(markdown, 800)}
        </pre>
      );
    }
    return (
      <div className="mt-2">
        <MarkdownTable markdown={markdown} />
      </div>
    );
  }
  // 标题 chunk：与解析预览块流的 heading 视觉一致（按层级加粗放大）
  if (chunk.chunkType === "heading") {
    return (
      <p className="line-clamp-4 whitespace-pre-wrap text-lg font-semibold text-slate-900">
        {chunk.content}
      </p>
    );
  }
  return (
    <p className="line-clamp-4 whitespace-pre-wrap text-sm leading-6 text-slate-700">
      {chunk.content}
    </p>
  );
}
