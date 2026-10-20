"""切分引擎共享数据结构。

说明：DESIGN 文件列表中未单列本模块；为让 core 层保持纯 Python
（不依赖 SQLAlchemy / Pydantic，进程池可 pickle，单元测试零外部依赖），
将 Block / Chunk / ChunkingConfig 等数据载体集中在此定义。
ORM（models/db.py）与 schema（models/schemas.py）字段与此一一对应。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


def _new_id() -> str:
    return uuid.uuid4().hex


class BlockType(str, Enum):
    """归一化后的文档元素类型（阶段一产物）。"""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    CODE = "code"
    IMAGE = "image"
    LIST_ITEM = "list_item"


class ChunkType(str, Enum):
    """chunk 类型枚举（共享知识 #6）。"""

    TEXT = "text"
    TABLE = "table"
    CODE = "code"
    IMAGE = "image"
    HEADING = "heading"


@dataclass
class Block:
    """归一化文档元素（阶段一输出）。"""

    id: str = ""
    type: BlockType = BlockType.PARAGRAPH
    text: str = ""
    level: int = 0  # 标题层级 1-6（仅 heading）
    page: int = 0
    end_page: int = 0
    bbox: list[float] = field(default_factory=list)
    table_data: dict[str, Any] | None = None
    list_level: int = 0
    breadcrumb: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id()
        if not self.end_page:
            self.end_page = self.page
        if isinstance(self.type, str):
            self.type = BlockType(self.type)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Block":
        return cls(
            id=d.get("id", ""),
            type=BlockType(d.get("type", "paragraph")),
            text=d.get("text", ""),
            level=d.get("level", 0),
            page=d.get("page", 0),
            end_page=d.get("end_page", 0),
            bbox=list(d.get("bbox", [])),
            table_data=d.get("table_data"),
            list_level=d.get("list_level", 0),
            breadcrumb=list(d.get("breadcrumb", [])),
        )


