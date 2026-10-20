"""Qdrant 中档向量库：稠密向量 + 原生稀疏向量（BGE-M3 lexical_weights）。

qdrant-client 懒导入。稀疏检索要求传入稀疏权重 dict（retriever 在
BGE-M3 本地 embedding 时透传 query 的 lexical_weights）。
RRF 仍在应用层统一执行（DESIGN 待明确#6）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.core.vector_store import (
    MAIN_TABLE,
    Filters,
    StoreHit,
    VectorItem,
    VectorStore,
    VectorStoreError,
    sanitize_table_name,
)

logger = logging.getLogger(__name__)


def _to_point_id(chunk_id: str) -> str:
    """chunk_id 转 Qdrant 点 ID（确定性 UUID5）。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"tessera:{chunk_id}"))


class QdrantStore(VectorStore):
    """Qdrant 实现（命名向量：dense + sparse）。"""

    #: Qdrant 稀疏路使用 BGE-M3 lexical_weights（原生稀疏向量检索）
    sparse_query_kind = "weights"

    def __init__(
        self,
        url: str = "http://localhost:6333",
        api_key: str | None = None,
        dimensions: int = 1024,
    ) -> None:
        self.url = url
        self.dimensions = dimensions
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise VectorStoreError("qdrant-client 未安装") from exc
        self._client = QdrantClient(url=url, api_key=api_key)

    # ------------------------------------------------------------------

    def _collection(self, table: str) -> str:
        return sanitize_table_name(table)

    def _ensure_collection(self, table: str) -> None:
        from qdrant_client import models

        name = self._collection(table)
        if self._client.collection_exists(name):
            return
        self._client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": models.VectorParams(
                    size=self.dimensions, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False)
                )
            },
        )

    @staticmethod
    def _filter(filters: Filters) -> Any | None:
        if not filters:
            return None
        from qdrant_client import models

        conditions: list[Any] = []
        if filters.get("document_ids"):
            conditions.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchAny(any=filters["document_ids"]),
                )
            )
        if filters.get("chunk_types"):
            conditions.append(
                models.FieldCondition(
                    key="chunk_type",
                    match=models.MatchAny(any=filters["chunk_types"]),
                )
            )
        return models.Filter(must=conditions) if conditions else None

    @staticmethod
    def _to_hit(scored: Any) -> StoreHit:
        payload = scored.payload or {}
        return StoreHit(
            id=payload.get("chunk_id", ""),
            score=float(scored.score or 0.0),
            content=payload.get("content", ""),
            document_id=payload.get("document_id", ""),
            chunk_type=payload.get("chunk_type", "text"),
            metadata=payload.get("metadata", {}),
        )

    # ------------------------------------------------------------------
    # VectorStore 接口
    # ------------------------------------------------------------------

    def upsert(self, items: list[VectorItem], table: str = MAIN_TABLE) -> None:
        if not items:
            return
        from qdrant_client import models

        self._ensure_collection(table)
        points = []
        for item in items:
            vector: dict[str, Any] = {"dense": item.vector}
            if item.sparse:
                vector["sparse"] = models.SparseVector(
                    indices=list(item.sparse.keys()),
                    values=list(item.sparse.values()),
                )
            points.append(
                models.PointStruct(
                    id=_to_point_id(item.id),
                    vector=vector,
                    payload={
                        "chunk_id": item.id,
                        "content": item.content,
                        "document_id": item.document_id,
                        "chunk_type": item.chunk_type,
                        "metadata": item.metadata,
                    },
                )
            )
        self._client.upsert(collection_name=self._collection(table), points=points)

    def dense_search(
        self,
        query_vec: list[float],
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
    ) -> list[StoreHit]:
        try:
            response = self._client.query_points(
                collection_name=self._collection(table),
                query=query_vec,
                using="dense",
                limit=top_k,
                query_filter=self._filter(filters),
            )
            return [self._to_hit(p) for p in response.points]
        except Exception:  # noqa: BLE001
            logger.warning("Qdrant 稠密检索失败", exc_info=True)
            return []

    def sparse_search(
        self,
        query: "str | dict[int, float]",
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
    ) -> list[StoreHit]:
        if not isinstance(query, dict):
            # Qdrant 稀疏路需要 BGE-M3 lexical_weights；纯文本无法检索
            return []
        from qdrant_client import models

        try:
            response = self._client.query_points(
                collection_name=self._collection(table),
                query=models.SparseVector(
                    indices=list(query.keys()), values=list(query.values())
                ),
                using="sparse",
                limit=top_k,
                query_filter=self._filter(filters),
            )
            return [self._to_hit(p) for p in response.points]
        except Exception:  # noqa: BLE001
            logger.warning("Qdrant 稀疏检索失败", exc_info=True)
            return []

    def delete_by_document(self, document_id: str, table: str = MAIN_TABLE) -> None:
        from qdrant_client import models

        try:
            self._client.delete(
                collection_name=self._collection(table),
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="document_id",
                                match=models.MatchValue(value=document_id),
                            )
                        ]
                    )
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning("Qdrant 删除文档向量失败", exc_info=True)

    def drop_table(self, table: str) -> None:
        try:
            self._client.delete_collection(self._collection(table))
        except Exception:  # noqa: BLE001
            logger.debug("Qdrant 删表跳过", exc_info=True)

    def health(self) -> bool:
        try:
            self._client.get_collections()
            return True
        except Exception:  # noqa: BLE001
            return False
