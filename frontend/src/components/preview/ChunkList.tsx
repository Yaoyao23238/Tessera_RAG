/**
 * 右侧 chunk 卡片列表：类型筛选 + 父子角色筛选 + 关键词过滤。
 * 列表按原文文档流排序（见 useChunks.sortByDocumentFlow）；
 * 仅章节树跳转时把对应卡片滚入视口（左侧滚动不驱动本列表）。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Search } from "lucide-react";
import { ChunkCard } from "./ChunkCard";
import { Input } from "../ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { CHUNK_TYPE_META, CHUNK_TYPES, type ChunkType } from "../../lib/constants";
import type { Chunk } from "../../types";

export function ChunkList({
  chunks,
  activeChunkId,
  scrollToChunkId,
  onHover,
  onClick,
}: {
  /** 全量 chunk（含父块与子块），按原文文档流顺序 */
  chunks: Chunk[];
  activeChunkId: string | null;
  /** 章节树跳转联动：需要滚入视口的 chunk（每次变化触发一次滚动） */
  scrollToChunkId: string | null;
  onHover: (id: string | null) => void;
  onClick: (id: string) => void;
}) {
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [roleFilter, setRoleFilter] = useState<string>("all");
  const [keyword, setKeyword] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  // 父块 id → 父块对象（子块卡片展开引用）
  const parentMap = useMemo(() => {
    const m = new Map<string, Chunk>();
    for (const c of chunks) m.set(c.id, c);
    return m;
  }, [chunks]);

  // 作为父块的 id 集合
  const parentIds = useMemo(() => {
    const s = new Set<string>();
    for (const c of chunks) if (c.parentId) s.add(c.parentId);
    return s;
  }, [chunks]);

  const filtered = useMemo(() => {
    return chunks.filter((c) => {
      if (typeFilter !== "all" && c.chunkType !== typeFilter) return false;
      if (roleFilter === "parent" && !parentIds.has(c.id)) return false;
      if (roleFilter === "child" && parentIds.has(c.id)) return false;
      if (keyword && !c.content.toLowerCase().includes(keyword.toLowerCase()))
        return false;
      return true;
    });
  }, [chunks, typeFilter, roleFilter, keyword, parentIds]);

  // 章节树跳转 → 右侧卡片滚入视口（scrollToChunkId 形如 "chunkId#nonce"）
  useEffect(() => {
    const targetId = scrollToChunkId?.split("#")[0];
    if (!targetId || !listRef.current) return;
    const el = listRef.current.querySelector(`[data-chunk-id="${targetId}"]`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [scrollToChunkId]);

  return (
    <div className="flex h-full min-h-0 flex-col rounded-xl border border-gray-200 bg-white">
      {/* 筛选工具行 */}
      <div className="flex items-center gap-2 border-b border-gray-100 px-3 py-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <Input
            className="h-7 pl-7 text-xs"
            placeholder="在 chunk 中搜索…"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
        </div>
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger className="h-7 w-24 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            {CHUNK_TYPES.map((t) => (
              <SelectItem key={t} value={t}>
                {CHUNK_TYPE_META[t as ChunkType].label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={roleFilter} onValueChange={setRoleFilter}>
          <SelectTrigger className="h-7 w-24 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部</SelectItem>
            <SelectItem value="parent">父块</SelectItem>
            <SelectItem value="child">子块</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* 卡片列表 */}
      <div ref={listRef} className="min-h-0 flex-1 space-y-2.5 overflow-y-auto p-3 thin-scroll">
        {filtered.length === 0 ? (
          <p className="py-10 text-center text-xs text-slate-400">无匹配 chunk</p>
        ) : (
          filtered.map((chunk) => (
            <ChunkCard
              key={chunk.id}
              chunk={chunk}
              parentChunk={chunk.parentId ? parentMap.get(chunk.parentId) : null}
              active={activeChunkId === chunk.id}
              onHover={onHover}
              onClick={onClick}
            />
          ))
        )}
      </div>
      <div className="border-t border-gray-100 px-3 py-1.5 text-[10px] text-slate-400">
        {filtered.length} / {chunks.length} 个 chunk
      </div>
    </div>
  );
}
