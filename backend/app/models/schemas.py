"""Pydantic v2 请求/响应模型。

字段与前端 ``src/types/index.ts`` 一一对应。API 统一响应格式
``{code, data, message}``，code=0 成功。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, Field, PlainSerializer

T = TypeVar("T")

# 从 SQLite 读回的 datetime 是 naive（SQLite 不保留时区），但业务上存的值是 UTC。
# 序列化时统一补 UTC 时区输出带 Z 的 ISO，前端 new Date() 才能正确转本地时区；
# 否则输出无 Z 字符串，浏览器按本地时区解析，导致显示偏差 8 小时。
UTCDT = Annotated[
    datetime,
    PlainSerializer(
        lambda v: (v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v).isoformat(),
        return_type=str,
        when_used="json",
    ),
]


# ---------------------------------------------------------------------------
# 通用响应
# ---------------------------------------------------------------------------


class ApiResponse(BaseModel, Generic[T]):
    """统一响应包裹：{code, data, message}。"""

    code: int = 0
    data: T | None = None
    message: str = "ok"


# ---------------------------------------------------------------------------
# 切分配置（与 core.types.ChunkingConfig 字段一致）
# ---------------------------------------------------------------------------


class ChunkingConfigSchema(BaseModel):
    chunk_size: int = 512
    chunk_overlap: int = 50
    parent_chunk_size: int = 1024
    child_chunk_size: int = 256
    enable_parent_child: bool = True
    enable_semantic: bool = True
    enable_llm: bool = False
    llm_merge_threshold: float = 0.0
    semantic_threshold: float = 0.3
    table_handling: str = "markdown"
    min_chunk_size: int = 50
    #: 无文本内容的图片/表格/代码块是否直接丢弃（不入库，避免污染检索）
    drop_empty_blocks: bool = True


# ---------------------------------------------------------------------------
# 文档 / Chunk / Task
# ---------------------------------------------------------------------------


class DocumentOut(BaseModel):
    id: str
    filename: str
    file_path: str
    file_hash: str
    file_type: str
    file_size: int
    page_count: int
    status: str
    title: str
    author: str | None = None
    lang: str | None = None
    parse_variant: str = "raw"
    chunk_count: int = 0
    embedded_count: int = 0
    error_message: str | None = None
    created_at: UTCDT
    updated_at: UTCDT

    model_config = {"from_attributes": True}


class ChunkOut(BaseModel):
    id: str
    document_id: str
    parent_id: str | None = None
    chunk_index: int
    content: str
    chunk_type: str
    breadcrumb: list[str] = Field(default_factory=list)
    page_number: int = 0
    end_page: int = 0
    token_count: int = 0
    char_count: int = 0
    strategy: str = "structure"
    bbox: list[float] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    table_data: dict[str, Any] | None = None
    heading_text: str = ""
    embedding_model: str | None = None
    is_embedded: bool = False
    vector_id: str | None = None

    model_config = {"from_attributes": True}


class TaskOut(BaseModel):
    id: str
    document_id: str | None = None
    task_type: str
    status: str
    progress: int = 0
    message: str = ""
    error: str | None = None

    model_config = {"from_attributes": True}


class ChunkRequest(BaseModel):
    """触发切分请求（可覆盖全局切分配置）。"""

    config: ChunkingConfigSchema | None = None


class CleanRequest(BaseModel):
    """LLM 清洗请求：文本清洗三档 + 表格增强开关（至少启用一项）。"""

    text_scope: str = "full"  # off / problem（仅问题块）/ full（全文清洗）
    clean_table: bool = False


class ParseVariantRequest(BaseModel):
    """切换切分链路使用的解析版本（raw/cleaned）。"""

    variant: str = "raw"


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    document_ids: list[str] | None = None
    chunk_types: list[str] | None = None
    use_rerank: bool | None = None  # None 表示跟随全局设置


class SearchResultItem(BaseModel):
    chunk_id: str
    parent_content: str = ""
    matched_child_text: str = ""
    source_file: str = ""
    page_number: int = 0
    breadcrumb: list[str] = Field(default_factory=list)
    chunk_type: str = "text"
    score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None
    rerank_score: float | None = None


class SearchResponseData(BaseModel):
    query: str
    results: list[SearchResultItem]
    total: int
    latency_ms: int
    used_rerank: bool | None = None  # 本次检索实际是否执行精排
    rerank_skipped: str | None = None  # 精排被跳过的原因（如模型未下载）


# ---------------------------------------------------------------------------
# 评测
# ---------------------------------------------------------------------------


class QASetGenerateRequest(BaseModel):
    document_id: str
    count: int = 20


class QAItemOut(BaseModel):
    id: str
    qa_set_id: str
    question: str
    answer: str = ""
    source_chunk_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class QASetOut(BaseModel):
    id: str
    name: str
    source: str
    document_id: str | None = None
    item_count: int = 0
    file_path: str | None = None
    created_at: UTCDT

    model_config = {"from_attributes": True}


class ExperimentCreateRequest(BaseModel):
    qa_set_id: str
    document_ids: list[str]
    chunking_config: ChunkingConfigSchema = Field(
        default_factory=ChunkingConfigSchema
    )
    name: str | None = None


class ExperimentOut(BaseModel):
    id: str
    name: str
    qa_set_id: str
    chunking_config: dict[str, Any] = Field(default_factory=dict)
    embedding_model: str = ""
    vector_table: str = ""
    document_ids: list[str] = Field(default_factory=list)
    recall_at_1: float | None = None
    recall_at_5: float | None = None
    recall_at_10: float | None = None
    mrr: float | None = None
    status: str
    error_message: str | None = None
    created_at: UTCDT
    completed_at: UTCDT | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 统计快照
# ---------------------------------------------------------------------------


class HistogramBin(BaseModel):
    bin_start: float
    bin_end: float
    count: int


class ChunkStatsOut(BaseModel):
    id: str
    document_id: str
    config_hash: str
    version: int
    histogram: list[HistogramBin] = Field(default_factory=list)
    type_counts: dict[str, int] = Field(default_factory=dict)
    mean_tokens: float = 0.0
    median_tokens: float = 0.0
    chunk_count: int = 0
    created_at: UTCDT

    model_config = {"from_attributes": True}


class ChunkStatsCompareOut(BaseModel):
    """统计快照 + 上一版对比。"""

    current: ChunkStatsOut
    previous: ChunkStatsOut | None = None


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    document_ids: list[str]
    format: str = "alpaca"  # alpaca / dify
    sections: list[str] | None = None  # 章节勾选过滤（breadcrumb 路径）
    strip_cot: bool = False  # 剥离 AI 对话存档思维链（【xx 推理过程】…【xx 回复】）


class ExportResponseData(BaseModel):
    file_path: str
    line_count: int
    format: str
    stripped_chunks: int = 0  # 被剔除或内容被清洗的 chunk 数


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------


class AppConfigSchema(BaseModel):
    data_dir: str = ""
    max_file_size_mb: int = 500
    cache_size_limit_gb: float = 2.0


class LLMProviderSchema(BaseModel):
    id: str = ""
    name: str = ""
    provider_type: str = "deepseek"  # deepseek / custom
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model_name: str = "deepseek-v4-flash"
    display_name: str = ""
    is_default: bool = False
    enabled: bool = True


class EmbeddingConfigSchema(BaseModel):
    provider_type: str = "local"  # local / openai_compatible
    model_name: str = "BAAI/bge-m3"
    api_key: str | None = None
    base_url: str | None = None
    dimensions: int = 1024


class RerankerConfigSchema(BaseModel):
    enabled: bool = False
    model_name: str = "BAAI/bge-reranker-v2-m3"
    top_n: int = 20
    #: 精排最低分阈值（0 = 不过滤）。只作用于 rerank_score；
    #: RRF 分是排名倒数和、无绝对语义，不参与阈值判断。
    #: 0.1 校准于 bge-reranker-v2-m3 的 sigmoid 双峰（实测正例 ~0.99 /
    #: 负例 ~0.01），换 reranker 模型需重新校准。
    min_score: float = 0.1


class VectorStoreConfigSchema(BaseModel):
    backend: str = "lancedb"  # sqlite_vec / lancedb / qdrant / pgvector
    connection_url: str | None = None
    collection_name: str = "tessera"
    config: dict[str, Any] = Field(default_factory=dict)


class SettingsOut(BaseModel):
    app: AppConfigSchema
    llm_providers: list[LLMProviderSchema] = Field(default_factory=list)
    embedding: EmbeddingConfigSchema
    reranker: RerankerConfigSchema
    vector_store: VectorStoreConfigSchema
    chunking: ChunkingConfigSchema


class ModelStatusOut(BaseModel):
    """本地模型下载状态。"""

    model_id: str
    kind: str = "embedding"  # embedding / reranker
    size_mb: float = 0.0
    downloaded: bool = False
    downloading: bool = False
    local_path: str | None = None


# ---------------------------------------------------------------------------
# 仪表盘 / 缓存
# ---------------------------------------------------------------------------


class DashboardStatsOut(BaseModel):
    document_count: int = 0
    chunk_count: int = 0
    embedded_count: int = 0
    storage_bytes: int = 0


class CacheStatusOut(BaseModel):
    total_bytes: int = 0
    parse_bytes: int = 0
    chunk_bytes: int = 0
    embed_bytes: int = 0
    limit_bytes: int = 0
