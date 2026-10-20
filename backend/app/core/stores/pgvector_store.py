"""pgvector 大档向量库：HNSW + tsvector 全文。

psycopg 懒导入。每表结构：
  id TEXT PK / embedding vector(dim) / content TEXT / document_id /
  chunk_type / metadata JSONB / tsv tsvector（generated）
RRF 融合统一在应用层执行（DESIGN 待明确#6），本实现只提供两路候选。
"""

from __future__ import annotations

import json
import logging

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


class PgVectorStore(VectorStore):
    """pgvector 实现。"""

    def __init__(self, dsn: str, dimensions: int = 1024) -> None:
        if not dsn:
            raise VectorStoreError("pgvector 需要 connection_url（DSN）")
        self.dsn = dsn
        self.dimensions = dimensions
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise VectorStoreError("psycopg 未安装") from exc

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn)

    # ------------------------------------------------------------------

    def _table(self, table: str) -> str:
        return sanitize_table_name(table)

    def _ensure_table(self, conn, table: str) -> None:
        name = self._table(table)
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS "{name}" (
                    id TEXT PRIMARY KEY,
                    embedding vector({self.dimensions}),
                    content TEXT,
                    document_id TEXT,
                    chunk_type TEXT,
                    metadata JSONB,
                    tsv tsvector GENERATED ALWAYS AS
                        (to_tsvector('simple', coalesce(content, ''))) STORED
                )"""
            )
            cur.execute(
                f"""CREATE INDEX IF NOT EXISTS "{name}_hnsw"
                ON "{name}" USING hnsw (embedding vector_cosine_ops)"""
            )
        conn.commit()

    @staticmethod
    def _filter_clause(filters: Filters) -> tuple[str, list[list[str]]]:
        clauses: list[str] = []
        params: list[list[str]] = []
        if filters:
            if filters.get("document_ids"):
                clauses.append("document_id = ANY(%s)")
                params.append(filters["document_ids"])
            if filters.get("chunk_types"):
                clauses.append("chunk_type = ANY(%s)")
                params.append(filters["chunk_types"])
        return (f"AND {' AND '.join(clauses)}" if clauses else ""), params

    # ------------------------------------------------------------------
    # VectorStore 接口
    # ------------------------------------------------------------------

    def upsert(self, items: list[VectorItem], table: str = MAIN_TABLE) -> None:
        if not items:
            return
        name = self._table(table)
        with self._connect() as conn:
            self._ensure_table(conn, table)
            with conn.cursor() as cur:
                for item in items:
                    cur.execute(
                        f"""INSERT INTO "{name}"
                        (id, embedding, content, document_id, chunk_type, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            content = EXCLUDED.content,
                            document_id = EXCLUDED.document_id,
                            chunk_type = EXCLUDED.chunk_type,
                            metadata = EXCLUDED.metadata""",
                        (
                            item.id,
                            item.vector,
                            item.content,
                            item.document_id,
                            item.chunk_type,
                            json.dumps(item.metadata, ensure_ascii=False),
                        ),
                    )
            conn.commit()

    def dense_search(
        self,
        query_vec: list[float],
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
    ) -> list[StoreHit]:
        name = self._table(table)
        clause, params = self._filter_clause(filters)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, content, document_id, chunk_type, metadata,
                        1 - (embedding <=> %s::vector) AS score
                    FROM "{name}" WHERE TRUE {clause}
                    ORDER BY embedding <=> %s::vector LIMIT %s""",
                    [query_vec, *params, query_vec, top_k],
                )
                rows = cur.fetchall()
        except Exception:  # noqa: BLE001
            logger.warning("pgvector 稠密检索失败", exc_info=True)
            return []
        return [self._row_to_hit(row) for row in rows]

    def sparse_search(
        self,
        query: "str | dict[int, float]",
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
    ) -> list[StoreHit]:
        if not isinstance(query, str):
            return []
        name = self._table(table)
        clause, params = self._filter_clause(filters)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, content, document_id, chunk_type, metadata,
                        ts_rank(tsv, plainto_tsquery('simple', %s)) AS score
                    FROM "{name}"
                    WHERE tsv @@ plainto_tsquery('simple', %s) {clause}
                    ORDER BY score DESC LIMIT %s""",
                    [query, query, *params, top_k],
                )
                rows = cur.fetchall()
        except Exception:  # noqa: BLE001
            logger.warning("pgvector 全文检索失败", exc_info=True)
            return []
        return [self._row_to_hit(row) for row in rows]

    def delete_by_document(self, document_id: str, table: str = MAIN_TABLE) -> None:
        name = self._table(table)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f'DELETE FROM "{name}" WHERE document_id = %s',
                    (document_id,),
                )
            conn.commit()
        except Exception:  # noqa: BLE001
            logger.warning("pgvector 删除文档向量失败", exc_info=True)

    def drop_table(self, table: str) -> None:
        name = self._table(table)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(f'DROP TABLE IF EXISTS "{name}"')
            conn.commit()
        except Exception:  # noqa: BLE001
            logger.debug("pgvector 删表跳过", exc_info=True)

    def health(self) -> bool:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_hit(row) -> StoreHit:
        metadata = row[4]
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        return StoreHit(
            id=row[0],
            score=float(row[5] or 0.0),
            content=row[1] or "",
            document_id=row[2] or "",
            chunk_type=row[3] or "text",
            metadata=metadata or {},
        )
