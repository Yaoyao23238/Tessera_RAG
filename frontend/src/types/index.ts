/**
 * 与后端 models/schemas.py 一一对应的 TS 类型。
 */

import type {
  ChunkType,
  DocStatus,
  ExpStatus,
  ExportFormat,
  TaskStatus,
  VectorBackend,
} from "../lib/constants";

export interface Document {
  id: string;
  filename: string;
  filePath: string;
  fileHash: string;
  fileType: string;
  fileSize: number;
  pageCount: number;
  status: DocStatus;
  title: string;
  author: string | null;
  lang: string | null;
  /** 切分链路使用的解析版本（raw/cleaned） */
  parseVariant: ParseVariant;
  chunkCount: number;
  embeddedCount: number;
  errorMessage: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface Chunk {
  id: string;
  documentId: string;
  parentId: string | null;
  chunkIndex: number;
  content: string;
  chunkType: ChunkType;
  breadcrumb: string[];
  pageNumber: number;
  endPage: number;
  tokenCount: number;
  charCount: number;
  strategy: string;
  bbox: number[];
  blockIds: string[];
  tableData: { markdown?: string; rows?: string[][]; headers?: string[] } | null;
  headingText: string;
  embeddingModel: string | null;
  isEmbedded: boolean;
  vectorId: string | null;
}

export interface Task {
  id: string;
  documentId: string | null;
  taskType: string;
  status: TaskStatus;
  progress: number;
  message: string;
  error: string | null;
}

export interface ChunkingConfig {
  chunkSize: number;
  chunkOverlap: number;
  parentChunkSize: number;
  childChunkSize: number;
  enableParentChild: boolean;
  enableSemantic: boolean;
  enableLlm: boolean;
  /** LLM 合并置信度阈值：0=标准档（should_merge 即合），0.75=保守档 */
  llmMergeThreshold: number;
  semanticThreshold: number;
  tableHandling: string;
  minChunkSize: number;
  /** 无文本内容的图片/表格块直接丢弃，不入库（避免污染检索） */
  dropEmptyBlocks: boolean;
}

export interface SearchResultItem {
  chunkId: string;
  parentContent: string;
  matchedChildText: string;
  sourceFile: string;
  pageNumber: number;
  breadcrumb: string[];
  chunkType: ChunkType;
  score: number;
  denseRank: number | null;
  sparseRank: number | null;
  /** 各通道原始分：稠密相似度 / FTS BM25 分。RRF 分是排名倒数和、无绝对语义 */
  denseScore: number | null;
  sparseScore: number | null;
  rerankScore: number | null;
}

export interface SearchResponseData {
  query: string;
  results: SearchResultItem[];
  total: number;
  latencyMs: number;
  usedRerank: boolean | null;
  rerankSkipped: string | null;
  /** 被 min_score 阈值滤掉的候选数；>0 且 results 为空 → 显示「无足够相关的结果」 */
  thresholdDropped: number;
}

export interface QASet {
  id: string;
  name: string;
  source: "upload" | "llm_generated";
  documentId: string | null;
  itemCount: number;
  filePath: string | null;
  createdAt: string;
}

export interface QAItem {
  id: string;
  qaSetId: string;
  question: string;
  answer: string;
  sourceChunkId: string | null;
  meta: Record<string, unknown>;
}

export interface Experiment {
  id: string;
  name: string;
  qaSetId: string;
  chunkingConfig: Record<string, unknown>;
  embeddingModel: string;
  vectorTable: string;
  documentIds: string[];
  recallAt1: number | null;
  recallAt5: number | null;
  recallAt10: number | null;
  mrr: number | null;
  status: ExpStatus;
  errorMessage: string | null;
  createdAt: string;
  completedAt: string | null;
}

export interface HistogramBin {
  binStart: number;
  binEnd: number;
  count: number;
}

export interface ChunkStats {
  id: string;
  documentId: string;
  configHash: string;
  version: number;
  histogram: HistogramBin[];
  typeCounts: Record<string, number>;
  meanTokens: number;
  medianTokens: number;
  chunkCount: number;
  createdAt: string;
}

export interface ChunkStatsCompare {
  current: ChunkStats;
  previous: ChunkStats | null;
}

export interface ExportRequest {
  documentIds: string[];
  format: ExportFormat;
  sections: string[] | null;
}

export interface LLMProvider {
  id: string;
  name: string;
  providerType: "deepseek" | "custom";
  baseUrl: string;
  apiKey: string;
  modelName: string;
  displayName: string;
  isDefault: boolean;
  enabled: boolean;
}

export interface EmbeddingConfig {
  providerType: "local" | "openai_compatible";
  modelName: string;
  apiKey: string | null;
  baseUrl: string | null;
  dimensions: number;
}

export interface RerankerConfig {
  enabled: boolean;
  modelName: string;
  topN: number;
  /** 精排最低分阈值；0 = 不过滤（只作用于 rerank 分） */
  minScore: number;
}

export interface VectorStoreConfig {
  backend: VectorBackend;
  connectionUrl: string | null;
  collectionName: string;
  config: Record<string, unknown>;
}

export interface ModelStatus {
  modelId: string;
  kind: "embedding" | "reranker" | "docling";
  sizeMb: number;
  downloaded: boolean;
  downloading: boolean;
  localPath: string | null;
  /** 模型描述（后端 /settings/models 附加字段） */
  description?: string;
}

export interface AppConfig {
  dataDir: string;
  maxFileSizeMb: number;
  cacheSizeLimitGb: number;
}

/** 全量设置（GET /api/settings）。 */
export interface Settings {
  app: AppConfig;
  llmProviders: LLMProvider[];
  embedding: EmbeddingConfig;
  reranker: RerankerConfig;
  vectorStore: VectorStoreConfig;
  chunking: ChunkingConfig;
  /** 模型下载源（huggingface / hf_mirror / modelscope） */
  download: { source: string };
}

export interface CacheStatus {
  totalBytes: number;
  parseBytes: number;
  chunkBytes: number;
  embedBytes: number;
  limitBytes: number;
}

export interface DashboardStats {
  documentCount: number;
  chunkCount: number;
  embeddedCount: number;
  storageBytes: number;
}

/** 分页列表响应（文档/chunk 通用）。 */
export interface PagedList<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

/** 上传单文件结果项（POST /documents/upload data.results[]）。 */
export interface UploadResultItem {
  filename: string;
  status: "created" | "exists" | "rejected";
  reason?: string;
  code?: number;
  documentId?: string;
  document?: Document;
}

// ---------------------------------------------------------------------------
// 解析预览（/documents/{id}/blocks、/parse-report）
// ---------------------------------------------------------------------------

/** 解析版本：raw（Docling 原始）/ cleaned（LLM 清洗版）。 */
export type ParseVariant = "raw" | "cleaned";

/** 解析块（后端 core.types.Block 的 camelCase 形态）。 */
export interface ParseBlock {
  id: string;
  type: "heading" | "paragraph" | "table" | "code" | "image" | "list_item";
  text: string;
  /** 标题层级 1-6（仅 heading） */
  level: number;
  page: number;
  endPage: number;
  bbox: number[];
  tableData: { markdown?: string; rows?: string[][]; headers?: string[] } | null;
  listLevel: number;
  breadcrumb: string[];
}

/** LLM 清洗元信息（写清洗缓存时记录）。 */
export interface CleanMeta {
  promptVersion?: string;
  usage?: {
    promptTokens?: number;
    completionTokens?: number;
    totalTokens?: number;
  };
  batches?: number;
  failedBatches?: number;
  modelName?: string;
  /** 文本清洗档位：off / problem / full */
  textScope?: string;
  cleanTable?: boolean;
  createdAt?: string;
}

export interface ParseBlocksResponse {
  variant: ParseVariant;
  blocks: ParseBlock[];
  cleanedMeta: CleanMeta | null;
}

export interface QualityWarning {
  kind: "no_heading" | "short_blocks" | "repeated_lines" | "garbled_text";
  severity: "info" | "warning" | "danger";
  message: string;
  suggestion: string;
  /** open_clean_dialog / reparse / "" */
  action: string;
  detail: Record<string, unknown>;
}

export interface ParseReport {
  totalBlocks: number;
  totalChars: number;
  pageCount: number;
  typeCounts: Record<string, number>;
  estTokensText: number;
  estTokensProblem: number;
  estTokensTable: number;
  warnings: QualityWarning[];
  variant: ParseVariant;
  cleanedMeta: CleanMeta | null;
  hasCleaned: boolean;
}

/** 原文面板渲染用的文档元素（由 parse 产物 Block 或 chunk 派生）。 */
export interface SourceBlock {
  id: string;
  type: "heading" | "paragraph" | "table" | "code" | "image" | "list_item";
  text: string;
  level: number;
  page: number;
  breadcrumb: string[];
  tableData?: { markdown?: string; rows?: string[][]; headers?: string[] } | null;
  /** 关联的 chunk id 列表（用于左右联动） */
  chunkIds: string[];
}

/** WS 任务进度 payload。 */
export interface TaskProgressPayload {
  status: string;
  progress: number;
  message: string;
}

/** WS 模型下载 payload（downloadedMb=-1 表示失败）。 */
export interface ModelDownloadPayload {
  model: string;
  downloadedMb: number;
  totalMb: number;
}

/** WS 实验进度 payload。 */
export interface ExperimentProgressPayload {
  current: number;
  total: number;
}
