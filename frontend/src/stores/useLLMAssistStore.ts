/**
 * LLM 辅助档位偏好（Zustand + localStorage 持久化）。
 *
 * 两页共用：
 * - 解析预览「LLM 辅助」：文本清洗三档 + 表格增强开关
 * - 切分预览「LLM 辅助」：边界合并增强三档
 *
 * 用户选择跨会话保留，重开弹窗不重置。
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

/** 解析清洗档位：关 / 仅问题块（省 token）/ 全文清洗 */
export type CleanScope = "off" | "problem" | "full";

/** 切分 LLM 增强档位：关 / 保守（仅高置信合并）/ 标准 */
export type ChunkLlmMode = "off" | "conservative" | "standard";

/** 保守档合并置信度阈值（与后端 llm_merge_threshold 口径一致） */
export const CONSERVATIVE_MERGE_THRESHOLD = 0.75;

interface LLMAssistState {
  // 解析预览
  cleanScope: CleanScope;
  cleanTable: boolean;
  // 切分预览
  chunkLlmMode: ChunkLlmMode;

  setCleanScope: (scope: CleanScope) => void;
  setCleanTable: (v: boolean) => void;
  setChunkLlmMode: (mode: ChunkLlmMode) => void;
}

export const useLLMAssistStore = create<LLMAssistState>()(
  persist(
    (set) => ({
      cleanScope: "full",
      cleanTable: false,
      chunkLlmMode: "off",

      setCleanScope: (cleanScope) => set({ cleanScope }),
      setCleanTable: (cleanTable) => set({ cleanTable }),
      setChunkLlmMode: (chunkLlmMode) => set({ chunkLlmMode }),
    }),
    { name: "tessera-llm-assist" }
  )
);