@dataclass
class ChunkingConfig:
    """切分参数（与 schemas.ChunkingConfigSchema 字段一致）。

    config_hash 口径（共享知识 #5）：chunk_size / overlap / enable_semantic /
    enable_llm / llm_merge_threshold / enable_parent_child / table_handling /
    semantic_threshold / min_chunk_size / drop_empty_blocks /
    parent_chunk_size / child_chunk_size
    + CHUNKER_VERSION（切分引擎逻辑变更时递增，使旧切分缓存失效）。
    """

    #: 切分引擎版本（编号/顺序等逻辑变更时递增，混入 config_hash 使旧缓存失效）
    CHUNKER_VERSION = "empty-img-early-1"

    chunk_size: int = 512
    chunk_overlap: int = 50
    parent_chunk_size: int = 1024
    child_chunk_size: int = 256
    enable_parent_child: bool = True
    enable_semantic: bool = True
    enable_llm: bool = False
    # LLM 合并置信度阈值：0 = 标准档（should_merge 即合并）；
    # 0.75 = 保守档（仅高置信合并，改动更少）
    llm_merge_threshold: float = 0.0
    semantic_threshold: float = 0.3
    table_handling: str = "markdown"
    min_chunk_size: int = 50
    #: 空信息块过滤：图片/表格/代码块无任何文本内容（仅剩「[图片]」这类
    #: 占位符）时是否直接丢弃。这类块向量化后是纯噪声，会在检索里霸榜
    #: （2026-09-07 PM 走查坐实），故默认丢弃；用户可在切分参数面板关掉。
    drop_empty_blocks: bool = True

    def config_hash(self) -> str:
        """切分缓存与统计快照共用的参数哈希。

        口径：**覆盖本 dataclass 除本方法/常量外的全部字段**。漏掉任何一个
        都会造成「参数改了但缓存 key 不变 → 命中旧缓存 → 参数静默不生效」
        （parent/child_chunk_size 就曾漏掉，2026-10-08 补上）。新增字段时
        必须同步加进来，`test_config_hash_covers_all_fields` 会拦下遗漏。
        """
        payload = {
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "parent_chunk_size": self.parent_chunk_size,
            "child_chunk_size": self.child_chunk_size,
            "enable_semantic": self.enable_semantic,
            "enable_llm": self.enable_llm,
            "llm_merge_threshold": self.llm_merge_threshold,
            "enable_parent_child": self.enable_parent_child,
            "table_handling": self.table_handling,
            "semantic_threshold": self.semantic_threshold,
            "min_chunk_size": self.min_chunk_size,
            "drop_empty_blocks": self.drop_empty_blocks,
            "chunker_version": self.CHUNKER_VERSION,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ChunkingConfig":
        if not d:
            return cls()
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class Chunk:
    """切分块（父块不向量化；子块 is_embedded=True 且 parent_id 指向父块）。"""

    id: str = ""
    document_id: str = ""
    parent_id: str | None = None
    chunk_index: int = 0
    content: str = ""
    chunk_type: ChunkType = ChunkType.TEXT
    breadcrumb: list[str] = field(default_factory=list)
    page_number: int = 0
    end_page: int = 0
    token_count: int = 0
    char_count: int = 0
    strategy: str = "structure"  # structure / semantic / llm / recursive
    bbox: list[float] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    table_data: dict[str, Any] | None = None
    heading_text: str = ""
    embedding_model: str | None = None
    is_embedded: bool = False
    vector_id: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id()
        if isinstance(self.chunk_type, str):
            self.chunk_type = ChunkType(self.chunk_type)
        if not self.end_page:
            self.end_page = self.page_number
        if not self.char_count:
            self.char_count = len(self.content)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["chunk_type"] = self.chunk_type.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chunk":
        return cls(
            id=d.get("id", ""),
            document_id=d.get("document_id", ""),
            parent_id=d.get("parent_id"),
            chunk_index=d.get("chunk_index", 0),
            content=d.get("content", ""),
            chunk_type=ChunkType(d.get("chunk_type", "text")),
            breadcrumb=list(d.get("breadcrumb", [])),
            page_number=d.get("page_number", 0),
            end_page=d.get("end_page", 0),
            token_count=d.get("token_count", 0),
            char_count=d.get("char_count", 0),
            strategy=d.get("strategy", "structure"),
            bbox=list(d.get("bbox", [])),
            block_ids=list(d.get("block_ids", [])),
            table_data=d.get("table_data"),
            heading_text=d.get("heading_text", ""),
            embedding_model=d.get("embedding_model"),
            is_embedded=d.get("is_embedded", False),
            vector_id=d.get("vector_id"),
        )


@dataclass
class ChunkResult:
    """切分引擎输出：父块 + 子块 + 配置哈希。"""

    parents: list[Chunk] = field(default_factory=list)
    children: list[Chunk] = field(default_factory=list)
    config_hash: str = ""

    @property
    def all_chunks(self) -> list[Chunk]:
        return [*self.parents, *self.children]

    def to_dict(self) -> dict[str, Any]:
        return {
            "parents": [c.to_dict() for c in self.parents],
            "children": [c.to_dict() for c in self.children],
            "config_hash": self.config_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChunkResult":
        return cls(
            parents=[Chunk.from_dict(x) for x in d.get("parents", [])],
            children=[Chunk.from_dict(x) for x in d.get("children", [])],
            config_hash=d.get("config_hash", ""),
        )


@dataclass
class EmbeddingResult:
    """向量化结果：稠密向量 + 可选稀疏词法权重（BGE-M3 lexical_weights）。"""

    dense: list[list[float]] = field(default_factory=list)
    sparse: list[dict[int, float]] | None = None


@dataclass
class ScoredChunk:
    """检索/Rerank 环节中的带分 chunk。

    dense_score / sparse_score 是各通道的**原始分**（稠密相似度 / FTS BM25 分），
    与 score（RRF 融合分）分开保留：RRF 分是排名倒数和、无绝对语义，
    没有各通道原始分就无法诊断"这条结果是靠哪个通道进来的、分高还是分低"。
    """

    chunk_id: str
    score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None
    dense_score: float | None = None
    sparse_score: float | None = None
    rerank_score: float | None = None
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResultItem:
    """检索返回单条结果（父块展开后）。"""

    chunk_id: str
    parent_content: str = ""
    matched_child_text: str = ""
    source_file: str = ""
    page_number: int = 0
    breadcrumb: list[str] = field(default_factory=list)
    chunk_type: str = "text"
    score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None
    dense_score: float | None = None
    sparse_score: float | None = None
    rerank_score: float | None = None


@dataclass
class SearchResponse:
    query: str
    results: list[SearchResultItem] = field(default_factory=list)
    total: int = 0
    latency_ms: int = 0
    #: 被 min_score 阈值滤掉的候选数。>0 且 results 为空时，前端应显示
    #: 「无足够相关的结果」而非「无召回结果」——两者含义不同：前者是候选都在
    #: 相关性门槛之下（全库无答案的负例查询属此类），后者是真没匹配。
    threshold_dropped: int = 0


@dataclass
class QAItemData:
    """评测 Q&A 条目（core 层载体，ORM 层落库为 QAItem）。"""

    id: str = ""
    question: str = ""
    answer: str = ""
    source_chunk_id: str | None = None
    # 生成该 QA 时的源 chunk 内容快照：实验重切后 chunk id 全变，
    # 命中判定需回退到内容匹配（见 evaluator.hit_rank）
    source_content: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _new_id()


@dataclass
class ExperimentResult:
    """单次实验指标。"""

    recall_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0
    total: int = 0
    # 每条 QA 的命中排名（None 表示未命中），供明细排查
    ranks: list[int | None] = field(default_factory=list)
