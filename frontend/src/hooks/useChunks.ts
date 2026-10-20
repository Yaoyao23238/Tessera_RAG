/**
 * chunk 相关查询 hooks。
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Chunk, ChunkStatsCompare, PagedList } from "../types";

/**
 * 按原文文档流重排 chunk。
 *
 * chunk_index 是两套独立编号空间（后端有意设计，便于 API role 过滤）：
 * - 顶层块（父块 + 独立块，parentId=null）按文档流统一编号，
 *   父块出现在其组内第一个子块的位置；
 * - 子块（parentId 非空）在 0..M 空间另行编号。
 * 直接按 chunkIndex 排序会把两套空间交错混排（顶层[5] 与 子[5] 相邻），
 * 导致列表相邻块在原文中不相邻。此处重建文档流：
 * 顶层块按顶层编号排序，每个父块后紧跟其子块（按子块编号）。
 */
export function sortByDocumentFlow(chunks: Chunk[]): Chunk[] {
  const childrenByParent = new Map<string, Chunk[]>();
  for (const c of chunks) {
    if (!c.parentId) continue;
    const arr = childrenByParent.get(c.parentId) ?? [];
    arr.push(c);
    childrenByParent.set(c.parentId, arr);
  }
  for (const arr of childrenByParent.values()) {
    arr.sort((a, b) => a.chunkIndex - b.chunkIndex);
  }
  const topLevel = chunks
    .filter((c) => c.parentId === null)
    .sort((a, b) => a.chunkIndex - b.chunkIndex);
  const ordered: Chunk[] = [];
  for (const c of topLevel) {
    ordered.push(c);
    const kids = childrenByParent.get(c.id);
    if (kids) ordered.push(...kids);
  }
  return ordered;
}

/**
 * 拉取某文档的全部 chunk（自动翻页，page_size=500 上限）。
 * 预览页需要全量做左右联动与章节树。
 */
export function useAllChunks(documentId: string | null) {
  return useQuery({
    queryKey: ["chunks", documentId, "all"],
    enabled: !!documentId,
    queryFn: async () => {
      const all: Chunk[] = [];
      let page = 1;
      // 单页上限 500，循环直至取完
      for (;;) {
        const resp = await api.get<PagedList<Chunk>>(
          `/documents/${documentId}/chunks?page=${page}&page_size=500`
        );
        all.push(...resp.items);
        if (all.length >= resp.total || resp.items.length === 0) break;
        page += 1;
      }
      return sortByDocumentFlow(all);
    },
  });
}

/** 切分统计快照（含上一版对比）。 */
export function useChunkStats(documentId: string | null, comparePrev = true) {
  return useQuery({
    queryKey: ["chunk-stats", documentId, comparePrev],
    enabled: !!documentId,
    retry: false,
    queryFn: () =>
      api.get<ChunkStatsCompare>(
        `/documents/${documentId}/chunk-stats${comparePrev ? "?compare=prev" : ""}`
      ),
  });
}
