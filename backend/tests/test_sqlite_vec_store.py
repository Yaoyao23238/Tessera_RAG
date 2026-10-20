"""sqlite-vec 兜底向量库真实集成测试（非 fake）。

对齐 test_stores.py（LanceDB 侧）的语义：upsert 幂等 / 删除 / dense / sparse /
过滤 / 重启后 FTS 可见性。使用真实 sqlite-vec + 临时目录。

背景：sqlite-vec 是设置页可切换的四档后端之一，此前零测试覆盖。
FTS5 影子表（{name}_fts）的 upsert 语义与主表不同——fts5 虚拟表的 id 是
UNINDEXED 普通列（无唯一约束），INSERT OR REPLACE 永远不会"替换"旧行，
同 id 更新会累积影子行、旧内容仍被稀疏检索命中（回归用例钉住）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.stores.sqlite_vec_store import SqliteVecStore  # noqa: E402
from app.core.vector_store import VectorItem  # noqa: E402


def _item(
    item_id: str,
    doc: str = "doc1",
    content: str = "内容",
    vec=None,
    chunk_type: str = "text",
) -> VectorItem:
    return VectorItem(
        id=item_id,
        vector=vec or [0.1, 0.2, 0.3],
        content=content,
        document_id=doc,
        chunk_type=chunk_type,
        metadata={},
    )


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "vec.db"


@pytest.fixture()
def store(db_path):
    s = SqliteVecStore(db_path)
    yield s
    s.close()


class TestSqliteVecUpsert:
    def test_upsert_idempotent_retry(self, store):
        """同 id 重复 upsert（store_failed 重试场景）不产生重复行。"""
        store.upsert([_item("c1", content="第一版"), _item("c2")])
        store.upsert([_item("c1", content="第二版"), _item("c2")])
        rows = store._conn.execute(
            'SELECT id, content FROM "tessera_chunks" ORDER BY id'
        ).fetchall()
        assert len(rows) == 2
        by_id = {r["id"]: r["content"] for r in rows}
        assert by_id["c1"] == "第二版"  # 更新而非追加

    def test_upsert_mixed_new_and_existing(self, store):
        store.upsert([_item("c1")])
        store.upsert([_item("c1", content="更新"), _item("c2", content="新增")])
        count = store._conn.execute(
            'SELECT COUNT(*) AS n FROM "tessera_chunks"'
        ).fetchone()["n"]
        assert count == 2

    def test_fts_no_stale_content_after_update(self, store):
        """同 id 更新后旧内容不再被稀疏检索命中（FTS 影子表回归）。

        fts5 影子表的 id 无唯一约束，INSERT OR REPLACE 不会替换旧行，
        旧影子行残留会让「旧词」继续命中已更新的块——检索返回自相矛盾
        的结果（content 是新内容却由旧词命中）。正确语义：更新即替换。
        """
        store.upsert([_item("c1", content="苹果香蕉")])
        assert any(h.id == "c1" for h in store.sparse_search("苹果", top_k=5))
        # 同 id 更新内容
        store.upsert([_item("c1", content="橘子葡萄")])
        assert any(h.id == "c1" for h in store.sparse_search("橘子", top_k=5))
        stale = store.sparse_search("香蕉", top_k=5)
        assert all(h.id != "c1" for h in stale), (
            "同 id 更新后旧词仍命中 → FTS 影子表残留旧行（upsert 需先删后插）"
        )
        # 影子行数应与主表一致（无累积）
        fts_count = store._conn.execute(
            'SELECT COUNT(*) AS n FROM "tessera_chunks_fts"'
        ).fetchone()["n"]
        main_count = store._conn.execute(
            'SELECT COUNT(*) AS n FROM "tessera_chunks"'
        ).fetchone()["n"]
        assert fts_count == main_count

    def test_empty_items_noop(self, store):
        store.upsert([])
        assert store.health() is True


class TestSqliteVecDelete:
    def test_delete_by_document(self, store):
        store.upsert(
            [
                _item("c1", doc="d1", content="苹果香蕉", vec=[1.0, 0.0, 0.0]),
                _item("c2", doc="d2", content="橘子葡萄", vec=[0.0, 1.0, 0.0]),
            ]
        )
        store.delete_by_document("d1")
        hits = store.dense_search([1.0, 0.0, 0.0], top_k=5)
        assert all(h.document_id != "d1" for h in hits)
        assert all(h.id != "c1" for h in store.sparse_search("苹果", top_k=5))
        # 影子行同步删除
        fts_count = store._conn.execute(
            'SELECT COUNT(*) AS n FROM "tessera_chunks_fts"'
        ).fetchone()["n"]
        assert fts_count == 1


class TestSqliteVecDense:
    def test_dense_search_ranking(self, store):
        store.upsert(
            [
                _item("near", vec=[1.0, 0.0, 0.0]),
                _item("far", vec=[0.0, 1.0, 0.0]),
            ]
        )
        hits = store.dense_search([1.0, 0.0, 0.0], top_k=2)
        assert [h.id for h in hits] == ["near", "far"]

    def test_dense_search_top_k(self, store):
        store.upsert([_item(f"c{i}", vec=[1.0, float(i), 0.0]) for i in range(5)])
        hits = store.dense_search([1.0, 0.0, 0.0], top_k=3)
        assert len(hits) == 3

    def test_dense_search_with_filters(self, store):
        store.upsert(
            [
                _item("c1", doc="d1", vec=[1.0, 0.0, 0.0], chunk_type="text"),
                _item("c2", doc="d2", vec=[0.9, 0.1, 0.0], chunk_type="table"),
            ]
        )
        hits = store.dense_search(
            [1.0, 0.0, 0.0], top_k=5, filters={"document_ids": ["d1"]}
        )
        assert [h.id for h in hits] == ["c1"]
        hits = store.dense_search(
            [1.0, 0.0, 0.0], top_k=5, filters={"chunk_types": ["table"]}
        )
        assert [h.id for h in hits] == ["c2"]


class TestSqliteVecDenseBruteForce:
    """sqlite-vec 扩展不可用时的暴力余弦兜底路径（同一语义）。"""

    @pytest.fixture()
    def store(self, db_path):
        s = SqliteVecStore(db_path)
        s._vec_available = False  # 强制走暴力余弦分支
        yield s
        s.close()

    def test_dense_search_ranking_brute(self, store):
        store.upsert(
            [
                _item("near", vec=[1.0, 0.0, 0.0]),
                _item("far", vec=[0.0, 1.0, 0.0]),
            ]
        )
        hits = store.dense_search([1.0, 0.0, 0.0], top_k=2)
        assert [h.id for h in hits] == ["near", "far"]

    def test_dense_search_with_filters_brute(self, store):
        store.upsert(
            [
                _item("c1", doc="d1", vec=[1.0, 0.0, 0.0]),
                _item("c2", doc="d2", vec=[0.9, 0.1, 0.0]),
            ]
        )
        hits = store.dense_search(
            [1.0, 0.0, 0.0], top_k=5, filters={"document_ids": ["d1"]}
        )
        assert [h.id for h in hits] == ["c1"]


class TestSqliteVecSparse:
    def test_sparse_cjk_bigram_hit(self, store):
        store.upsert([_item("c1", content="参考文献著录规则")])
        hits = store.sparse_search("参考文献", top_k=5)
        assert any(h.id == "c1" for h in hits)

    def test_sparse_dict_query_returns_empty(self, store):
        """sqlite-vec 后端不消费原生稀疏权重 dict（FTS 需要文本）。"""
        store.upsert([_item("c1")])
        assert store.sparse_search({0: 1.0}, top_k=5) == []

    def test_single_char_query_is_expected_to_miss(self, store):
        """单字中文查询在稀疏通道 0 命中是预期行为（与 LanceDB 侧口径一致）。"""
        store.upsert(
            [
                _item("c1", content="参考文献著录规则"),
                _item("c2", content="图像裁剪与镜像变换"),
            ]
        )
        assert store.sparse_search("献", top_k=5) == []
        assert store.sparse_search("龘", top_k=5) == []

    def test_sparse_with_filters(self, store):
        store.upsert(
            [
                _item("c1", doc="d1", content="参考文献著录规则"),
                _item("c2", doc="d2", content="参考文献引用格式"),
            ]
        )
        hits = store.sparse_search(
            "参考文献", top_k=5, filters={"document_ids": ["d1"]}
        )
        assert [h.id for h in hits] == ["c1"]

    def test_sparse_no_duplicate_rows_per_id(self, store):
        """同一 id 在 FTS JOIN 结果中不得出现多次（影子行累积的另一面）。"""
        store.upsert([_item("c1", content="参考文献")])
        store.upsert([_item("c1", content="参考文献")])
        hits = store.sparse_search("参考文献", top_k=10)
        ids = [h.id for h in hits]
        assert len(ids) == len(set(ids))


class TestSqliteVecRestart:
    def test_fts_visible_after_restart(self, db_path):
        """新实例（模拟进程重启）应能检索到此前写入的行。"""
        store = SqliteVecStore(db_path)
        store.upsert([_item("c1", content="苹果香蕉", vec=[1.0, 0.0, 0.0])])
        store.close()

        restarted = SqliteVecStore(db_path)
        try:
            assert any(
                h.id == "c1" for h in restarted.sparse_search("苹果", top_k=5)
            )
            hits = restarted.dense_search([1.0, 0.0, 0.0], top_k=5)
            assert any(h.id == "c1" for h in hits)
        finally:
            restarted.close()


class TestSqliteVecMisc:
    def test_drop_table(self, store):
        store.upsert([_item("c1")])
        store.drop_table("tessera_chunks")
        assert store.sparse_search("内容", top_k=5) == []

    def test_health(self, store):
        assert store.health() is True
