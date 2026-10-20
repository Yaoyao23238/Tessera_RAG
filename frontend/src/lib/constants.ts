/**
 * 前后端共用常量（与 backend/app/models/db.py 保持一致）。
 */

// 文档状态机（backend: DocStatus）
export const DOC_STATUS = [
  "uploaded",
  "parsing",
  "parsed",
  "chunking",
  "chunked",
  "embedding",
  "indexed",
  "encrypted",
  "parse_failed",
  "chunk_failed",
  "embed_failed",
  "store_failed",
] as const;
export type DocStatus = (typeof DOC_STATUS)[number];

// chunk 类型枚举（backend: ChunkTypeStr）
export const CHUNK_TYPES = ["text", "table", "code", "image", "heading"] as const;
export type ChunkType = (typeof CHUNK_TYPES)[number];

// 任务状态
export const TASK_STATUS = ["pending", "running", "completed", "failed"] as const;
export type TaskStatus = (typeof TASK_STATUS)[number];

// 实验状态
export const EXP_STATUS = ["pending", "running", "completed", "failed"] as const;
export type ExpStatus = (typeof EXP_STATUS)[number];

// 向量库后端
export const VECTOR_BACKENDS = ["sqlite_vec", "lancedb", "qdrant", "pgvector"] as const;
export type VectorBackend = (typeof VECTOR_BACKENDS)[number];

// 导出模板
export const EXPORT_FORMATS = ["alpaca", "dify"] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

// WebSocket 消息类型（共享知识 #3）
export const WS_MSG = {
  TASK_PROGRESS: "task_progress",
  MODEL_DOWNLOAD: "model_download",
  EXPERIMENT_PROGRESS: "experiment_progress",
} as const;

// API 基址（开发环境经 vite 代理）
export const API_BASE = "/api";
export const WS_BASE = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

// LanceDB 表命名约定（共享知识 #4）
export const MAIN_VECTOR_TABLE = "tessera_chunks";

// 支持的上传格式（与后端 SUPPORTED_EXTENSIONS 对齐）
export const SUPPORTED_FORMATS = [".pdf", ".docx", ".pptx", ".md", ".txt", ".html"];

// 文档状态展示元数据：文案 + 圆点颜色
export const DOC_STATUS_META: Record<
  DocStatus,
  { label: string; color: string; spin?: boolean }
> = {
  uploaded: { label: "已上传", color: "#94A3B8" },
  parsing: { label: "解析中", color: "#3B82F6", spin: true },
  parsed: { label: "已解析", color: "#0EA5E9" },
  chunking: { label: "切分中", color: "#3B82F6", spin: true },
  chunked: { label: "已切分", color: "#8B5CF6" },
  embedding: { label: "向量化中", color: "#3B82F6", spin: true },
  indexed: { label: "已入库", color: "#10B981" },
  encrypted: { label: "已加密", color: "#F59E0B" },
  parse_failed: { label: "解析失败", color: "#EF4444" },
  chunk_failed: { label: "切分失败", color: "#EF4444" },
  embed_failed: { label: "向量化失败", color: "#EF4444" },
  store_failed: { label: "写库失败", color: "#EF4444" },
};

// chunk 类型展示元数据：文案 + 色标（PRD：正文蓝/表格绿/代码橙/图片紫）
export const CHUNK_TYPE_META: Record<ChunkType, { label: string; color: string }> = {
  text: { label: "正文", color: "#3B82F6" },
  table: { label: "表格", color: "#10B981" },
  code: { label: "代码", color: "#F59E0B" },
  image: { label: "图片", color: "#8B5CF6" },
  heading: { label: "标题", color: "#64748B" },
};

// 解析块类型（backend: BlockType，比 chunk 多 list_item）
export const BLOCK_TYPES = [
  "heading",
  "paragraph",
  "table",
  "code",
  "image",
  "list_item",
] as const;
export type BlockTypeName = (typeof BLOCK_TYPES)[number];

// 解析块类型展示元数据：与 chunk 色标保持一致（解析预览页）
export const BLOCK_TYPE_META: Record<
  BlockTypeName,
  { label: string; color: string }
> = {
  heading: { label: "标题", color: "#64748B" },
  paragraph: { label: "正文", color: "#3B82F6" },
  table: { label: "表格", color: "#10B981" },
  code: { label: "代码", color: "#F59E0B" },
  image: { label: "图片", color: "#8B5CF6" },
  list_item: { label: "列表", color: "#0EA5E9" },
};

// 切分策略标签
export const STRATEGY_LABELS: Record<string, string> = {
  structure: "结构",
  semantic: "语义",
  llm: "LLM",
  recursive: "递归",
};

// 表格处理方式
export const TABLE_HANDLING_OPTIONS = [
  { value: "preserve", label: "保留表格" },
  { value: "markdown", label: "转 Markdown" },
  { value: "skip", label: "跳过" },
] as const;

// 向量库档位说明
export const VECTOR_BACKEND_META: Record<VectorBackend, { label: string; desc: string }> = {
  sqlite_vec: { label: "sqlite-vec", desc: "单文件兜底档，零依赖" },
  lancedb: { label: "LanceDB", desc: "默认档，原生 BM25+向量混合检索" },
  qdrant: { label: "Qdrant", desc: "中档，需独立服务" },
  pgvector: { label: "pgvector", desc: "大档，需 PostgreSQL" },
};
