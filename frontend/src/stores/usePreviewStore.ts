/**
 * 切分预览页客户端状态（Zustand）。
 *
 * 保存当前文档、调参面板配置（未触发重切前的草稿）、联动选中态、
 * 章节树勾选集合。组件卸载不丢失，便于页面切换后回来继续调参。
 */

import { create } from "zustand";
import type { ChunkingConfig } from "../types";

export const DEFAULT_CHUNKING: ChunkingConfig = {
  chunkSize: 512,
  chunkOverlap: 50,
  parentChunkSize: 1024,
  childChunkSize: 256,
  enableParentChild: true,
  enableSemantic: true,
  enableLlm: false,
  llmMergeThreshold: 0,
  semanticThreshold: 0.3,
  tableHandling: "markdown",
  minChunkSize: 50,
  dropEmptyBlocks: true,
};

interface PreviewState {
  documentId: string | null;
  /** 调参草稿（点「重新切分」才提交后端） */
  config: ChunkingConfig;
  /** 当前高亮/选中的 chunk */
  activeChunkId: string | null;
  /** 联动来源：避免左右滚动互相触发死循环 */
  linkSource: "left" | "right" | null;
  /** 章节树勾选（breadcrumb 路径 → 是否入库）；空集合=全选 */
  excludedSections: string[];
  /** 章节树折叠 */
  treeCollapsed: boolean;

  setDocumentId: (id: string | null) => void;
  setConfig: (patch: Partial<ChunkingConfig>) => void;
  resetConfig: (c: ChunkingConfig) => void;
  setActiveChunk: (id: string | null, source: "left" | "right" | null) => void;
  toggleSection: (path: string) => void;
  clearSections: () => void;
  setTreeCollapsed: (v: boolean) => void;
}

export const usePreviewStore = create<PreviewState>((set) => ({
  documentId: null,
  config: DEFAULT_CHUNKING,
  activeChunkId: null,
  linkSource: null,
  excludedSections: [],
  treeCollapsed: false,

  setDocumentId: (id) =>
    set({ documentId: id, activeChunkId: null, excludedSections: [] }),
  setConfig: (patch) => set((s) => ({ config: { ...s.config, ...patch } })),
  resetConfig: (c) => set({ config: c }),
  setActiveChunk: (id, source) => set({ activeChunkId: id, linkSource: source }),
  toggleSection: (path) =>
    set((s) => ({
      excludedSections: s.excludedSections.includes(path)
        ? s.excludedSections.filter((p) => p !== path)
        : [...s.excludedSections, path],
    })),
  clearSections: () => set({ excludedSections: [] }),
  setTreeCollapsed: (v) => set({ treeCollapsed: v }),
}));
