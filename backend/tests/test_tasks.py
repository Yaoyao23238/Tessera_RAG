"""任务编排测试：run_chunk / run_embed / run_experiment / 崩溃恢复。

core 层全部 fake 注入；DB 用临时 SQLite；不走进程池、不需要网络/模型。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from conftest import FakeEmbedder, FakeLLMClient, FakeVectorStore, make_paragraph
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models.db import (
    Base,
    Chunk,
    ChunkStatsSnapshot,
    Document,
    DocStatus,
    Task,
    TaskStatus,
)
from app.services.runtime import RuntimeContext
from app.workers.tasks import (
    recover_interrupted_tasks,
    run_chunk,
    run_embed,
)
from sqlalchemy import select


class TestModelReadyStatus:
    def test_ready_status_without_side_effects(self, tmp_path):
        """model_ready_status 不触发实例构建/权重加载，只反映已构建实例状态。"""
        settings = Settings(data_dir=tmp_path / "data")
        settings.ensure_dirs()
        runtime = RuntimeContext(settings)
        # 未构建实例时 False（且不应因查询而构建）
        assert runtime.model_ready_status() == {
            "embedder_ready": False,
            "reranker_ready": False,
        }
        runtime.set_embedder(FakeEmbedder())
        status = runtime.model_ready_status()
        assert status["embedder_ready"] is True
        assert status["reranker_ready"] is False


class Harness:
    """测试装配：临时 data 目录 + fake 单例 + 独立 async engine。"""

    def __init__(self, tmp_path: Path) -> None:
        self.settings = Settings(data_dir=tmp_path / "data")
        self.settings.ensure_dirs()
        self.runtime = RuntimeContext(self.settings)
        self.runtime.set_embedder(FakeEmbedder())
        self.store = FakeVectorStore()
        self.runtime.set_vector_store(self.store)
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.settings.db_path.as_posix()}"
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init_db(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def add_document(self, **kwargs) -> Document:
        defaults = {
            "filename": "测试.md",
            "file_path": "/tmp/测试.md",
            "file_hash": "hash_abc",
            "file_type": "md",
            "status": DocStatus.PARSED.value,
        }
        defaults.update(kwargs)
        doc = Document(**defaults)
        async with self.factory() as session:
            session.add(doc)
            await session.commit()
        return doc

    async def get_document(self, doc_id: str) -> Document:
        async with self.factory() as session:
            return await session.get(Document, doc_id)

    async def dispose(self) -> None:
        await self.engine.dispose()


def _seed_parse_cache(harness: Harness, file_hash: str = "hash_abc") -> None:
    blocks = [
        make_paragraph(f"第{i}段内容，用于测试切分任务编排。").to_dict()
        for i in range(6)
    ]
    harness.runtime.cache.set_parse(file_hash, blocks)


def test_run_chunk_success(tmp_path):
    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        _seed_parse_cache(harness)
        doc = await harness.add_document()
        await run_chunk(doc.id, harness.runtime, harness.factory)

        doc = await harness.get_document(doc.id)
        assert doc.status == DocStatus.CHUNKED.value
        assert doc.chunk_count > 0
        async with harness.factory() as session:
            chunks = (
                (await session.execute(select(Chunk).where(Chunk.document_id == doc.id)))
                .scalars()
                .all()
            )
            assert len(chunks) == doc.chunk_count
            # 未向量化前 is_embedded 全为 False
            assert all(not c.is_embedded for c in chunks)
            # 统计快照已落库
            snaps = (
                (
                    await session.execute(
                        select(ChunkStatsSnapshot).where(
                            ChunkStatsSnapshot.document_id == doc.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(snaps) == 1
            assert snaps[0].version == 1
            assert snaps[0].chunk_count == doc.chunk_count
            # 任务完成
            task = (
                (await session.execute(select(Task))).scalars().all()[0]
            )
            assert task.status == TaskStatus.COMPLETED.value
        await harness.dispose()

    asyncio.run(main())


def test_snapshot_retention_keeps_latest_five(tmp_path):
    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        _seed_parse_cache(harness)
        doc = await harness.add_document()
        for _ in range(6):
            await run_chunk(doc.id, harness.runtime, harness.factory)
        async with harness.factory() as session:
            snaps = (
                (
                    await session.execute(
                        select(ChunkStatsSnapshot)
                        .where(ChunkStatsSnapshot.document_id == doc.id)
                        .order_by(ChunkStatsSnapshot.version)
                    )
                )
                .scalars()
                .all()
            )
            assert len(snaps) == 5
            assert [s.version for s in snaps] == [2, 3, 4, 5, 6]
        await harness.dispose()

    asyncio.run(main())


def test_chunk_cache_skipped_when_llm_enabled(tmp_path):
    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        _seed_parse_cache(harness)
        doc = await harness.add_document()
        config_hash = harness.runtime.chunking_config().config_hash()

        # 默认（LLM 关闭）：写切分缓存
        await run_chunk(doc.id, harness.runtime, harness.factory)
        assert harness.runtime.cache.get_chunk(doc.file_hash, config_hash) is not None

        # 开启 LLM：不写不读切分缓存
        harness.runtime._settings["chunking"]["enable_llm"] = True
        harness.runtime.set_llm_client(FakeLLMClient())
        llm_config_hash = harness.runtime.chunking_config().config_hash()
        harness.runtime.cache.clear("chunk")
        await run_chunk(doc.id, harness.runtime, harness.factory)
        assert (
            harness.runtime.cache.get_chunk(doc.file_hash, llm_config_hash) is None
        )
        await harness.dispose()

    asyncio.run(main())


def test_run_embed_indexed(tmp_path):
    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        _seed_parse_cache(harness)
        doc = await harness.add_document()
        await run_chunk(doc.id, harness.runtime, harness.factory)
        await run_embed(doc.id, harness.runtime, harness.factory)

        doc = await harness.get_document(doc.id)
        assert doc.status == DocStatus.INDEXED.value
        assert doc.embedded_count > 0
        # 向量写入主库表
        assert len(harness.store.tables.get("tessera_chunks", {})) > 0
        async with harness.factory() as session:
            embedded = (
                (
                    await session.execute(
                        select(Chunk).where(
                            Chunk.document_id == doc.id, Chunk.is_embedded.is_(True)
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(embedded) == doc.embedded_count
            assert all(c.embedding_model == "fake-embedder" for c in embedded)
        await harness.dispose()

    asyncio.run(main())


class _FailStore(FakeVectorStore):
    def upsert(self, items, table="tessera_chunks"):
        raise RuntimeError("disk full")


class _CountingEmbedder(FakeEmbedder):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return super().embed(texts)


def test_store_failed_retry_reuses_embed_cache(tmp_path):
    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        _seed_parse_cache(harness)
        doc = await harness.add_document()
        await run_chunk(doc.id, harness.runtime, harness.factory)

        # 第一次：写库失败 → store_failed（向量已入缓存）
        harness.runtime.set_vector_store(_FailStore())
        await run_embed(doc.id, harness.runtime, harness.factory)
        doc = await harness.get_document(doc.id)
        assert doc.status == DocStatus.STORE_FAILED.value

        # 第二次：换好库重试 → embedding 缓存命中，不再调用 embedder
        counting = _CountingEmbedder()
        harness.runtime.set_embedder(counting)
        harness.runtime.set_vector_store(FakeVectorStore())
        await run_embed(doc.id, harness.runtime, harness.factory)
        doc = await harness.get_document(doc.id)
        assert doc.status == DocStatus.INDEXED.value
        assert counting.calls == 0  # 全部命中 embedding 缓存
        await harness.dispose()

    asyncio.run(main())


def test_embed_failed(tmp_path):
    class BadEmbedder(FakeEmbedder):
        def embed(self, texts):
            raise RuntimeError("model exploded")

    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        _seed_parse_cache(harness)
        harness.runtime.set_embedder(BadEmbedder())
        doc = await harness.add_document()
        await run_chunk(doc.id, harness.runtime, harness.factory)
        await run_embed(doc.id, harness.runtime, harness.factory)
        doc = await harness.get_document(doc.id)
        assert doc.status == DocStatus.EMBED_FAILED.value
        assert "model exploded" in (doc.error_message or "")
        await harness.dispose()

    asyncio.run(main())


def test_recover_interrupted_tasks(tmp_path):
    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        doc = await harness.add_document(status=DocStatus.PARSING.value)
        async with harness.factory() as session:
            session.add(
                Task(document_id=doc.id, task_type="parse", status=TaskStatus.RUNNING.value)
            )
            session.add(
                Task(document_id=None, task_type="chunk", status=TaskStatus.PENDING.value)
            )
            session.add(
                Task(
                    document_id=None,
                    task_type="embed",
                    status=TaskStatus.COMPLETED.value,
                )
            )
            await session.commit()
        affected = await recover_interrupted_tasks(harness.factory)
        assert affected == 2
        doc = await harness.get_document(doc.id)
        assert doc.status == DocStatus.PARSE_FAILED.value
        async with harness.factory() as session:
            statuses = [
                t.status for t in (await session.execute(select(Task))).scalars().all()
            ]
        assert statuses.count(TaskStatus.FAILED.value) == 2
        assert TaskStatus.COMPLETED.value in statuses
        await harness.dispose()

    asyncio.run(main())


# ---------------------------------------------------------------------------
# run_clean（LLM 清洗：raw 缓存 → cleaned 缓存 → variant 切换）
# ---------------------------------------------------------------------------


def test_run_clean_success(tmp_path):
    from app.workers.tasks import run_clean

    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        harness.runtime.dispatch_enabled = False  # 阻断连锁 run_chunk
        _seed_parse_cache(harness)
        doc = await harness.add_document()
        fake_llm = FakeLLMClient(
            [
                # 6 个段落块一批返回（标记 1-6）
                "\n\n".join(
                    f"<<<BLOCK {i}>>>\n清洗后第{i}段" for i in range(1, 7)
                )
            ]
        )
        harness.runtime.set_llm_client(fake_llm)
        await run_clean(doc.id, harness.runtime, harness.factory)

        doc = await harness.get_document(doc.id)
        assert doc.parse_variant == "cleaned"
        cleaned = harness.runtime.cache.get_parse_cleaned("hash_abc")
        assert cleaned is not None
        assert cleaned["blocks"][0]["text"] == "清洗后第1段"
        assert cleaned["meta"]["prompt_version"]
        assert cleaned["meta"]["usage"]["total_tokens"] == 15
        assert cleaned["meta"]["model_name"] == "fake-llm"
        # 原始缓存不被覆盖
        raw = harness.runtime.cache.get_parse("hash_abc")
        assert raw[0]["text"].startswith("第0段")
        await harness.dispose()

    asyncio.run(main())


def test_run_clean_without_llm_fails(tmp_path):
    from app.workers.tasks import run_clean

    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        harness.runtime.dispatch_enabled = False
        _seed_parse_cache(harness)
        doc = await harness.add_document()
        # 不注入 LLM：llm_client() 返回 None
        await run_clean(doc.id, harness.runtime, harness.factory)

        doc = await harness.get_document(doc.id)
        assert doc.parse_variant == "raw"  # 失败不切换版本
        assert harness.runtime.cache.get_parse_cleaned("hash_abc") is None
        async with harness.factory() as session:
            task = (await session.execute(select(Task))).scalars().first()
        assert task.status == TaskStatus.FAILED.value
        assert "未配置 LLM" in (task.error or "")
        await harness.dispose()

    asyncio.run(main())


def test_run_clean_missing_parse_cache(tmp_path):
    from app.workers.tasks import run_clean

    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        harness.runtime.dispatch_enabled = False
        doc = await harness.add_document()
        harness.runtime.set_llm_client(FakeLLMClient())
        await run_clean(doc.id, harness.runtime, harness.factory)

        doc = await harness.get_document(doc.id)
        assert doc.parse_variant == "raw"
        async with harness.factory() as session:
            task = (await session.execute(select(Task))).scalars().first()
        assert task.status == TaskStatus.FAILED.value
        assert "解析缓存" in (task.error or "")
        await harness.dispose()

    asyncio.run(main())


def test_load_blocks_prefers_cleaned_variant(tmp_path):
    """variant=cleaned 时切分链路读清洗版；清洗缓存缺失回退原始版。"""

    async def main():
        from app.workers.tasks import _load_blocks

        harness = Harness(tmp_path)
        await harness.init_db()
        _seed_parse_cache(harness)
        harness.runtime.cache.set_parse_cleaned(
            "hash_abc",
            [make_paragraph("清洗版文本").to_dict()],
            {"prompt_version": "clean-v1"},
        )
        doc = await harness.add_document(parse_variant="cleaned")
        blocks = await _load_blocks(doc, harness.runtime)
        assert len(blocks) == 1
        assert blocks[0].text == "清洗版文本"

        # 清洗缓存被删 → 回退原始版
        harness.runtime.cache.clear_parse_cleaned("hash_abc")
        blocks = await _load_blocks(doc, harness.runtime)
        assert len(blocks) == 6
        await harness.dispose()

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 缓存失效缺口回归（重解析/清洗后旧切分缓存必须作废）
# ---------------------------------------------------------------------------


def test_run_parse_clears_chunk_caches(tmp_path):
    """重解析后 raw+cleaned 两变体切分缓存与旧 chunks 一并作废。

    缺口：切分缓存 key 只含 file_hash+variant+config_hash，不含解析器版本，
    修复解析器后重解析若不清缓存，手动切分仍命中旧结果。
    """
    from app.workers.tasks import run_parse

    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        _seed_parse_cache(harness)  # 解析缓存命中，不走进程池
        doc = await harness.add_document()
        config_hash = harness.runtime.chunking_config().config_hash()
        payload = {"parents": [], "children": [], "config_hash": config_hash}
        harness.runtime.cache.set_chunk(
            doc.file_hash, config_hash, payload, variant="raw"
        )
        harness.runtime.cache.set_chunk(
            doc.file_hash, config_hash, payload, variant="cleaned"
        )
        async with harness.factory() as session:
            session.add(
                Chunk(
                    id="stale",
                    document_id=doc.id,
                    content="旧切分块",
                    chunk_index=0,
                    token_count=3,
                )
            )
            await session.commit()

        await run_parse(doc.id, harness.runtime, harness.factory)

        doc = await harness.get_document(doc.id)
        assert doc.status == DocStatus.PARSED.value
        assert doc.chunk_count == 0
        assert (
            harness.runtime.cache.get_chunk(doc.file_hash, config_hash, "raw") is None
        )
        assert (
            harness.runtime.cache.get_chunk(doc.file_hash, config_hash, "cleaned")
            is None
        )
        async with harness.factory() as session:
            rows = (
                (
                    await session.execute(
                        select(Chunk).where(Chunk.document_id == doc.id)
                    )
                )
                .scalars()
                .all()
            )
            assert rows == []
        await harness.dispose()

    asyncio.run(main())


def test_run_clean_clears_cleaned_chunk_cache(tmp_path):
    """清洗产物更新后 cleaned 变体切分缓存作废，raw 变体保留可复用。

    缺口：切分缓存 key 不含 text_scope/clean_table，不同清洗参数
    会命中同一份旧切分结果。
    """
    from app.workers.tasks import run_clean

    async def main():
        harness = Harness(tmp_path)
        await harness.init_db()
        harness.runtime.dispatch_enabled = False  # 阻断连锁 run_chunk
        _seed_parse_cache(harness)
        doc = await harness.add_document()
        config_hash = harness.runtime.chunking_config().config_hash()
        payload = {"parents": [], "children": [], "config_hash": config_hash}
        harness.runtime.cache.set_chunk(
            doc.file_hash, config_hash, payload, variant="raw"
        )
        harness.runtime.cache.set_chunk(
            doc.file_hash, config_hash, payload, variant="cleaned"
        )
        fake_llm = FakeLLMClient(
            ["\n\n".join(f"<<<BLOCK {i}>>>\n清洗后第{i}段" for i in range(1, 7))]
        )
        harness.runtime.set_llm_client(fake_llm)

        await run_clean(doc.id, harness.runtime, harness.factory)

        assert (
            harness.runtime.cache.get_chunk(doc.file_hash, config_hash, "cleaned")
            is None
        )
        assert (
            harness.runtime.cache.get_chunk(doc.file_hash, config_hash, "raw")
            is not None
        )
        await harness.dispose()

    asyncio.run(main())
