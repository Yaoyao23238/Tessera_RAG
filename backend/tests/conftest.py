"""pytest 共享 fixture：伪造 embedder / LLM / 向量库，隔离外部依赖。

测试不依赖网络、GPU、docling、torch、lancedb。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

# 保证可以 import app.*（backend 目录加入 sys.path）
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.types import (  # noqa: E402
    Block,
    BlockType,
    ChunkingConfig,
    EmbeddingResult,
)
from app.core.vector_store import StoreHit  # noqa: E402


class FakeEmbedder:
    """确定性伪造 embedder：文本 hash → 8 维向量，无需任何模型。"""

    def __init__(self, dimensions: int = 8) -> None:
        self._dimensions = dimensions

    def is_loaded(self) -> bool:
        """Embedder 接口：fake 无权重加载，恒为就绪。"""
        return True

    def embed(self, texts: list[str]) -> EmbeddingResult:
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vec = [b / 255.0 for b in digest[: self._dimensions]]
            vectors.append(vec)
        return EmbeddingResult(dense=vectors, sparse=None)

    def dimensions(self) -> int:
        return self._dimensions

    def model_name(self) -> str:
        return "fake-embedder"


class FakeLLMClient:
    """伪造 LLM：按队列返回预设响应，无网络调用。"""

    model_name = "fake-llm"

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        self.calls.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return '{"should_merge": false, "confidence": 0.5, "summary": ""}'

    def chat_with_usage(self, messages: list[dict[str, str]], **kwargs):
        """供 LLM 清洗链路使用：返回 (文本, usage)。"""
        text = self.chat(messages, **kwargs)
        return text, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


class FakeVectorStore:
    """内存向量库：按稠密余弦相似度检索，稀疏路返回空。"""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, tuple[list[float], str]]] = {}
        degraded_from = None

    def upsert(self, items, table: str = "tessera_chunks") -> None:
        bucket = self.tables.setdefault(table, {})
        for item in items:
            bucket[item.id] = (item.vector, item.content)

    def dense_search(self, query_vec, top_k=10, filters=None, table="tessera_chunks"):
        bucket = self.tables.get(table, {})
        scored = []
        for chunk_id, (vec, content) in bucket.items():
            dot = sum(a * b for a, b in zip(query_vec, vec))
            na = sum(a * a for a in query_vec) ** 0.5
            nb = sum(b * b for b in vec) ** 0.5
            score = dot / (na * nb) if na and nb else 0.0
            scored.append(StoreHit(id=chunk_id, score=score, content=content))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    def sparse_search(self, query, top_k=10, filters=None, table="tessera_chunks"):
        return []

    def delete_by_document(self, document_id, table="tessera_chunks") -> None:
        pass

    def drop_table(self, table: str) -> None:
        self.tables.pop(table, None)

    def health(self) -> bool:
        return True


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def fake_store() -> FakeVectorStore:
    return FakeVectorStore()


@pytest.fixture
def config() -> ChunkingConfig:
    return ChunkingConfig(
        chunk_size=64,
        chunk_overlap=8,
        parent_chunk_size=128,
        child_chunk_size=40,
        enable_parent_child=True,
        enable_semantic=False,
        enable_llm=False,
        min_chunk_size=5,
    )


def make_heading(text: str, level: int = 1, page: int = 1) -> Block:
    return Block(type=BlockType.HEADING, text=text, level=level, page=page)


def make_paragraph(text: str, page: int = 1) -> Block:
    return Block(type=BlockType.PARAGRAPH, text=text, page=page)


def make_table(text: str = "| a | b |\n|---|---|\n| 1 | 2 |", page: int = 1) -> Block:
    return Block(
        type=BlockType.TABLE,
        text=text,
        page=page,
        table_data={"markdown": text, "rows": [["1", "2"]], "headers": ["a", "b"]},
    )


def make_list_item(text: str, level: int = 0, page: int = 1) -> Block:
    return Block(type=BlockType.LIST_ITEM, text=text, list_level=level, page=page)


def make_image(text: str = "", page: int = 1) -> Block:
    """图片块：text 为空即「无文本内容的图片」，聚合后内容为占位符 [图片]。"""
    return Block(type=BlockType.IMAGE, text=text, page=page)
