"""sqlite-vec 兜底向量库：FTS5 + vec0 共存同一 .db 单文件。

- 稠密：优先 sqlite-vec vec0 虚拟表 KNN；sqlite-vec 不可用时退化为
  应用层暴力余弦（<5000 chunk 兜底规模可接受）
- 稀疏：FTS5 BM25（中文按字切分写入与查询，绕过 unicode61 对
  CJK 连续文本整段成 token 的限制）
- 融合：应用层 RRF（retriever 统一执行）
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import struct
import threading
from pathlib import Path

from app.core.vector_store import (
    MAIN_TABLE,
    Filters,
    StoreHit,
    VectorItem,
    VectorStore,
    fts_tokenize,
    sanitize_table_name,
)

logger = logging.getLogger(__name__)


def _pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class SqliteVecStore(VectorStore):
    """sqlite-vec 实现（单文件零服务）。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._vec_available = self._try_load_vec_extension()

    def _try_load_vec_extension(self) -> bool:
        """加载 sqlite-vec 扩展（不可用时降级暴力余弦）。"""
        try:
            import sqlite_vec

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            return True
        except Exception:  # noqa: BLE001
            logger.warning("sqlite-vec 不可用，稠密检索退化为暴力余弦")
            return False

    # ------------------------------------------------------------------
    # 表管理
    # ------------------------------------------------------------------

    def _tables(self, table: str) -> tuple[str, str, str]:
        name = sanitize_table_name(table)
        return name, f"{name}_fts", f"{name}_vec"

    def _ensure_tables(self, table: str, dimensions: int) -> None:
        main, fts, vec = self._tables(table)
        with self._lock:
            self._conn.execute(
                f"""CREATE TABLE IF NOT EXISTS "{main}" (
                    id TEXT PRIMARY KEY,
                    vector BLOB,
                    content TEXT,
                    document_id TEXT,
                    chunk_type TEXT,
                    metadata TEXT
                )"""
            )
            self._conn.execute(
                f"""CREATE VIRTUAL TABLE IF NOT EXISTS "{fts}"
                USING fts5(id UNINDEXED, content)"""
            )
            if self._vec_available and dimensions > 0:
                try:
                    self._conn.execute(
                        f"""CREATE VIRTUAL TABLE IF NOT EXISTS "{vec}"
                        USING vec0(id TEXT PRIMARY KEY, embedding float[{dimensions}])"""
                    )
                except sqlite3.Error:
                    logger.debug("vec0 表创建失败，使用暴力余弦", exc_info=True)
            self._conn.commit()

    # ------------------------------------------------------------------
    # VectorStore 接口
    # ------------------------------------------------------------------

    def upsert(self, items: list[VectorItem], table: str = MAIN_TABLE) -> None:
        if not items:
            return
        dimensions = len(items[0].vector)
        self._ensure_tables(table, dimensions)
        main, fts, vec = self._tables(table)
        with self._lock:
            for item in items:
                self._conn.execute(
                    f'INSERT OR REPLACE INTO "{main}" VALUES (?,?,?,?,?,?)',
                    (
                        item.id,
                        _pack_vector(item.vector),
                        item.content,
                        item.document_id,
                        item.chunk_type,
                        json.dumps(item.metadata, ensure_ascii=False),
                    ),
                )
                # fts5 影子表的 id 是 UNINDEXED 普通列（无唯一约束），
                # INSERT OR REPLACE 不会命中冲突、永远只追加——同 id 更新会
                # 累积影子行，旧内容仍被稀疏检索命中。必须先删后插。
                self._conn.execute(
                    f'DELETE FROM "{fts}" WHERE id = ?', (item.id,)
                )
                self._conn.execute(
                    f'INSERT INTO "{fts}" (id, content) VALUES (?,?)',
                    (item.id, fts_tokenize(item.content)),
                )
                if self._vec_available:
                    try:
                        self._conn.execute(
                            f'INSERT OR REPLACE INTO "{vec}" (id, embedding) VALUES (?,?)',
                            (item.id, _pack_vector(item.vector)),
                        )
                    except sqlite3.Error:
                        logger.debug("vec0 写入失败，跳过", exc_info=True)
            self._conn.commit()

    def _filter_clause(
        self, filters: Filters, alias: str = ""
    ) -> tuple[str, list[str]]:
        """构建 metadata 过滤 WHERE 子句。"""
        prefix = f"{alias}." if alias else ""
        clauses: list[str] = []
        params: list[str] = []
        if filters:
            if filters.get("document_ids"):
                placeholders = ",".join("?" for _ in filters["document_ids"])
                clauses.append(f"{prefix}document_id IN ({placeholders})")
                params.extend(filters["document_ids"])
            if filters.get("chunk_types"):
                placeholders = ",".join("?" for _ in filters["chunk_types"])
                clauses.append(f"{prefix}chunk_type IN ({placeholders})")
                params.extend(filters["chunk_types"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def dense_search(
        self,
        query_vec: list[float],
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
    ) -> list[StoreHit]:
        main, _, vec = self._tables(table)
        where, params = self._filter_clause(filters)
        with self._lock:
            if self._vec_available:
                try:
                    return self._dense_search_vec0(
                        vec, main, query_vec, top_k, where, params
                    )
                except sqlite3.Error:
                    logger.debug("vec0 检索失败，回退暴力余弦", exc_info=True)
            rows = self._conn.execute(
                f'SELECT * FROM "{main}" {where}', params
            ).fetchall()
        scored = [
            (row, _cosine(query_vec, _unpack_vector(row["vector"])))
            for row in rows
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return [self._row_to_hit(row, score) for row, score in scored[:top_k]]

    def _dense_search_vec0(
        self,
        vec: str,
        main: str,
        query_vec: list[float],
        top_k: int,
        where: str,
        params: list[str],
    ) -> list[StoreHit]:
        """vec0 KNN 检索（距离转相似度分数）。"""
        # 先 KNN 取候选，再按过滤条件筛选（候选放大 4 倍弥补过滤损耗）
        rows = self._conn.execute(
            f"""SELECT v.id, v.distance, m.* FROM "{vec}" v
                JOIN "{main}" m ON m.id = v.id
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance""",
            (_pack_vector(query_vec), top_k * 4),
        ).fetchall()
        hits: list[StoreHit] = []
        filter_ids: set[str] | None = None
        if where:
            allowed = self._conn.execute(
                f'SELECT id FROM "{main}" {where}', params
            ).fetchall()
            filter_ids = {row["id"] for row in allowed}
        for row in rows:
            if filter_ids is not None and row["id"] not in filter_ids:
                continue
            distance = float(row["distance"])
            hits.append(self._row_to_hit(row, 1.0 / (1.0 + distance)))
            if len(hits) >= top_k:
                break
        return hits

    def sparse_search(
        self,
        query: "str | dict[int, float]",
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
    ) -> list[StoreHit]:
        if not isinstance(query, str):
            # sqlite-vec 后端不使用原生稀疏权重，FTS 需要文本
            return []
        main, fts, _ = self._tables(table)
        terms = [t for t in fts_tokenize(query).split() if t]
        if not terms:
            return []
        match_query = " OR ".join(f'"{t}"' for t in terms)
        where, params = self._filter_clause(filters, alias="m")
        and_clause = where.replace("WHERE", "AND", 1) if where else ""
        with self._lock:
            try:
                rows = self._conn.execute(
                    f"""SELECT m.*, bm25("{fts}") AS rank FROM "{fts}" f
                        JOIN "{main}" m ON m.id = f.id
                        WHERE "{fts}" MATCH ? {and_clause}
                        ORDER BY rank LIMIT ?""",
                    [match_query, *params, top_k],
                ).fetchall()
            except sqlite3.Error:
                logger.warning("FTS5 检索失败", exc_info=True)
                return []
        # bm25 返回越小越好，转为正分数
        return [
            self._row_to_hit(row, 1.0 / (1.0 + abs(float(row["rank"]))))
            for row in rows
        ]

    def delete_by_document(self, document_id: str, table: str = MAIN_TABLE) -> None:
        main, fts, vec = self._tables(table)
        with self._lock:
            ids = [
                row["id"]
                for row in self._conn.execute(
                    f'SELECT id FROM "{main}" WHERE document_id = ?',
                    (document_id,),
                ).fetchall()
            ]
            self._conn.execute(
                f'DELETE FROM "{main}" WHERE document_id = ?', (document_id,)
            )
            for chunk_id in ids:
                self._conn.execute(f'DELETE FROM "{fts}" WHERE id = ?', (chunk_id,))
                if self._vec_available:
                    try:
                        self._conn.execute(
                            f'DELETE FROM "{vec}" WHERE id = ?', (chunk_id,)
                        )
                    except sqlite3.Error:
                        pass
            self._conn.commit()

    def drop_table(self, table: str) -> None:
        main, fts, vec = self._tables(table)
        with self._lock:
            for name in (main, fts, vec):
                self._conn.execute(f'DROP TABLE IF EXISTS "{name}"')
            self._conn.commit()

    def health(self) -> bool:
        try:
            self._conn.execute("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        """关闭数据库连接（Windows 下释放文件句柄）。"""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "SqliteVecStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_hit(row: sqlite3.Row, score: float) -> StoreHit:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return StoreHit(
            id=row["id"],
            score=score,
            content=row["content"] or "",
            document_id=row["document_id"] or "",
            chunk_type=row["chunk_type"] or "text",
            metadata=metadata,
        )
