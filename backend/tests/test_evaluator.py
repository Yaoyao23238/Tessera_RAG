"""评测引擎测试：抽样 / 命中判定 / Recall@k / MRR / 实验端到端。"""

from __future__ import annotations

import pytest
from conftest import FakeEmbedder, FakeLLMClient, FakeVectorStore

from app.core.evaluator import (
    OVERLAP_THRESHOLD,
    Evaluator,
    content_overlap,
    hit_rank,
    mean_reciprocal_rank,
    recall_at_k,
    sample_chunks,
)
from app.core.types import Chunk, ChunkType, QAItemData


class TestMetrics:
    def test_recall_at_k(self):
        ranks = [1, None, 3]
        result = recall_at_k(ranks)
        assert result[1] == pytest.approx(1 / 3)
        assert result[5] == pytest.approx(2 / 3)
        assert result[10] == pytest.approx(2 / 3)

    def test_recall_empty(self):
        assert recall_at_k([]) == {1: 0.0, 5: 0.0, 10: 0.0}

    def test_mrr(self):
        # (1/1 + 1/3 + 0) / 3
        assert mean_reciprocal_rank([1, 3, None]) == pytest.approx((1 + 1 / 3) / 3)

    def test_hit_rank_direct(self):
        assert hit_rank(["a", "b"], "b", {}) == 2

    def test_hit_rank_via_parent(self):
        parent_map = {"c1": "p1"}
        assert hit_rank(["p1", "c2"], "c1", parent_map) == 1

    def test_hit_rank_miss(self):
        assert hit_rank(["a"], "b", {}) is None
        assert hit_rank(["a"], None, {}) is None

    def test_hit_rank_via_content(self):
        # 实验重切后 chunk id 全变：ID 匹配失败，内容重叠兜底命中
        source = "实验采用 MNIST 数据集，训练 100 轮。"
        content_map = {"new_id": source}
        assert hit_rank(["new_id"], "旧uuid", {}, content_map, source) == 1
        assert hit_rank(["new_id"], "旧uuid", {}) is None  # 未提供快照时不误判

    def test_hit_rank_content_below_threshold(self):
        # 内容重叠不足阈值：不算命中
        content_map = {"new_id": "完全不相关的内容。"}
        assert (
            hit_rank(["new_id"], "旧uuid", {}, content_map, "长" * 200) is None
        )

    def test_hit_rank_via_document(self):
        # 文档级期望：结果 chunk 所属文档匹配即命中（评测集按文档给期望）
        chunk_doc_names = {"c1": "a.pdf", "c2": "b.pdf"}
        assert (
            hit_rank(
                ["c1", "c2"],
                None,
                {},
                expect_document="b.pdf",
                chunk_doc_names=chunk_doc_names,
            )
            == 2
        )
        assert (
            hit_rank(
                ["c1"],
                None,
                {},
                expect_document="b.pdf",
                chunk_doc_names=chunk_doc_names,
            )
            is None
        )


class TestContentOverlap:
    def test_exact_and_substring(self):
        assert content_overlap("abc", "abc") == 1.0
        assert content_overlap("abc", "xxabcxx") == 1.0
        assert content_overlap("xxabcxx", "abc") == 3 / 7

    def test_window_partial(self):
        # 6 个窗口（50 字符）：3 个命中 → 0.5，超过阈值
        source = ("段" * 50) * 6
        content = "段" * 50 * 3 + "其他" * 50 * 3
        assert content_overlap(source, content) >= OVERLAP_THRESHOLD
        # 无关内容 → 0
        assert content_overlap(source, "无关内容" * 20) == 0.0

    def test_empty(self):
        assert content_overlap("", "x") == 0.0


class TestSampleChunks:
    def test_skip_table_and_image(self):
        chunks = [
            Chunk(content=f"文本{i}", chunk_type=ChunkType.TEXT, token_count=10)
            for i in range(8)
        ]
        chunks.append(Chunk(content="表格", chunk_type=ChunkType.TABLE))
        chunks.append(Chunk(content="[图片]", chunk_type=ChunkType.IMAGE))
        sampled = sample_chunks(chunks, count=4)
        assert len(sampled) == 4
        assert all(c.chunk_type == ChunkType.TEXT for c in sampled)

    def test_fewer_chunks_than_count(self):
        chunks = [Chunk(content="唯一", token_count=10)]
        assert len(sample_chunks(chunks, count=20)) == 1

    def test_empty(self):
        assert sample_chunks([], 20) == []


class TestGenerateQA:
    def test_generate_with_llm(self):
        chunks = [
            Chunk(content=f"内容{i}", chunk_type=ChunkType.TEXT, token_count=10)
            for i in range(3)
        ]
        llm = FakeLLMClient(
            ['{"question": "问题1？", "answer": "答案1"}'] * 3
        )
        evaluator = Evaluator(FakeEmbedder(), FakeVectorStore(), llm_client=llm)
        items = evaluator.generate_qa(chunks, count=3)
        assert len(items) == 3
        assert all(i.source_chunk_id for i in items)
        assert items[0].question == "问题1？"

    def test_generate_without_llm_raises(self):
        evaluator = Evaluator(FakeEmbedder(), FakeVectorStore())
        with pytest.raises(ValueError):
            evaluator.generate_qa([Chunk(content="x")])


