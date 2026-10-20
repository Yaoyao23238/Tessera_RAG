/**
 * 解析预览左栏导航：章节树（heading 块构建，点击定位）+
 * 类型筛选 chips（带数量角标）+ 页码跳转 + 块内搜索。
 */

import { useMemo } from "react";
import { ChevronDown, ChevronRight, ListTree, Search } from "lucide-react";
import { useState } from "react";
import { BLOCK_TYPES, BLOCK_TYPE_META } from "../../lib/constants";
import { cn } from "../../lib/utils";
import type { ParseBlock } from "../../types";
import { Input } from "../ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";

interface NavNode {
  block: ParseBlock;
  children: NavNode[];
}

/** 由 heading 块层级构建章节树（level 栈）。 */
function buildHeadingTree(blocks: ParseBlock[]): NavNode[] {
  const roots: NavNode[] = [];
  const stack: NavNode[] = [];
  for (const block of blocks) {
    if (block.type !== "heading") continue;
    const node: NavNode = { block, children: [] };
    const level = block.level > 0 ? block.level : 1;
    while (stack.length > 0) {
      const top = stack[stack.length - 1];
      const topLevel = top.block.level > 0 ? top.block.level : 1;
      if (topLevel >= level) stack.pop();
      else break;
    }
    if (stack.length === 0) roots.push(node);
    else stack[stack.length - 1].children.push(node);
    stack.push(node);
  }
  return roots;
}

export function ParseNavPanel({
  blocks,
  typeFilter,
  onToggleType,
  keyword,
  onKeywordChange,
  onJump,
}: {
  blocks: ParseBlock[];
  typeFilter: Set<string>;
  onToggleType: (type: string) => void;
  keyword: string;
  onKeywordChange: (keyword: string) => void;
  onJump: (blockId: string) => void;
}) {
  const tree = useMemo(() => buildHeadingTree(blocks), [blocks]);

  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const b of blocks) counts[b.type] = (counts[b.type] ?? 0) + 1;
    return counts;
  }, [blocks]);

  const pages = useMemo(() => {
    const set = new Set<number>();
    for (const b of blocks) set.add(b.page);
    return [...set].sort((a, b) => a - b);
  }, [blocks]);

  /** 页码跳转：定位该页第一个块。 */
  const handlePageJump = (value: string) => {
    const page = Number(value);
    const target = blocks.find((b) => b.page === page);
    if (target) onJump(target.id);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      {/* 章节树 */}
      <div className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-gray-200 bg-white thin-scroll">
        <p className="flex items-center gap-1.5 border-b border-gray-100 px-3 py-2 text-xs font-medium text-slate-600">
          <ListTree className="h-3.5 w-3.5 text-slate-400" />
          章节树
          <span className="ml-auto text-[10px] font-normal text-slate-300">
            {tree.length} 个顶级章节
          </span>
        </p>
        {tree.length > 0 ? (
          <div className="px-2 py-2">
            {tree.map((node) => (
              <NavNodeRow key={node.block.id} node={node} depth={0} onJump={onJump} />
            ))}
          </div>
        ) : (
          <p className="px-3 py-3 text-xs text-slate-400">
            未识别到标题结构
          </p>
        )}
      </div>

      {/* 类型筛选 */}
      <div className="rounded-xl border border-gray-200 bg-white">
        <p className="border-b border-gray-100 px-3 py-2 text-xs font-medium text-slate-600">
          类型筛选
          {typeFilter.size > 0 && (
            <button
              type="button"
              className="ml-2 text-[10px] font-normal text-blue-500 hover:text-blue-600"
              onClick={() => typeFilter.forEach((t) => onToggleType(t))}
            >
              清除
            </button>
          )}
        </p>
        <div className="flex flex-wrap gap-1.5 px-3 py-2.5">
          {BLOCK_TYPES.map((type) => {
            const meta = BLOCK_TYPE_META[type];
            const count = typeCounts[type] ?? 0;
            const active = typeFilter.has(type);
            return (
              <button
                key={type}
                type="button"
                onClick={() => onToggleType(type)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] ring-1 transition-colors",
                  active
                    ? "bg-slate-900 text-white ring-slate-900"
                    : "bg-white text-slate-600 ring-gray-200 hover:bg-gray-50"
                )}
              >
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ backgroundColor: active ? "#fff" : meta.color }}
                />
                {meta.label}
                <span className={active ? "text-slate-300" : "text-slate-400"}>
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* 页码跳转 + 块内搜索 */}
      <div className="space-y-2 rounded-xl border border-gray-200 bg-white px-3 py-2.5">
        <Select onValueChange={handlePageJump}>
          <SelectTrigger className="h-8 bg-white text-xs">
            <SelectValue placeholder={`页码跳转（共 ${pages.length || 0} 页）`} />
          </SelectTrigger>
          <SelectContent className="max-h-64">
            {pages.map((p) => (
              <SelectItem key={p} value={String(p)}>
                第 {p} 页
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <Input
            value={keyword}
            onChange={(e) => onKeywordChange(e.target.value)}
            placeholder="块内搜索…"
            className="h-8 pl-8 text-xs"
          />
        </div>
      </div>
    </div>
  );
}

function NavNodeRow({
  node,
  depth,
  onJump,
}: {
  node: NavNode;
  depth: number;
  onJump: (blockId: string) => void;
}) {
  const [open, setOpen] = useState(depth < 1);
  return (
    <div>
      <div
        className="group flex items-center gap-1 rounded-md py-1 pr-1 text-xs hover:bg-gray-50"
        style={{ paddingLeft: `${depth * 14 + 4}px` }}
      >
        {node.children.length > 0 ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="p-0.5 text-slate-400"
            aria-label={open ? "折叠" : "展开"}
          >
            {open ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
          </button>
        ) : (
          <span className="w-4" />
        )}
        <button
          type="button"
          className="min-w-0 flex-1 truncate text-left text-slate-700 hover:text-slate-900"
          title={node.block.text}
          onClick={() => onJump(node.block.id)}
        >
          {node.block.text}
        </button>
        <span className="text-[10px] text-slate-300">p.{node.block.page}</span>
      </div>
      {open &&
        node.children.map((child) => (
          <NavNodeRow
            key={child.block.id}
            node={child}
            depth={depth + 1}
            onJump={onJump}
          />
        ))}
    </div>
  );
}
