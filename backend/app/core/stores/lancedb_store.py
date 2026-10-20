"""LanceDB 默认向量库：dense ANN + Tantivy FTS 两路检索。

lancedb / pyarrow 懒导入。FTS 索引在 content 列上懒创建；
FTS 不可用时 sparse_search 返回空列表（retriever 退化为单路稠密）。
RRF 融合统一在应用层（retriever）执行，保证与 sqlite-vec 行为一致。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.core.vector_store import (
    MAIN_TABLE,
    Filters,
    StoreHit,
    VectorItem,
    VectorStore,
    VectorStoreError,
    fts_tokenize,
    sanitize_table_name,
)

logger = logging.getLogger(__name__)


class LanceDBStore(VectorStore):
    """LanceDB 嵌入式实现。"""

    def __init__(self, db_dir: str | Path) -> None:
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        try:
            import lancedb
        except ImportError as exc:
            raise VectorStoreError("lancedb 未安装") from exc
        self._db = lancedb.connect(str(self.db_dir))
        self._fts_ready: set[str] = set()

    # ------------------------------------------------------------------
    # 表管理
    # ------------------------------------------------------------------

    def _table_name(self, table: str) -> str:
        return sanitize_table_name(table)

    def _open_table(self, table: str) -> Any | None:
        name = self._table_name(table)
        try:
            if name in self._db.table_names():
                return self._db.open_table(name)
        except Exception:  # noqa: BLE001
            logger.debug("LanceDB 打开表失败：%s", name, exc_info=True)
        return None

    def _ensure_fts(self, tbl: Any, name: str) -> bool:
        """确保 content_fts 列上有 Tantivy FTS 索引（必要时先做列迁移）。"""
        if name in self._fts_ready:
            return True
        # 中文 FTS 依赖 content_fts 影子列（fts_tokenize 逐字分词），
        # 直接在 content 上建索引会让 CJK 整段成一个 token 而查不到
        if "content_fts" not in tbl.schema.names:
            try:
                self._migrate_add_fts_column(tbl, name)
            except Exception:  # noqa: BLE001
                logger.warning("content_fts 列迁移失败，稀疏检索暂不可用", exc_info=True)
                return False
            tbl = self._open_table(name)
            if tbl is None:
                return False
        try:
            tbl.create_fts_index("content_fts", replace=False)
            self._fts_ready.add(name)
            return True
        except Exception:  # noqa: BLE001 - 索引已存在或版本不支持
            # 索引已存在时也算可用
            self._fts_ready.add(name)
            logger.debug("FTS 索引创建跳过（可能已存在）", exc_info=True)
            return True

    def _migrate_add_fts_column(self, tbl: Any, name: str) -> None:
        """存量表补 content_fts 列：整表读出→重建（行级规模，一次性）。"""
        logger.info("LanceDB 表 %s 缺 content_fts 列，执行一次性迁移", name)
        rows = tbl.to_pandas().to_dict("records")
        for row in rows:
            row["content_fts"] = fts_tokenize(str(row.get("content") or ""))
        self._db.drop_table(name)
        if rows:
            self._db.create_table(name, rows)

    @staticmethod
    def _row(item: VectorItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "vector": item.vector,
            "content": item.content,
            # 中文 FTS 影子列（逐字分词），展示仍用 content 原文
            "content_fts": fts_tokenize(item.content),
            "document_id": item.document_id,
            "chunk_type": item.chunk_type,
            "metadata_json": json.dumps(item.metadata, ensure_ascii=False),
        }

    @staticmethod
    def _filter_where(filters: Filters) -> str | None:
        if not filters:
            return None
        clauses: list[str] = []
        if filters.get("document_ids"):
            ids = ", ".join(f"'{d}'" for d in filters["document_ids"])
            clauses.append(f"document_id IN ({ids})")
        if filters.get("chunk_types"):
            types = ", ".join(f"'{t}'" for t in filters["chunk_types"])
            clauses.append(f"chunk_type IN ({types})")
        return " AND ".join(clauses) if clauses else None

    @staticmethod
    def _to_hit(row: dict[str, Any], score: float) -> StoreHit:
        try:
            metadata = json.loads(row.get("metadata_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return StoreHit(
            id=row.get("id", ""),
            score=score,
            content=row.get("content", ""),
            document_id=row.get("document_id", ""),
            chunk_type=row.get("chunk_type", "text"),
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # VectorStore 接口
    # ------------------------------------------------------------------

    def upsert(self, items: list[VectorItem], table: str = MAIN_TABLE) -> None:
        if not items:
            return
        name = self._table_name(table)
        rows = [self._row(item) for item in items]
        try:
            tbl = self._open_table(table)
            if tbl is None:
                self._db.create_table(name, rows)
            else:
                # 幂等写入：同 id 更新、新 id 插入（merge_insert）。
                # tbl.add 是纯追加，store_failed 重试只重跑 run_embed 不删库，
                # 会产生重复向量行；此写法与 sqlite-vec 的 INSERT OR REPLACE 对齐。
                (
                    tbl.merge_insert("id")
                    .when_matched_update_all()
                    .when_not_matched_insert_all()
                    .execute(rows)
                )
                # FTS 索引维护（版本相关）：实测 lancedb 0.37.1 上 merge_insert
                # 写入/更新的行都能被 FTS 检索到，但旧版本不会自动更新索引，
                # 故本进程已确认建过索引时整体重建一次（入库低频、万级行秒级）。
                # 该行为由 test_fts_sees_new_rows_after_store_restart /
                # test_fts_sees_updated_content_after_store_restart 钉住，
                # 升级 lancedb 后若这两个用例挂了，说明这里要改成无条件重建。
                if name in self._fts_ready:
                    tbl.create_fts_index("content_fts", replace=True)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"LanceDB 写入失败：{exc}") from exc

    def dense_search(
        self,
        query_vec: list[float],
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
    ) -> list[StoreHit]:
        tbl = self._open_table(table)
        if tbl is None:
            return []
        try:
            query = tbl.search(query_vec)
            where = self._filter_where(filters)
            if where:
                query = query.where(where)
            rows = query.limit(top_k).to_list()
        except Exception:  # noqa: BLE001
            logger.warning("LanceDB 稠密检索失败", exc_info=True)
            return []
        return [
            self._to_hit(row, 1.0 / (1.0 + float(row.get("_distance", 0.0))))
            for row in rows
        ]

    def sparse_search(
        self,
        query: "str | dict[int, float]",
        top_k: int = 10,
        filters: Filters = None,
        table: str = MAIN_TABLE,
    ) -> list[StoreHit]:
        if not isinstance(query, str):
            return []
        tbl = self._open_table(table)
        if tbl is None:
            return []
        name = self._table_name(table)
        if not self._ensure_fts(tbl, name):
            return []
        # 迁移（drop+重建）后旧表句柄失效，重新打开
        tbl = self._open_table(table)
        if tbl is None:
            return []
        # 查询同样过 fts_tokenize，与写入侧逐字分词对齐（OR 语义）
        tokenized = fts_tokenize(query)
        try:
            search = tbl.search(tokenized, query_type="fts")
            where = self._filter_where(filters)
            if where:
                search = search.where(where)
            rows = search.limit(top_k).to_list()
        except Exception:  # noqa: BLE001
            logger.warning("LanceDB FTS 检索失败，返回空（退化为单路稠密）")
            return []
        return [
            self._to_hit(row, 1.0 / (1.0 + abs(float(row.get("_score", 0.0)))))
            for row in rows
        ]

    def delete_by_document(self, document_id: str, table: str = MAIN_TABLE) -> None:
        tbl = self._open_table(table)
        if tbl is None:
            return
        try:
            tbl.delete(f"document_id = '{document_id}'")
        except Exception:  # noqa: BLE001
            logger.warning("LanceDB 删除文档向量失败", exc_info=True)

    def drop_table(self, table: str) -> None:
        name = self._table_name(table)
        try:
            self._db.drop_table(name)
            self._fts_ready.discard(name)
        except Exception:  # noqa: BLE001
            logger.debug("LanceDB 删表跳过：%s", name, exc_info=True)

    def health(self) -> bool:
        try:
            self._db.table_names()
            return True
        except Exception:  # noqa: BLE001
            return False
