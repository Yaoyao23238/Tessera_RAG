"""VectorStore 抽象基类 + 工厂（含 LanceDB → sqlite-vec 自动降级）。

四实现：
- lancedb_store（默认）：dense ANN + Tantivy FTS 两路结果
- sqlite_vec_store（兜底）：FTS5 + vec0 同库单文件
- qdrant_store：稠密 + 原生稀疏
- pgvector_store：HNSW + tsvector

统一行为（DESIGN 待明确#6）：RRF 与 Rerank 均在应用层执行，
各后端只负责返回 dense / sparse 两路候选。
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

logger = logging.getLogger(__name__)

# 主库固定表名（共享知识 #4）
MAIN_TABLE = "tessera_chunks"


def experiment_table_name(experiment_id: str) -> str:
    """实验独立表命名：exp_{experiment_id 前 8 位}。"""
    return f"exp_{experiment_id[:8]}"


def sanitize_table_name(name: str) -> str:
    """表名净化：仅保留字母数字下划线，数字开头补前缀。"""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name) or MAIN_TABLE
    if cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned


@dataclass
class VectorItem:
    """待写入向量库的条目。"""

    id: str
    vector: list[float]
    content: str = ""
    sparse: dict[int, float] | None = None  # BGE-M3 lexical_weights（Qdrant/pgvector 用）
    document_id: str = ""
    chunk_type: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoreHit:
    """向量库单路检索命中。"""

    id: str
    score: float = 0.0
    content: str = ""
    document_id: str = ""
    chunk_type: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)


# 检索过滤条件：{"document_ids": [...], "chunk_types": [...]}
Filters = dict[str, list[str]] | None

# 稀疏查询：原始文本（FTS 后端）或稀疏权重 dict（Qdrant 原生稀疏）
SparseQuery = Union[str, dict[int, float]]

_CJK_RANGE = ("一", "鿿")

#: FTS 分词器版本。分词规则变更时递增——影子列与索引必须随之重建，
#: 否则索引里是旧词表、查询用新词表，稀疏检索会静默失效。
#: 1 = 逐字（已废弃）；2 = CJK 相邻二元。
FTS_TOKENIZER_VERSION = 2


def _is_cjk(ch: str) -> bool:
    return _CJK_RANGE[0] <= ch <= _CJK_RANGE[1]


def fts_tokenize(text: str) -> str:
    """FTS 分词预处理：CJK 连续段切**相邻二元**，非 CJK 段原样保留。

    为什么不用逐字（版本 1）：逐字 + OR 语义下，任何含常用字（与/的/是）
    的长文本都会命中；再叠加 BM25 长度归一化，全库最长的块（表格密集）
    被惩罚最重 —— 2026-10-08 实测正确答案文档连 FTS top15 都进不去，
    反被中等长度的「文字墙」文档霸榜。二元组合（「图像」「著录」）特异性
    高数个数量级，误命中随之大幅下降。

    - CJK 段长度为 1 时退化为单字（否则产不出任何 token）
    - 非 CJK 段（拉丁词、数字、符号）**逐字原样输出**，不拼接也不插空格，
      由底层分词器（Tantivy unicode61 / FTS5 unicode61）自行切词，
      行为与版本 1 完全一致，避免把 ``FastAPI`` 拆成 ``F a s t A P I``
    - 写入侧（影子列）与查询侧必须同用本函数

    分词规则若再变更：递增 FTS_TOKENIZER_VERSION 并全量重建影子列与
    FTS 索引（重入库即可，upsert 会按 content 重算影子列）。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if _is_cjk(text[i]):
            j = i
            while j < n and _is_cjk(text[j]):
                j += 1
            run = text[i:j]
            grams = (
                [run] if len(run) == 1 else [run[k : k + 2] for k in range(len(run) - 1)]
            )
            for gram in grams:
                out.append(f" {gram} ")
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


class VectorStoreError(Exception):
    """向量库操作失败。"""


class VectorStore(ABC):
    """向量库抽象基类。"""

    #: 初始化降级来源（工厂层设置，用于前端提示「已自动降级 sqlite-vec」）
    degraded_from: str | None = None

    #: 稀疏检索查询形态："text"=FTS 文本（sqlite-vec/LanceDB/pgvector），
    #: "weights"=BGE-M3 lexical_weights 字典（Qdrant 原生稀疏向量）。
    #: retriever 依此决定 sparse_search 传原始查询串还是权重字典。
    sparse_query_kind: str = "text"

    @abstractmethod
    def upsert(self, items: list[VectorItem], table: str = MAIN_TABLE) -> None:
        """批量写入/更新向量。"""

    @abstractmethod
    def dense_search(
        self,
        query_vec: list[float],
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
    ) -> list[StoreHit]:
        """稠密向量检索。"""

    @abstractmethod
    def sparse_search(
        self,
        query: "str | dict[int, float]",
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
    ) -> list[StoreHit]:
        """稀疏检索（FTS 文本或原生稀疏向量，由实现决定）。"""

    @abstractmethod
    def delete_by_document(self, document_id: str, table: str = MAIN_TABLE) -> None:
        """按文档级联删除。"""

    @abstractmethod
    def drop_table(self, table: str) -> None:
        """删除整张表（删除实验时级联调用）。"""

    @abstractmethod
    def health(self) -> bool:
        """健康检查。"""


def create_vector_store(
    config: dict[str, Any], vector_dir: str | Path
) -> VectorStore:
    """向量库工厂。

    backend 枚举：sqlite_vec / lancedb / qdrant / pgvector。
    LanceDB 初始化失败自动降级 sqlite-vec 并在 store.degraded_from 标记。
    """
    backend = config.get("backend", "lancedb")
    vector_dir = Path(vector_dir)

    if backend == "lancedb":
        try:
            from app.core.stores.lancedb_store import LanceDBStore

            store = LanceDBStore(vector_dir / "lancedb")
            if not store.health():
                raise VectorStoreError("LanceDB 健康检查失败")
            return store
        except Exception as exc:  # noqa: BLE001 - 任何初始化失败都降级
            logger.warning(
                "LanceDB 初始化失败，自动降级 sqlite-vec：%s", exc, exc_info=True
            )
            from app.core.stores.sqlite_vec_store import SqliteVecStore

            fallback = SqliteVecStore(vector_dir / "tessera_vec.db")
            fallback.degraded_from = "lancedb"
            return fallback

    if backend == "sqlite_vec":
        from app.core.stores.sqlite_vec_store import SqliteVecStore

        return SqliteVecStore(vector_dir / "tessera_vec.db")

    if backend == "qdrant":
        from app.core.stores.qdrant_store import QdrantStore

        return QdrantStore(
            url=config.get("connection_url") or "http://localhost:6333",
            api_key=(config.get("config") or {}).get("api_key"),
            dimensions=int((config.get("config") or {}).get("dimensions", 1024)),
        )

    if backend == "pgvector":
        from app.core.stores.pgvector_store import PgVectorStore

        return PgVectorStore(
            dsn=config.get("connection_url") or "",
            dimensions=int((config.get("config") or {}).get("dimensions", 1024)),
        )

    raise VectorStoreError(f"未知向量库后端：{backend}")
