"""混合检索测试：RRF 融合 / Rerank 回退 / 父块展开。"""

from __future__ import annotations

import pytest
from conftest import FakeEmbedder

from app.core.retriever import RRF_K, Retriever
from app.core.vector_store import StoreHit


class ScriptedStore:
    """脚本化向量库：返回预设两路结果。"""

    degraded_from = None

    def __init__(self, dense: list[StoreHit], sparse: list[StoreHit]) -> None:
        self._dense = dense
        self._sparse = sparse

    def upsert(self, items, table="tessera_chunks"):
        pass

    def dense_search(self, query_vec, top_k=10, filters=None, table="tessera_chunks"):
        return self._dense[:top_k]

    def sparse_search(self, query, top_k=10, filters=None, table="tessera_chunks"):
        return self._sparse[:top_k]

    def delete_by_document(self, document_id, table="tessera_chunks"):
        pass

    def drop_table(self, table):
        pass

    def health(self):
        return True


def _hits(ids: list[str]) -> list[StoreHit]:
    return [StoreHit(id=i, score=1.0, content=f"内容{i}") for i in ids]


class TestRrfFuse:
    def test_rrf_score_and_order(self):
        dense = _hits(["a", "b", "c"])
        sparse = _hits(["b", "d"])
        fused = Retriever.rrf_fuse(dense, sparse, k=RRF_K)
        order = [c.chunk_id for c in fused]
        # b 两路命中分数叠加，排第一；a 次之（稠密 rank1）
        assert order[0] == "b"
        assert order[1] == "a"
        # b 的分数 = 1/(60+2) + 1/(60+1)
        assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)
        # 排名信息保留
        by_id = {c.chunk_id: c for c in fused}
        assert by_id["a"].dense_rank == 1
        assert by_id["a"].sparse_rank is None
        assert by_id["d"].sparse_rank == 2

    def test_empty_inputs(self):
        assert Retriever.rrf_fuse([], []) == []


class _FailingReranker:
    enabled = True
    top_n = 20

    def rerank(self, query, candidates):
        raise RuntimeError("rerank model broken")


class _ReverseReranker:
    enabled = True
    top_n = 20

    def rerank(self, query, candidates):
        for i, c in enumerate(reversed(candidates)):
            c.rerank_score = float(len(candidates) - i)
        return list(reversed(candidates))


class _ScoredReranker:
    """按预设分数打分（用于验证 min_score 截断）。"""

    enabled = True

    def __init__(self, scores, top_n: int = 20, min_score: float = 0.0) -> None:
        self._scores = list(scores)
        self.top_n = top_n
        self.min_score = min_score

    def rerank(self, query, candidates):
        for c, s in zip(candidates, self._scores):
            c.rerank_score = s
        return sorted(candidates, key=lambda c: c.rerank_score or 0.0, reverse=True)


class TestRerankMinScore:
    """精排最低分阈值：只作用于 rerank_score，默认 0 = 不过滤。"""

    def test_zero_threshold_keeps_all(self):
        store = ScriptedStore(_hits(["a", "b", "c"]), [])
        retriever = Retriever(
            FakeEmbedder(), store, reranker=_ScoredReranker([0.9, 0.01, 0.0001])
        )
        resp = retriever.search("查询", top_k=3)
        assert [r.chunk_id for r in resp.results] == ["a", "b", "c"]

    def test_threshold_filters_low_scores(self):
        # 复现问题 5：精排分 1e-04 的噪声块此前仍占末位返回
        store = ScriptedStore(_hits(["a", "b", "c"]), [])
        retriever = Retriever(
            FakeEmbedder(),
            store,
            reranker=_ScoredReranker([0.9, 0.01, 0.0001], min_score=0.5),
        )
        resp = retriever.search("查询", top_k=3)
        assert [r.chunk_id for r in resp.results] == ["a"]

    def test_threshold_drops_unreranked_tail(self):
        # top_n=1 → tail 未精排，开阈值时一并丢弃（否则低分 tail 会冒到前面）
        store = ScriptedStore(_hits(["a", "b", "c"]), [])
        retriever = Retriever(
            FakeEmbedder(),
            store,
            reranker=_ScoredReranker([0.9], top_n=1, min_score=0.5),
        )
        resp = retriever.search("查询", top_k=3)
        assert [r.chunk_id for r in resp.results] == ["a"]

    def test_threshold_ignored_when_rerank_fails(self):
        # 精排失败时没有分数可比，阈值必须失效（回退 RRF 全量）
        class _FailingWithThreshold(_FailingReranker):
            min_score = 0.99

        store = ScriptedStore(_hits(["a", "b"]), [])
        retriever = Retriever(
            FakeEmbedder(), store, reranker=_FailingWithThreshold()
        )
        resp = retriever.search("查询", top_k=2)
        assert [r.chunk_id for r in resp.results] == ["a", "b"]


class TestSearchPipeline:
    def test_search_returns_rrf_order(self):
        store = ScriptedStore(_hits(["a", "b", "c"]), _hits(["b", "d"]))
        retriever = Retriever(FakeEmbedder(), store)
        resp = retriever.search("查询", top_k=4)
        assert resp.results[0].chunk_id == "b"
        assert resp.query == "查询"
        assert resp.latency_ms >= 0
        assert resp.total == 4

    def test_rerank_failure_falls_back(self):
        store = ScriptedStore(_hits(["a", "b"]), [])
        retriever = Retriever(FakeEmbedder(), store, reranker=_FailingReranker())
        resp = retriever.search("查询", top_k=2)
        # RRF 顺序不变，无 rerank_score
        assert [r.chunk_id for r in resp.results] == ["a", "b"]
        assert all(r.rerank_score is None for r in resp.results)

    def test_rerank_applied(self):
        store = ScriptedStore(_hits(["a", "b"]), [])
        retriever = Retriever(FakeEmbedder(), store, reranker=_ReverseReranker())
        resp = retriever.search("查询", top_k=2)
        assert [r.chunk_id for r in resp.results] == ["b", "a"]
        assert all(r.rerank_score is not None for r in resp.results)

    def test_rerank_disabled_by_flag(self):
        store = ScriptedStore(_hits(["a", "b"]), [])
        retriever = Retriever(FakeEmbedder(), store, reranker=_ReverseReranker())
        resp = retriever.search("查询", top_k=2, use_rerank=False)
        assert [r.chunk_id for r in resp.results] == ["a", "b"]

    def test_parent_expansion(self):
        store = ScriptedStore(_hits(["child1"]), [])

        def resolver(chunk_ids):
            return {
                "child1": {
                    "parent_content": "父块完整内容",
                    "matched_child_text": "内容child1",
                    "source_file": "文档.pdf",
                    "page_number": 7,
                    "breadcrumb": ["第3章", "3.2 实验"],
                    "chunk_type": "text",
                }
            }

        retriever = Retriever(FakeEmbedder(), store, parent_resolver=resolver)
        resp = retriever.search("查询", top_k=1)
        item = resp.results[0]
        assert item.parent_content == "父块完整内容"
        assert item.matched_child_text == "内容child1"
        assert item.source_file == "文档.pdf"
        assert item.page_number == 7
        assert item.breadcrumb == ["第3章", "3.2 实验"]
