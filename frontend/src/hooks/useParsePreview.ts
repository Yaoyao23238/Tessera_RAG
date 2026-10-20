/**
 * 解析预览页查询 hooks：块列表 / 质量报告。
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ParseBlocksResponse, ParseReport, ParseVariant } from "../types";

/** 解析块列表（全量；variant 缺省由后端取文档当前生效版本）。 */
export function useParseBlocks(
  documentId: string | null,
  variant?: ParseVariant | null
) {
  return useQuery({
    queryKey: ["parse-blocks", documentId, variant ?? "effective"],
    enabled: !!documentId,
    retry: false,
    queryFn: () =>
      api.get<ParseBlocksResponse>(
        `/documents/${documentId}/blocks${variant ? `?variant=${variant}` : ""}`
      ),
  });
}

/** 解析质量报告（统计 + 警告 + 清洗 token 预估）。 */
export function useParseReport(
  documentId: string | null,
  variant?: ParseVariant | null
) {
  return useQuery({
    queryKey: ["parse-report", documentId, variant ?? "effective"],
    enabled: !!documentId,
    retry: false,
    queryFn: () =>
      api.get<ParseReport>(
        `/documents/${documentId}/parse-report${variant ? `?variant=${variant}` : ""}`
      ),
  });
}
