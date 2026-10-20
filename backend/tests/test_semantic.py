"""语义断崖切分测试：句子切分 / 相似度 / 断崖点 / semantic_split。"""

from __future__ import annotations

from app.core.semantic import (
    adjacent_similarities,
    cosine_similarity,
    find_cliff_points,
    semantic_split,
    split_sentences,
)


class TestSplitSentences:
    def test_chinese_sentences(self):
        text = "第一句话。第二句话！第三句话？"
        assert split_sentences(text) == ["第一句话。", "第二句话！", "第三句话？"]

    def test_english_sentences(self):
        text = "First sentence. Second sentence! Third one?"
        result = split_sentences(text)
        assert len(result) == 3

    def test_empty_text(self):
        assert split_sentences("") == []


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_zero_vector(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestFindCliffPoints:
    def test_cliff_below_mu_minus_sigma(self):
        # 8 个高相似 + 1 个极低相似 → 低处为断崖
        sims = [0.9, 0.9, 0.9, 0.1, 0.9, 0.9, 0.9, 0.9, 0.9]
        cliffs = find_cliff_points(sims, threshold=0.3)
        assert 3 in cliffs
        # 最优断崖排最前
        assert cliffs[0] == 3

    def test_no_cliff_when_uniform(self):
        sims = [0.8, 0.8, 0.8, 0.8]
        assert find_cliff_points(sims, threshold=0.3) == []

    def test_threshold_floor(self):
        # μ-σ 低于 threshold 时以 threshold 为准：
        # sims=[0.4,0.5,0.4,0.5] → μ=0.45, σ=0.05, μ-σ=0.40
        # threshold=0.3 → cutoff=0.40 → 无断崖
        sims = [0.4, 0.5, 0.4, 0.5]
        assert find_cliff_points(sims, threshold=0.3) == []
        # threshold=0.45 → cutoff=0.45 → 0.4 处为断崖
        assert find_cliff_points(sims, threshold=0.45) == [0, 2]


class TestSemanticSplit:
    @staticmethod
    def _group_embed_fn(groups: dict[str, int], dim: int = 4):
        """按首字符分组给单向量：不同组正交 → 组间必断崖。"""

        def embed(texts: list[str]) -> list[list[float]]:
            vectors = []
            for text in texts:
                idx = groups.get(text[0], 0)
                vec = [0.0] * dim
                vec[idx % dim] = 1.0
                vectors.append(vec)
            return vectors

        return embed

    def test_split_at_cliff(self):
        sentences = [f"A第{i}句。" for i in range(5)] + [f"B第{i}句。" for i in range(5)]
        text = "".join(sentences)
        embed_fn = self._group_embed_fn({"A": 0, "B": 1})
        parts = semantic_split(text, embed_fn, threshold=0.3, max_tokens=1000)
        assert parts is not None
        assert len(parts) == 2
        assert parts[0].startswith("A")
        assert parts[1].startswith("B")

    def test_returns_none_without_embed_fn(self):
        text = "第一句。第二句。第三句。第四句。"
        assert semantic_split(text, None, 0.3, 100) is None

    def test_returns_none_when_embed_fails(self):
        def bad_embed(texts):
            raise RuntimeError("model not loaded")

        text = "第一句。第二句。第三句。第四句。"
        assert semantic_split(text, bad_embed, 0.3, 100) is None

    def test_returns_none_when_too_few_sentences(self):
        assert semantic_split("只有一句。", lambda t: [[1.0]], 0.3, 100) is None
