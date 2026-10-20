/**
 * 检索结果卡片：关键词高亮、RRF 分数进度条、dense/sparse 排名、
 * 页码/面包屑/来源文件、点击展开父块完整上下文、Rerank 分数徽标。
 */

import { useState } from "react";
import { ChevronDown, ChevronRight, FileText } from "lucide-react";
import { Badge } from "../ui/card";
import { ProgressBar } from "../ui/skeleton";
import { CHUNK_TYPE_META, type ChunkType } from "../../lib/constants";
import { cn, highlightParts } from "../../lib/utils";
import type { SearchResultItem } from "../../types";

export function ResultCard({
  item,
  query,
  maxScore,
}: {
  item: SearchResultItem;
  query: string;
  /** 本批结果最高分（用于分数条归一化） */
  maxScore: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const meta = CHUNK_TYPE_META[item.chunkType as ChunkType] ?? {
    label: item.chunkType,
    color: "#94A3B8",
  };
  const scorePct = maxScore > 0 ? Math.round((item.score / maxScore) * 100) : 0;

  return (
    <div
      className={cn(
        "cursor-pointer rounded-xl border bg-white p-4 transition-all",
        expanded ? "border-slate-400 shadow-sm" : "border-gray-200 hover:border-slate-300"
      )}
      onClick={() => setExpanded((v) => !v)}
    >
      {/* 头部：类型 + 来源 + 面包屑 */}
      <div className="flex items-center gap-2">
        <Badge color={meta.color}>{meta.label}</Badge>
        <span className="inline-flex items-center gap-1 text-xs text-slate-500">
          <FileText className="h-3 w-3" />
          {item.sourceFile}
        </span>
        {item.pageNumber > 0 && (
          <span className="text-xs text-slate-400">P{item.pageNumber}</span>
        )}
        {item.rerankScore != null && (
          <span className="rounded bg-purple-50 px-1.5 py-0.5 text-[10px] text-purple-600">
            Rerank {item.rerankScore.toFixed(3)}
          </span>
        )}
        {item.denseScore != null && (
          <span className="rounded bg-sky-50 px-1.5 py-0.5 text-[10px] text-sky-600">
            稠密 {item.denseScore.toFixed(3)}
          </span>
        )}
        {item.sparseScore != null && (
          <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-600">
            稀疏 {item.sparseScore.toFixed(3)}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1 text-[10px] text-slate-400">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </span>
      </div>

      {item.breadcrumb.length > 0 && (
        <p className="mt-1 truncate text-xs text-slate-400">
          {item.breadcrumb.join(" / ")}
        </p>
      )}

      {/* 命中片段（关键词高亮） */}
      <p className="mt-2 text-sm leading-6 text-slate-700">
        <Highlighted text={item.matchedChildText || item.parentContent} query={query} />
      </p>

      {/* 分数行 */}
      <div className="mt-3 flex items-center gap-3">
        <span className="w-14 text-[10px] text-slate-400">RRF 分数</span>
        <ProgressBar value={scorePct} className="w-40" />
        <span className="text-xs tabular-nums text-slate-500">
          {item.score.toFixed(4)}
        </span>
        <span className="ml-auto text-[10px] text-slate-400">
          {item.denseRank != null && `稠密 #${item.denseRank}`}
          {item.denseRank != null && item.sparseRank != null && " · "}
          {item.sparseRank != null && `稀疏 #${item.sparseRank}`}
        </span>
      </div>

      {/* 展开：父块完整上下文 */}
      {expanded && item.parentContent && (
        <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 p-3">
          <p className="mb-1 text-[10px] font-medium text-slate-500">
            父块完整上下文
          </p>
          <p className="max-h-72 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-slate-600 thin-scroll">
            <Highlighted text={item.parentContent} query={query} />
          </p>
        </div>
      )}
    </div>
  );
}

/** 关键词高亮渲染。 */
export function Highlighted({ text, query }: { text: string; query: string }) {
  const parts = highlightParts(text, query);
  return (
    <>
      {parts.map((p, i) =>
        p.hit ? (
          <mark key={i} className="search-hit">
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        )
      )}
    </>
  );
}
