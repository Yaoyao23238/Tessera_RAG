/**
 * 章节树（左侧面板顶部可折叠）：层级展示、点击跳转、勾选控制入库范围。
 *
 * 由解析 Block 的 breadcrumb 路径构建嵌套树（与原文面板同源）。
 */

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, ListTree } from "lucide-react";
import { usePreviewStore } from "../../stores/usePreviewStore";
import { Checkbox } from "../ui/checkbox";
import { cn } from "../../lib/utils";
import type { ParseBlock } from "../../types";

interface TreeNode {
  title: string;
  path: string; // 完整路径，/ 分隔
  children: TreeNode[];
  /** 该章节下第一个可跳转的 block id */
  firstBlockId: string | null;
}

function buildTree(blocks: ParseBlock[]): TreeNode[] {
  const roots: TreeNode[] = [];
  const byPath = new Map<string, TreeNode>();
  for (const b of blocks) {
    let parents = roots;
    let prefix = "";
    for (const title of b.breadcrumb) {
      prefix = prefix ? `${prefix}/${title}` : title;
      let node = byPath.get(prefix);
      if (!node) {
        node = { title, path: prefix, children: [], firstBlockId: null };
        byPath.set(prefix, node);
        parents.push(node);
      }
      if (!node.firstBlockId) node.firstBlockId = b.id;
      parents = node.children;
    }
  }
  return roots;
}

export function SectionTree({
  blocks,
  onJump,
}: {
  /** 原文面板渲染的解析 Blocks */
  blocks: ParseBlock[];
  onJump: (blockId: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const excluded = usePreviewStore((s) => s.excludedSections);
  const toggleSection = usePreviewStore((s) => s.toggleSection);

  const tree = useMemo(() => buildTree(blocks), [blocks]);

  if (tree.length === 0) return null;

  return (
    <div className="rounded-xl border border-gray-200 bg-white">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-xs font-medium text-slate-600"
        onClick={() => setCollapsed((v) => !v)}
      >
        <ListTree className="h-3.5 w-3.5 text-slate-400" />
        章节结构
        {excluded.length > 0 && (
          <span className="rounded-full bg-amber-100 px-1.5 text-[10px] text-amber-700">
            已排除 {excluded.length} 节
          </span>
        )}
        {collapsed ? (
          <ChevronRight className="ml-auto h-3.5 w-3.5 text-slate-400" />
        ) : (
          <ChevronDown className="ml-auto h-3.5 w-3.5 text-slate-400" />
        )}
      </button>
      {!collapsed && (
        <div className="max-h-56 overflow-y-auto border-t border-gray-100 px-2 py-2 thin-scroll">
          {tree.map((node) => (
            <TreeNodeRow
              key={node.path}
              node={node}
              depth={0}
              excluded={excluded}
              onToggle={toggleSection}
              onJump={onJump}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function TreeNodeRow({
  node,
  depth,
  excluded,
  onToggle,
  onJump,
}: {
  node: TreeNode;
  depth: number;
  excluded: string[];
  onToggle: (path: string) => void;
  onJump: (blockId: string) => void;
}) {
  const [open, setOpen] = useState(depth < 1);
  // 父节被排除时子节视为排除（UI 仅展示父层勾选）
  const checked = !excluded.includes(node.path);

  return (
    <div>
      <div
        className={cn(
          "group flex items-center gap-1 rounded-md py-1 pr-1 text-xs hover:bg-gray-50",
          !checked && "opacity-50"
        )}
        style={{ paddingLeft: `${depth * 14 + 4}px` }}
      >
        <Checkbox
          checked={checked}
          onCheckedChange={() => onToggle(node.path)}
          className="h-3.5 w-3.5"
        />
        {node.children.length > 0 ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="p-0.5 text-slate-400"
            aria-label={open ? "折叠" : "展开"}
          >
            {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          </button>
        ) : (
          <span className="w-4" />
        )}
        <button
          type="button"
          className="min-w-0 flex-1 truncate text-left text-slate-700 hover:text-slate-900"
          title={node.title}
          onClick={() => node.firstBlockId && onJump(node.firstBlockId)}
        >
          {node.title}
        </button>
      </div>
      {open &&
        node.children.map((child) => (
          <TreeNodeRow
            key={child.path}
            node={child}
            depth={depth + 1}
            excluded={excluded}
            onToggle={onToggle}
            onJump={onJump}
          />
        ))}
    </div>
  );
}
