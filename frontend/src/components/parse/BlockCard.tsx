/**
 * 解析块卡片：类型色条 + 徽章/页码/字符数/面包屑 + 按类型渲染内容。
 *
 * 表格块的 table_data.rows 为空（解析阶段仅填 markdown），
 * 故表格渲染走自解析 markdown → <table>。
 */

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, ImageIcon } from "lucide-react";
import { BLOCK_TYPE_META } from "../../lib/constants";
import { MarkdownTable } from "../common/MarkdownTable";
import { cn } from "../../lib/utils";
import type { ParseBlock } from "../../types";

/** 长文本折叠阈值（字符数） */
const COLLAPSE_CHARS = 300;

export function BlockCard({
  block,
  highlight,
}: {
  block: ParseBlock;
  /** 搜索关键词（命中行内高亮） */
  highlight?: string;
}) {
  const meta = BLOCK_TYPE_META[block.type] ?? BLOCK_TYPE_META.paragraph;
  const [expanded, setExpanded] = useState(false);

  const isLongText =
    (block.type === "paragraph" || block.type === "list_item") &&
    block.text.length > COLLAPSE_CHARS;

  return (
    <div
      data-block-id={block.id}
      className="scroll-mt-4 rounded-lg border border-gray-200 bg-white transition-shadow hover:shadow-sm"
    >
      <div className="flex">
        {/* 类型色条 */}
        <div
          className="w-1 shrink-0 rounded-l-lg"
          style={{ backgroundColor: meta.color }}
        />
        <div className="min-w-0 flex-1 px-3 py-2.5">
          {/* 元信息行 */}
          <div className="mb-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-slate-400">
            <span
              className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 font-medium"
              style={{ color: meta.color, backgroundColor: `${meta.color}14` }}
            >
              {meta.label}
              {block.type === "heading" && block.level > 0 && (
                <span className="opacity-70">H{block.level}</span>
              )}
            </span>
            <span>
              p.{block.page}
              {block.endPage > block.page && `-${block.endPage}`}
            </span>
            <span>{block.text.length} 字符</span>
            {block.breadcrumb.length > 0 && (
              <span className="truncate text-slate-300" title={block.breadcrumb.join(" / ")}>
                {block.breadcrumb.join(" / ")}
              </span>
            )}
          </div>
          {/* 内容区 */}
          <BlockBody
            block={block}
            expanded={expanded}
            collapse={isLongText}
            highlight={highlight}
          />
          {isLongText && (
            <button
              type="button"
              className="mt-1 inline-flex items-center gap-0.5 text-[11px] text-blue-500 hover:text-blue-600"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? (
                <>
                  <ChevronUp className="h-3 w-3" /> 收起
                </>
              ) : (
                <>
                  <ChevronDown className="h-3 w-3" /> 展开全文（{block.text.length} 字符）
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function BlockBody({
  block,
  expanded,
  collapse,
  highlight,
}: {
  block: ParseBlock;
  expanded: boolean;
  collapse: boolean;
  highlight?: string;
}) {
  switch (block.type) {
    case "heading": {
      const sizes = ["text-xl", "text-lg", "text-base", "text-sm", "text-sm", "text-sm"];
      return (
        <p
          className={cn(
            "font-semibold text-slate-900",
            sizes[Math.min(Math.max(block.level - 1, 0), 5)]
          )}
        >
          <Highlighted text={block.text} keyword={highlight} />
        </p>
      );
    }
    case "table":
      return <MarkdownTable markdown={block.tableData?.markdown || block.text} />;
    case "code":
      return (
        <pre className="max-h-64 overflow-auto rounded-md bg-slate-900 p-3 text-xs leading-5 text-slate-100">
          <code>{block.text}</code>
        </pre>
      );
    case "image":
      return (
        <div className="flex h-24 items-center justify-center gap-2 rounded-md border border-dashed border-purple-200 bg-purple-50 text-xs text-purple-400">
          <ImageIcon className="h-4 w-4" />
          图片块（第 {block.page} 页
          {block.bbox.length === 4 &&
            `，bbox [${block.bbox.map((n) => Math.round(n)).join(", ")}]`}
          ）
        </div>
      );
    case "list_item":
      return (
        <p
          className="whitespace-pre-wrap text-sm leading-6 text-slate-700"
          style={{ paddingLeft: `${Math.min(block.listLevel, 6) * 16}px` }}
        >
          <span className="mr-1 text-slate-400">•</span>
          <Highlighted text={block.text} keyword={highlight} />
        </p>
      );
    default:
      return (
        <p
          className={cn(
            "whitespace-pre-wrap text-sm leading-6 text-slate-700",
            collapse && !expanded && "line-clamp-4"
          )}
        >
          <Highlighted text={block.text} keyword={highlight} />
        </p>
      );
  }
}

/** 搜索关键词行内高亮。 */
function Highlighted({ text, keyword }: { text: string; keyword?: string }) {
  const parts = useMemo(() => {
    if (!keyword) return null;
    const idx = text.toLowerCase().indexOf(keyword.toLowerCase());
    if (idx < 0) return null;
    return [text.slice(0, idx), text.slice(idx, idx + keyword.length), text.slice(idx + keyword.length)];
  }, [text, keyword]);
  if (!parts) return <>{text}</>;
  return (
    <>
      {parts[0]}
      <mark className="rounded bg-amber-100 px-0.5 text-inherit">{parts[1]}</mark>
      {parts[2]}
    </>
  );
}