class TestRunExperiment:
    def test_end_to_end(self):
        # 构造父块 + 两个子块；QA 的 question 与子块内容相同
        # （FakeEmbedder 由文本 hash 取向量 → 完全命中 top-1）
        parent = Chunk(content="父块", is_embedded=False)
        child1 = Chunk(
            content="实验采用 MNIST 数据集。",
            parent_id=parent.id,
            is_embedded=True,
            token_count=10,
        )
        child2 = Chunk(
            content="训练使用 Adam 优化器。",
            parent_id=parent.id,
            is_embedded=True,
            token_count=10,
        )
        chunks = [parent, child1, child2]
        qa_items = [
            QAItemData(question=child1.content, source_chunk_id=child1.id),
            QAItemData(question=child2.content, source_chunk_id=child2.id),
        ]
        store = FakeVectorStore()
        evaluator = Evaluator(FakeEmbedder(), store)
        progress: list[tuple[int, int]] = []
        result = evaluator.run_experiment(
            chunks,
            qa_items,
            table="exp_12345678",
            progress_cb=lambda cur, total: progress.append((cur, total)),
        )
        # 实验写入独立表
        assert "exp_12345678" in store.tables
        assert len(store.tables["exp_12345678"]) == 2
        # 全部 top-1 命中
        assert result.recall_at_1 == 1.0
        assert result.recall_at_5 == 1.0
        assert result.recall_at_10 == 1.0
        assert result.mrr == 1.0
        assert result.total == 2
        # 进度回调逐条推进
        assert progress == [(1, 2), (2, 2)]

    def test_partial_miss(self):
        child = Chunk(content="已知内容。", is_embedded=True, token_count=5)
        qa_hit = QAItemData(question="已知内容。", source_chunk_id=child.id)
        qa_miss = QAItemData(question="无关问题", source_chunk_id="不存在的id")
        store = FakeVectorStore()
        evaluator = Evaluator(FakeEmbedder(), store)
        result = evaluator.run_experiment([child], [qa_hit, qa_miss], table="exp_x")
        assert result.recall_at_1 == pytest.approx(0.5)
        assert result.mrr == pytest.approx(0.5)

    def test_rechunked_ids_still_hit_via_content(self):
        """回归：实验重切后 chunk id 全变（新 UUID + 前缀），
        旧 source_chunk_id 匹配不上——内容快照兜底后指标不再恒 0。"""
        source_text = "实验采用 MNIST 数据集，训练 100 轮，准确率 99%。"
        parent = Chunk(content="父块", is_embedded=False)
        child = Chunk(
            content=source_text,
            parent_id=parent.id,
            is_embedded=True,
            token_count=10,
        )
        chunks = [parent, child]
        # QA 的 source_chunk_id 是旧切分时的 id，与实验 chunk 完全不同
        qa_items = [
            QAItemData(
                question="用的什么数据集？",
                source_chunk_id="旧切分的uuid",
                source_content=source_text,
            )
        ]
        store = FakeVectorStore()
        evaluator = Evaluator(FakeEmbedder(), store)
        result = evaluator.run_experiment(chunks, qa_items, table="exp_x")
        assert result.recall_at_1 == 1.0  # 修复前恒 0
        assert result.mrr == 1.0

    def test_document_level_expectation(self):
        """回归：评测集只有 expect_document（无 source_chunk_id）也能命中——
        修复前 hit_rank 无 source_chunk_id 直接判不命中，指标恒 0。"""
        child = Chunk(
            content="本标准规定了参考文献的著录项目。",
            is_embedded=True,
            token_count=10,
            document_id="doc-1",
        )
        qa_items = [
            QAItemData(
                question="参考文献著录项目有哪些",
                meta={"expect_document": "著录规则.pdf"},
            )
        ]
        store = FakeVectorStore()
        evaluator = Evaluator(FakeEmbedder(), store)
        result = evaluator.run_experiment(
            [child],
            qa_items,
            table="exp_x",
            doc_names={"doc-1": "著录规则.pdf"},
        )
        assert result.recall_at_1 == 1.0
        assert result.mrr == 1.0

    def test_negative_excluded_from_denominator(self):
        """回归：负例（无 source_chunk_id 且无 expect_document）不进指标分母，
        否则完美检索也只有 正例数/总条数。"""
        child = Chunk(
            content="已知内容。",
            is_embedded=True,
            token_count=5,
            document_id="doc-1",
        )
        qa_pos = QAItemData(
            question="已知内容。", meta={"expect_document": "a.pdf"}
        )
        qa_neg = QAItemData(question="宋代官窑开片成因是什么")
        store = FakeVectorStore()
        evaluator = Evaluator(FakeEmbedder(), store)
        result = evaluator.run_experiment(
            [child],
            [qa_pos, qa_neg],
            table="exp_x",
            doc_names={"doc-1": "a.pdf"},
        )
        # 只按正例算：完美命中 = 1.0（修复前 0.5）
        assert result.recall_at_1 == 1.0
        assert result.mrr == 1.0
        assert result.total == 2
        assert result.ranks == [1, None]  # 负例 rank 恒 None
