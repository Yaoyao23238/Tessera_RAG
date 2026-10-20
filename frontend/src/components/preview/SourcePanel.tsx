/**
 * 左侧原文面板：渲染解析 Blocks 原文流（与解析预览页同源同组件），
 * 每个元素标 data-block-id；滚动时顶栏面包屑跟随（IntersectionObserver）。
 *
 * 数据源：/documents/{id}/blocks（解析原始 Blocks），不再用父块 chunk 近似，
 * 保证表格/图片/代码等类型块渲染效果与解析预览页一致。
 * 左侧滚动不驱动右侧 chunk 列表（联动为右→左单向，用户要求）。
 */

import { useEffect, useRef, useState } from "react";
import { SectionTree } from "./SectionTree";
import { BlockCard } from "../parse/BlockCard";
import { usePreviewStore } from "../../stores/usePreviewStore";
import { cn } from "../../lib/utils";
import type { ParseBlock } from "../../types";

export function SourcePanel({
  blocks,
  highlightBlockIds,
  flashBlockId,
  onJumpTo,
  onHoverBlock,
}: {
  /** 解析 Blocks（已按文档流排序，挂好 breadcrumb） */
  blocks: ParseBlock[];
  /** 需要高亮的左侧元素 id 集合（由鼠标悬停驱动，覆盖整个 chunk 区间） */
  highlightBlockIds: Set<string>;
  /** 点击联动时需要闪烁的元素 id（每次变化重新触发闪烁） */
  flashBlockId: string | null;
  /** 章节树点击跳转 */
  onJumpTo: (blockId: string) => void;
  /** 鼠标悬停/离开某个原文块 */
  onHoverBlock: (blockId: string | null) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [currentCrumb, setCurrentCrumb] = useState<string[]>([]);

  // 悬停高亮：highlightBlockIds 变化时刷新高亮集合，不做自动滚动——
  // 滚动定位的场景走 flashBlockId（右侧点击/章节树跳转）。
  // 高亮只由鼠标悬停驱动（父组件保证），滚动阅读时不会有高亮块乱跳。
  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;
    root.querySelectorAll(".block-highlight").forEach((el) => el.classList.remove("block-highlight"));
    for (const id of highlightBlockIds) {
      root.querySelector(`[data-block-id="${id}"]`)?.classList.add("block-highlight");
    }
  }, [highlightBlockIds]);

  // 点击闪烁（flashBlockId 形如 "blockId#nonce"，nonce 用于重复触发）
  useEffect(() => {
    const root = containerRef.current;
    const flashId = flashBlockId?.split("#")[0];
    if (!root || !flashId) return;
    const el = root.querySelector(`[data-block-id="${flashId}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("block-flash");
    const timer = setTimeout(() => el.classList.remove("block-flash"), 1900);
    return () => {
      clearTimeout(timer);
      el.classList.remove("block-flash");
    };
  }, [flashBlockId]);

  // 可见性检测：仅用于面包屑跟随（不再驱动右侧列表）
  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;
    const visible = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const id = (entry.target as HTMLElement).dataset.blockId;
          if (!id) continue;
          if (entry.isIntersecting) {
            visible.set(id, entry.boundingClientRect.top);
          } else {
            visible.delete(id);
          }
        }
        // 取最靠近顶部的可见块
        let first: string | null = null;
        let minTop = Infinity;
        for (const [id, top] of visible) {
          if (top < minTop) {
            minTop = top;
            first = id;
          }
        }
        if (first) {
          const block = blocks.find((b) => b.id === first);
          if (block) setCurrentCrumb(block.breadcrumb);
        }
      },
      { root, threshold: 0.1 }
    );
    const els = root.querySelectorAll("[data-block-id]");
    els.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
    // blocks 引用变化时重建 observer
  }, [blocks]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <SectionTree blocks={blocks} onJump={onJumpTo} />

      <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-gray-200 bg-white">
        {/* 顶栏面包屑跟随 */}
        <div className="border-b border-gray-100 px-4 py-2 text-xs text-slate-500">
          {currentCrumb.length > 0 ? (
            <span className="truncate">{currentCrumb.join(" / ")}</span>
          ) : (
            <span className="text-slate-300">原文</span>
          )}
        </div>
        <div
          ref={containerRef}
          className="min-h-0 flex-1 overflow-y-auto px-6 py-4 thin-scroll"
          onMouseLeave={() => onHoverBlock(null)}
        >
          {blocks.map((block) => (
            <SourceBlockWrapper
              key={block.id}
              block={block}
              onHoverBlock={onHoverBlock}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

/** 单个原文块：直接复用解析预览的 BlockCard，保证渲染一致；
 *  外层仅控制章节排除的透明度 + 悬停上报。 */
function SourceBlockWrapper({
  block,
  onHoverBlock,
}: {
  block: ParseBlock;
  onHoverBlock: (blockId: string | null) => void;
}) {
  const excluded = usePreviewExcluded(block.breadcrumb);
  return (
    <div
      className={cn("mb-3 scroll-mt-4", excluded && "opacity-40")}
      onMouseEnter={() => onHoverBlock(block.id)}
    >
      <BlockCard block={block} />
    </div>
  );
}

/** 判断块的章节路径是否被章节树排除（任一祖先路径被排除即视为排除）。 */
function usePreviewExcluded(breadcrumb: string[]): boolean {
  const excluded = usePreviewStore((s) => s.excludedSections);
  const path = breadcrumb.join("/");
  return excluded.some((p) => path === p || path.startsWith(`${p}/`));
}
