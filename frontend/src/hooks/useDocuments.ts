/**
 * 文档相关查询 hooks。
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Document, PagedList } from "../types";

/** 文档分页列表。 */
export function useDocuments(page = 1, pageSize = 50, status?: string) {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (status) params.set("status", status);
  return useQuery({
    queryKey: ["documents", page, pageSize, status ?? ""],
    queryFn: () => api.get<PagedList<Document>>(`/documents?${params}`),
    refetchInterval: (query) => {
      // 有进行中状态的文档时轮询兜底（WS 之外的保险）
      const items = query.state.data?.items ?? [];
      const active = items.some((d) =>
        ["uploaded", "parsing", "chunking", "embedding"].includes(d.status)
      );
      return active ? 3000 : false;
    },
  });
}

/** 单个文档详情。 */
export function useDocument(documentId: string | null) {
  return useQuery({
    queryKey: ["documents", "detail", documentId],
    queryFn: () => api.get<Document>(`/documents/${documentId}`),
    enabled: !!documentId,
    refetchInterval: (query) => {
      // 中间态轮询兜底（WS 之外的保险），与列表页口径一致
      const status = query.state.data?.status ?? "";
      return ["uploaded", "parsing", "chunking", "embedding"].includes(status)
        ? 3000
        : false;
    },
  });
}
