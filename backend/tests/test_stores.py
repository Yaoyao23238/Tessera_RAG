"""stores 层真实集成测试（非 fake）：LanceDB 幂等写入 / 删除 / 检索。

使用真实 lancedb + 临时目录，验证与 sqlite-vec 对齐的语义。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.vector_store import (  # noqa: E402
    FTS_TOKENIZER_VERSION,
    VectorItem,
    fts_tokenize,
)

lancedb = pytest.importorskip("lancedb")

from app.core.stores.lancedb_store import LanceDBStore  # noqa: E402


def _item(item_id: str, doc: str = "doc1", content: str = "内容", vec=None) -> VectorItem:
    return VectorItem(
        id=item_id,
        vector=vec or [0.1, 0.2, 0.3],
        content=content,
        document_id=doc,
        chunk_type="text",
        metadata={},
    )


class TestLanceDBUpsert:
    def test_upsert_idempotent_retry(self, tmp_path):
        """同 id 重复 upsert（store_failed 重试场景）不产生重复行。"""
        store = LanceDBStore(tmp_path / "lance")
        store.upsert([_item("c1", content="第一版"), _item("c2")])
        # 模拟重试：同 id 再写一次（内容更新）
        store.upsert([_item("c1", content="第二版"), _item("c2")])
        tbl = store._open_table("tessera_chunks")
        assert tbl.count_rows() == 2
        rows = {r["id"]: r["content"] for r in tbl.to_pandas().to_dict("records")}
        assert rows["c1"] == "第二版"  # 更新而非追加

    def test_upsert_mixed_new_and_existing(self, tmp_path):
        store = LanceDBStore(tmp_path / "lance")
        store.upsert([_item("c1")])
        store.upsert([_item("c1", content="更新"), _item("c2", content="新增")])
        tbl = store._open_table("tessera_chunks")
        assert tbl.count_rows() == 2

    def test_delete_and_dense_search(self, tmp_path):
        store = LanceDBStore(tmp_path / "lance")
        store.upsert(
            [_item("c1", doc="d1", vec=[1.0, 0.0, 0.0]), _item("c2", doc="d2")]
        )
        hits = store.dense_search([1.0, 0.0, 0.0], top_k=5)
        assert hits and hits[0].id == "c1"
        store.delete_by_document("d1")
        hits = store.dense_search([1.0, 0.0, 0.0], top_k=5)
        assert all(h.document_id != "d1" for h in hits)

    def test_fts_consistent_after_upsert(self, tmp_path):
        """建过 FTS 索引后再 upsert，稀疏检索应能命中新内容（索引重建）。"""
        store = LanceDBStore(tmp_path / "lance")
        store.upsert([_item("c1", content="苹果香蕉")])
        first = store.sparse_search("苹果", top_k=5)  # 触发 FTS 建索引
        assert any(h.id == "c1" for h in first)
        # 同 id 更新内容（merge_insert 路径触发 FTS 重建）
        store.upsert([_item("c1", content="橘子葡萄")])
        hits = store.sparse_search("橘子", top_k=5)
        assert any(h.id == "c1" for h in hits)
        # 旧内容不再命中
        stale = store.sparse_search("香蕉", top_k=5)
        assert all(h.id != "c1" for h in stale)

    def test_fts_sees_new_rows_after_store_restart(self, tmp_path):
        """新 store 实例（模拟进程重启）先 upsert 再查，稀疏检索应命中新行。

        问题 7 报告的时序：进程重启 → _fts_ready 为空 → merge_insert 后
        不重建索引 → 索引漏新行。本例在真实 lancedb 上钉住实际行为。
        """
        db_dir = tmp_path / "lance"
        store = LanceDBStore(db_dir)
        store.upsert([_item("c1", content="苹果香蕉")])
        store.sparse_search("苹果", top_k=5)  # 触发建索引

        restarted = LanceDBStore(db_dir)
        assert restarted._fts_ready == set()  # 新进程：索引状态未记录
        restarted.upsert([_item("c2", content="仙人掌柠檬")])
        hits = restarted.sparse_search("仙人掌", top_k=5)
        assert any(h.id == "c2" for h in hits), (
            "重启后首次 upsert 的新行查不到 → 需在 upsert 分支无条件重建索引"
        )

    def test_fts_sees_updated_content_after_store_restart(self, tmp_path):
        """重启后首次 upsert 走的是 when_matched 更新分支，同样要能查到新内容。"""
        db_dir = tmp_path / "lance"
        store = LanceDBStore(db_dir)
        store.upsert([_item("c1", content="苹果香蕉")])
        store.sparse_search("苹果", top_k=5)

        restarted = LanceDBStore(db_dir)
        assert restarted._fts_ready == set()
        restarted.upsert([_item("c1", content="仙人掌柠檬")])
        assert any(h.id == "c1" for h in restarted.sparse_search("仙人掌", top_k=5)), (
            "重启后首次 upsert 更新的内容查不到 → 需在 upsert 分支无条件重建索引"
        )
        assert all(h.id != "c1" for h in restarted.sparse_search("苹果", top_k=5))

    def test_single_char_query_is_expected_to_miss(self, tmp_path):
        """单字中文查询在稀疏通道 0 命中是**预期行为**，不是 bug。

        bigram 索引里没有单字 token（除非该字在原文中是孤立单字段），
        所以单字查询必然查不到。要求：不崩、不返回伪命中、不污染排序。
        别把它当 bug 修成 LIKE 兜底——那会与 rerank 阈值冲突（LIKE 命中的块
        过精排分数必然落在负例区间，开阈值后照样被滤空），等于白做。
        API 层仍有稠密通道兜底，见 retriever 的 dense_search 分支。
        """
        store = LanceDBStore(tmp_path / "lance")
        store.upsert(
            [
                _item("c1", content="参考文献著录规则"),
                _item("c2", content="图像裁剪与镜像变换"),
            ]
        )
        store.sparse_search("参考文献", top_k=5)  # 触发建索引
        assert store.sparse_search("献", top_k=5) == []
        assert store.sparse_search("龘", top_k=5) == []

    def test_isolated_single_char_still_indexed(self, tmp_path):
        """段长为 1 的 CJK 退化单字仍入索引（tokenizer 的兜底语义）。"""
        store = LanceDBStore(tmp_path / "lance")
        store.upsert([_item("c3", content="A中B")])
        store.sparse_search("A", top_k=5)  # 触发建索引
        hits = store.sparse_search("中", top_k=5)
        assert any(h.id == "c3" for h in hits), "孤立单字段应按单字兜底可命中"


class TestFtsTokenize:
    """FTS 分词预处理（版本 2 = CJK 相邻二元）。golden 钉住行为。"""

    def test_cjk_becomes_adjacent_bigrams(self):
        # 「参考文献著录格式」→ 参考/考文/文献/献著/著录/录格/格式
        assert fts_tokenize("参考文献著录格式").split() == [
            "参考", "考文", "文献", "献著", "著录", "录格", "格式",
        ]

    def test_multi_char_run_never_yields_single_chars(self):
        # 逐字分词（版本 1）的病灶：单字 + OR 会让任何含常用字的长文本命中
        tokens = fts_tokenize("图像裁剪与镜像变换").split()
        assert all(len(t) == 2 for t in tokens), tokens
        assert tokens == ["图像", "像裁", "裁剪", "剪与", "与镜", "镜像", "像变", "变换"]

    def test_single_char_run_falls_back_to_single(self):
        assert fts_tokenize("龘").split() == ["龘"]

    def test_non_cjk_kept_verbatim(self):
        # 不能把 FastAPI 拆成 F a s t A P I
        assert fts_tokenize("FastAPI") == "FastAPI"

    def test_mixed_run_boundaries(self):
        assert fts_tokenize("图像API中文").split() == ["图像", "API", "中文"]

    def test_empty_string(self):
        assert fts_tokenize("") == ""

    def test_query_and_index_sides_use_same_tokenizer(self):
        # 写入侧（影子列）与查询侧共用本函数，这是两者一致性的唯一保证
        assert fts_tokenize("参考文献") == fts_tokenize("参考文献")
        assert fts_tokenize("参考文献").split() == ["参考", "考文", "文献"]

    def test_tokenizer_version_is_recorded(self):
        # 分词规则变更必须递增版本号（影子列/索引需随之重建）
        assert FTS_TOKENIZER_VERSION >= 2
