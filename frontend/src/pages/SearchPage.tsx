/**
 * 检索测试页：query 输入回车搜索、结果卡片（高亮/分数条/排名）、
 * Rerank 开关状态徽标、top_k 调节、检索历史下拉。
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search as SearchIcon, History, Loader2 } from "lucide-react";
import { api, friendlyError } from "../lib/api";
import { ResultCard } from "../components/search/ResultCard";
import { RerankBadge } from "../components/search/RerankBadge";
import { SearchFilters } from "../components/search/SearchFilters";
import { EmptyState, ErrorState } from "../components/common/States";
import { ListSkeleton } from "../components/ui/skeleton";
import { Input } from "../components/ui/input";
import { Button } from "../components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { useTaskStore } from "../stores/useTaskStore";
import type { SearchResponseData, Settings } from "../types";

const HISTORY_KEY = "tessera-search-history";

function loadHistory(): string[] {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]") as string[];
  } catch {
    return [];
  }
}

export default function SearchPage() {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState("10");
  const [rerankOverride, setRerankOverride] = useState<boolean | null>(null);
  const [history, setHistory] = useState<string[]>(loadHistory);
  // 检索范围筛选（PRD P0-8）：空数组 = 不过滤
  const [docIds, setDocIds] = useState<string[]>([]);
  const [chunkTypes, setChunkTypes] = useState<string[]>([]);
  const pushToast = useTaskStore((s) => s.pushToast);
  const hasFilter = docIds.length > 0 || chunkTypes.length > 0;

  // 全局设置（Rerank 默认状态）
  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<Settings>("/settings"),
    staleTime: 30_000,
  });

  const { data, isFetching, error, refetch } = useQuery({
    queryKey: ["search", query, topK, rerankOverride, docIds, chunkTypes],
    enabled: query.length > 0,
    retry: false,
    queryFn: () =>
      api.post<SearchResponseData>("/search", {
        query,
        topK: Number(topK),
        useRerank: rerankOverride,
        // 空数组传 null（后端 falsy 忽略，语义 = 不过滤）
        documentIds: docIds.length > 0 ? docIds : null,
        chunkTypes: chunkTypes.length > 0 ? chunkTypes : null,
      }),
  });

  function submit(q: string) {
    const trimmed = q.trim();
    if (!trimmed) return;
    setQuery(trimmed);
    setHistory((prev) => {
      const next = [trimmed, ...prev.filter((h) => h !== trimmed)].slice(0, 10);
      localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
      return next;
    });
  }

  const maxScore = Math.max(...(data?.results.map((r) => r.score) ?? [0]));

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6 thin-scroll">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-900">检索测试</h1>
        <RerankBadge
          globalEnabled={settings?.reranker.enabled ?? false}
          override={rerankOverride}
          onOverrideChange={setRerankOverride}
        />
      </div>

      {/* 搜索栏 */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <SearchIcon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input
            className="h-11 rounded-xl bg-white pl-9 text-sm shadow-sm"
            placeholder="输入查询，回车或点「检索」（稠密+稀疏混合，RRF 融合）"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit(input);
            }}
          />
        </div>
        {/* 可见检索按钮：触屏用户无键盘时回车难触发 */}
        <Button className="h-11" onClick={() => submit(input)}>
          <SearchIcon className="mr-1 h-4 w-4" />
          检索
        </Button>
        <Select value={topK} onValueChange={setTopK}>
          <SelectTrigger className="h-11 w-24 bg-white">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {["5", "10", "20", "50"].map((v) => (
              <SelectItem key={v} value={v}>
                top {v}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {history.length > 0 && (
          <Select
            value=""
            onValueChange={(v) => {
              setInput(v);
              submit(v);
            }}
          >
            <SelectTrigger className="h-11 w-28 bg-white">
              <History className="mr-1 h-3.5 w-3.5 text-slate-400" />
              <SelectValue placeholder="历史" />
            </SelectTrigger>
            <SelectContent>
              {history.map((h) => (
                <SelectItem key={h} value={h}>
                  {h}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {/* 检索范围筛选（PRD P0-8） */}
      <SearchFilters
        docIds={docIds}
        chunkTypes={chunkTypes}
        onDocIdsChange={setDocIds}
        onChunkTypesChange={setChunkTypes}
      />

      {/* 结果区 */}
      {isFetching ? (
        <div className="flex flex-col gap-2">
          <p className="flex items-center gap-2 text-xs text-slate-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> 检索中…
          </p>
          <ListSkeleton rows={3} />
        </div>
      ) : error ? (
        <ErrorState
          message={friendlyError(error)}
          onRetry={() => {
            if (query) void refetch();
            else pushToast({ kind: "info", title: "请先输入查询" });
          }}
        />
      ) : !data ? (
        <EmptyState
          title="输入查询开始检索"
          hint="混合检索链路：稠密 + 稀疏 → RRF 融合 → Rerank（可选）→ 父块展开"
        />
      ) : data.results.length === 0 ? (
        hasFilter ? (
          // 有筛选条件时优先提示筛选问题（阈值/无召回的判断被筛选干扰）
          <EmptyState
            title="当前筛选无结果"
            hint="所选文档/内容类型范围内没有命中，可清空筛选或放宽范围后重试"
          />
        ) : data.thresholdDropped > 0 ? (
          // 候选存在但全在精排相关性门槛之下（全库无答案的查询属此类）。
          // 与「无召回结果」语义不同，也与弱命中（命中但分数低、仍渲染卡片）不同。
          // 注意 N 含未精排就被丢弃的 tail（超出精排窗口的候选），故不说"精排后均低于"。
          <EmptyState
            title="无足够相关的结果"
            hint={`${data.thresholdDropped} 条候选未通过相关性门槛。库中可能没有对应内容；如需放宽，可在「设置 → Rerank」调低最低分阈值`}
          />
        ) : (
          <EmptyState
            title="无召回结果"
            hint="确认已有文档完成入库（状态为「已入库」）"
          />
        )
      ) : (
        <>
          {data.rerankSkipped && (
            <p className="flex items-center gap-1.5 rounded-lg bg-amber-50 px-3 py-1.5 text-xs text-amber-700">
              {data.rerankSkipped}（可在「设置 → Rerank」中下载模型后启用）
            </p>
          )}
          <p className="text-xs text-slate-400">
            {data.total} 条结果 · 耗时 {data.latencyMs} ms
            {data.usedRerank ? " · 已精排" : " · 未精排"}
          </p>
          <div className="space-y-3">
            {data.results.map((item) => (
              <ResultCard
                key={item.chunkId}
                item={item}
                query={data.query}
                maxScore={maxScore}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
