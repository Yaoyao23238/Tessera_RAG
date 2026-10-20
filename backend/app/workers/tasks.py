"""任务编排：parse / chunk / embed / evaluate 全链路。

要点（对应 DESIGN 4.1/4.3 时序与遗留问题）：
- 解析缓存命中则跳过 Docling；LLM 开启时不走切分缓存
- embed 与 store 分两阶段：embed 失败 → embed_failed；写库失败 →
  store_failed（重试时 embedding 缓存命中，跳过重算）
- 切分完成后写统计快照（每文档保留最近 5 版）
- 崩溃恢复：启动时 pending/running 任务标记 failed
- CPU 密集（Docling 解析）走进程池；切分/向量化需共享模型单例，
  走 asyncio.to_thread（模型无法跨 spawn 进程传递）
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import struct
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.chunker import Chunker
from app.core.heading_infer import infer_headings
from app.core.evaluator import Evaluator
from app.core.metadata import extract_metadata
from app.core.parser import (
    EmptyDocumentError,
    EncryptedPDFError,
    Parser,
    ParserError,
)
from app.core.stats import compute_stats
from app.core.types import Block, ChunkingConfig, ChunkResult, QAItemData
from app.core.vector_store import MAIN_TABLE, VectorItem
from app.models.db import (
    Chunk,
    ChunkStatsSnapshot,
    Document,
    DocStatus,
    Experiment,
    ExpStatus,
    QAItem,
    Task,
    TaskStatus,
)
from app.services.file_storage import text_hash
from app.services.runtime import RuntimeContext
from app.workers.pool import run_cpu

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]

# 后台任务集合（防 GC + 异常落日志）
_BG_TASKS: set[asyncio.Task] = set()

_EMBED_BATCH_SIZE = 32
_SNAPSHOT_KEEP = 5  # 统计快照每文档保留最近 5 版


def _ws():
    """懒导入 WS 推送辅助（避免 workers→api 的模块级循环依赖）。"""
    from app.api import ws

    return ws


def dispatch_background(
    fn: Callable[..., Any], *args: Any, runtime: RuntimeContext, **kwargs: Any
) -> asyncio.Task | None:
    """分发后台任务；runtime.dispatch_enabled=False 时（测试）跳过。"""
    if not getattr(runtime, "dispatch_enabled", True):
        return None

    async def _runner() -> None:
        try:
            await fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("后台任务执行失败：%s", getattr(fn, "__name__", fn))

    task = asyncio.create_task(_runner())
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


async def _push(
    task: Task, status: str, progress: int, message: str = ""
) -> None:
    try:
        await _ws().push_task_progress(
            task.id, task.document_id, status, progress, message
        )
    except Exception:  # noqa: BLE001
        logger.debug("WS 推送失败（无连接时正常）", exc_info=True)


async def _create_task(
    session: AsyncSession, document_id: str | None, task_type: str
) -> Task:
    task = Task(
        document_id=document_id,
        task_type=task_type,
        status=TaskStatus.RUNNING.value,
        started_at=datetime.now(timezone.utc),
    )
    session.add(task)
    await session.flush()
    return task


def _finish_task(
    task: Task, ok: bool, message: str = "", error: str | None = None
) -> None:
    task.status = TaskStatus.COMPLETED.value if ok else TaskStatus.FAILED.value
    task.progress = 100 if ok else task.progress
    task.message = message
    task.error = error
    task.completed_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def _parse_entrypoint(file_path: str, password: str | None) -> tuple[list[dict], dict]:
    """进程池入口（必须模块级可 pickle）：Docling 解析 → Block dict 列表 + 元数据。

    Returns:
        (blocks_data, meta_dict) — blocks_data 为 Block dict 列表，
        meta_dict 为 {title, author, lang}。
    """
    blocks, meta = Parser().parse_with_meta(file_path, password=password)
    return [b.to_dict() for b in blocks], meta


async def run_parse(
    document_id: str,
    runtime: RuntimeContext,
    session_factory: SessionFactory,
    password: str | None = None,
) -> None:
    """解析任务：缓存命中跳过 Docling；完成后停在 parsed 等用户验收。

    用户动线：解析完成 → 解析预览页验收（必要时修正/LLM 清洗）
    → 手动触发切分。重解析后旧 chunks/向量作废，在此一并清理。
    """
    async with session_factory() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            return
        doc.status = DocStatus.PARSING.value
        doc.error_message = None
        task = await _create_task(session, document_id, "parse")
        await session.commit()
        await _push(task, DocStatus.PARSING.value, 10, "解析中")

        try:
            blocks_data = runtime.cache.get_parse(doc.file_hash)
            docling_meta: dict = {}
            if blocks_data is None:
                blocks_data, docling_meta = await run_cpu(
                    _parse_entrypoint, doc.file_path, password
                )
                if not blocks_data:
                    raise EmptyDocumentError("未检测到文本内容")
                runtime.cache.set_parse(doc.file_hash, blocks_data)
            doc.status = DocStatus.PARSED.value
            doc.parse_cache_path = str(runtime.cache.parse_path(doc.file_hash))
            doc.page_count = max(
                (b.get("end_page", 1) for b in blocks_data), default=1
            )
            # 元数据填充：title/author/lang（规则提取 + Docling 元数据合并）
            blocks = [Block.from_dict(d) for d in blocks_data]
            meta = extract_metadata(blocks, docling_meta)
            if meta.get("title"):
                doc.title = meta["title"]
            if meta.get("author"):
                doc.author = meta["author"]
            if meta.get("lang"):
                doc.lang = meta["lang"]
            # 重解析后旧切分产物作废：清 chunks + 向量 + 切分缓存（失败不阻断）。
            # 切分缓存 key 只含 file_hash+variant+config_hash，不含解析器版本，
            # 不清则修复解析器后重解析仍命中旧切分结果
            await session.execute(
                delete(Chunk).where(Chunk.document_id == document_id)
            )
            doc.chunk_count = 0
            doc.embedded_count = 0
            try:
                runtime.vector_store.delete_by_document(document_id)
            except Exception:  # noqa: BLE001
                logger.warning("清理旧向量失败（不阻断解析）", exc_info=True)
            runtime.cache.clear_document_caches(doc.file_hash)
            _finish_task(task, True, "解析完成")
            await session.commit()
            await _push(task, DocStatus.PARSED.value, 100, "解析完成")
        except EncryptedPDFError as exc:
            doc.status = DocStatus.ENCRYPTED.value
            doc.error_message = str(exc)  # 密码不落盘
            _finish_task(task, False, error=str(exc))
            await session.commit()
            await _push(task, DocStatus.ENCRYPTED.value, 0, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - 含 ParserError 及进程池异常
            doc.status = DocStatus.PARSE_FAILED.value
            doc.error_message = str(exc)
            _finish_task(task, False, error=str(exc))
            await session.commit()
            await _push(task, DocStatus.PARSE_FAILED.value, 0, str(exc))
            return

    # 解析完成 → 停在 parsed 状态，不再自动切分。
    # 用户在「解析预览」页验收后，手动触发切分（文档列表/解析预览页
    # 的「开始切分」按钮）。LLM 清洗（run_clean）仍保留清洗后自动重切。


# ---------------------------------------------------------------------------
# LLM 清洗（解析预览页：基于原始解析缓存，产物写 cleaned 缓存）
# ---------------------------------------------------------------------------


def _push_clean_progress(
    task_id: str, document_id: str, done: int, total: int
) -> None:
    """清洗进度（工作线程内调用，线程安全广播）。"""
    progress = 10 + int(85 * done / max(total, 1))
    try:
        _ws().manager.broadcast_threadsafe(
            {
                "type": "task_progress",
                "task_id": task_id,
                "document_id": document_id,
                "payload": {
                    "status": "cleaning",
                    "progress": progress,
                    "message": f"LLM 清洗中 {done}/{total}",
                },
            }
        )
    except Exception:  # noqa: BLE001
        logger.debug("清洗进度推送失败", exc_info=True)


async def run_clean(
    document_id: str,
    runtime: RuntimeContext,
    session_factory: SessionFactory,
    text_scope: str = "full",
    clean_table: bool = False,
) -> None:
    """LLM 清洗任务：raw 缓存 → 清洗 → cleaned 缓存 → 切 variant → 自动重切。

    清洗始终基于原始解析缓存（重复清洗不叠加）；raw 缓存永不被覆盖，
    回退原始版只需切 parse_variant。
    text_scope：off / problem（仅问题块）/ full（全文清洗）。
    """
    from app.core.cleaner import BlockCleaner

    async with session_factory() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            return
        task = await _create_task(session, document_id, "clean")
        await session.commit()
        await _push(task, "cleaning", 5, "LLM 清洗准备中")

        try:
            llm = runtime.llm_client()
            if llm is None:
                raise ParserError("未配置 LLM，请先在设置页配置并启用模型")

            raw_data = runtime.cache.get_parse(doc.file_hash)
            if not raw_data:
                raise ParserError("缺少解析缓存，请先重新解析")
            blocks = [Block.from_dict(d) for d in raw_data]

            result = await asyncio.to_thread(
                lambda: BlockCleaner(llm).clean(
                    blocks,
                    text_scope=text_scope,
                    clean_table=clean_table,
                    progress_cb=lambda done, total: _push_clean_progress(
                        task.id, document_id, done, total
                    ),
                )
            )

            meta = {
                **result.meta,
                "model_name": llm.model_name,
                "text_scope": text_scope,
                "clean_table": clean_table,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            # 清洗不改块类型（1:1 映射），标题推断在清洗结果上再跑一次：
            # 清洗合并断行后标题文本可能变化，且 raw 缓存可能早于推断功能生成
            infer_headings(result.blocks)
            runtime.cache.set_parse_cleaned(
                doc.file_hash, [b.to_dict() for b in result.blocks], meta
            )
            # 清洗产物已更新：旧 cleaned 变体切分缓存作废（key 不含清洗参数，
            # 不同 text_scope/clean_table 清洗结果不能复用同一切分缓存）；
            # raw 变体缓存不受影响，切回原始版仍可复用
            runtime.cache.clear_chunk(doc.file_hash, variant="cleaned")
            doc.parse_variant = "cleaned"
            total_tokens = result.usage.get("total_tokens", 0)
            message = f"清洗完成（{result.batches} 批，{total_tokens} tokens）"
            if result.failed_batches:
                message += f"，{result.failed_batches} 批失败保留原文"
            _finish_task(task, True, message)
            await session.commit()
            await _push(task, "cleaned", 100, message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM 清洗失败：%s", document_id)
            doc.error_message = str(exc)
            _finish_task(task, False, error=str(exc))
            await session.commit()
            await _push(task, DocStatus.PARSE_FAILED.value, 0, str(exc))
            return

    # 清洗完成 → 自动重切（让清洗结果生效到切分链路）
    dispatch_background(
        run_chunk, document_id, runtime, session_factory, runtime=runtime
    )


# ---------------------------------------------------------------------------
# 切分（含统计快照落库与 5 版剪枝）
# ---------------------------------------------------------------------------


def _chunk_to_orm(item: Any, document_id: str) -> Chunk:
    """core Chunk dataclass → ORM（is_embedded 重置为 False：尚未向量化）。"""
    return Chunk(
        id=item.id,
        document_id=document_id,
        parent_id=item.parent_id,
        chunk_index=item.chunk_index,
        content=item.content,
        chunk_type=item.chunk_type.value,
        breadcrumb=item.breadcrumb,
        page_number=item.page_number,
        end_page=item.end_page,
        token_count=item.token_count,
        char_count=item.char_count,
        strategy=item.strategy,
        bbox=item.bbox,
        block_ids=item.block_ids,
        table_data=item.table_data,
        heading_text=item.heading_text,
        is_embedded=False,
    )


async def _load_blocks(
    doc: Document, runtime: RuntimeContext
) -> list[Block]:
    """从解析缓存加载 Block 列表（按文档当前 parse_variant 选原始/清洗版）。

    variant=cleaned 但清洗缓存缺失时回退原始版（清洗缓存被 LRU 淘汰等场景），
    保证切分链路始终可用。
    """
    variant = getattr(doc, "parse_variant", "raw") or "raw"
    if variant == "cleaned":
        cleaned = runtime.cache.get_parse_cleaned(doc.file_hash)
        if cleaned is not None:
            return [Block.from_dict(d) for d in cleaned.get("blocks", [])]
        logger.warning(
            "清洗缓存缺失，回退原始解析版：%s", doc.file_hash[:8]
        )
    blocks_data = runtime.cache.get_parse(doc.file_hash)
    if blocks_data is None and doc.parse_cache_path:
        path = Path(doc.parse_cache_path)
        if path.is_file():
            blocks_data = json.loads(path.read_text(encoding="utf-8"))
    if not blocks_data:
        raise ParserError("缺少解析缓存，请先重新解析")
    return [Block.from_dict(d) for d in blocks_data]


def _chunk_document(
    blocks: list[Block],
    config: ChunkingConfig,
    document_id: str,
    runtime: RuntimeContext,
) -> ChunkResult:
    """同步执行切分（线程内调用）。"""
    embedder = runtime.embedder if config.enable_semantic else None
    llm = runtime.llm_client() if config.enable_llm else None
    return Chunker(embedder=embedder, llm_client=llm).chunk(
        blocks, config, document_id
    )


async def run_chunk(
    document_id: str,
    runtime: RuntimeContext,
    session_factory: SessionFactory,
    config_override: dict[str, Any] | None = None,
) -> None:
    """切分任务：缓存（LLM 开启除外）→ 切分 → 落库 → 统计快照。"""
    async with session_factory() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            return
        doc.status = DocStatus.CHUNKING.value
        doc.error_message = None
        task = await _create_task(session, document_id, "chunk")
        await session.commit()
        await _push(task, DocStatus.CHUNKING.value, 10, "切分中")

        try:
            config = runtime.chunking_config()
            if config_override:
                config = ChunkingConfig.from_dict(
                    {**config.to_dict(), **config_override}
                )
            config_hash = config.config_hash()
            variant = getattr(doc, "parse_variant", "raw") or "raw"
            blocks = await _load_blocks(doc, runtime)

            result: ChunkResult | None = None
            # LLM 开启时不走切分缓存（输出有随机性）
            if not config.enable_llm:
                cached = runtime.cache.get_chunk(doc.file_hash, config_hash, variant)
                if cached is not None:
                    result = ChunkResult.from_dict(cached)
                    logger.info(
                        "切分缓存命中：%s/%s/%s",
                        doc.file_hash[:8],
                        variant,
                        config_hash,
                    )
            if result is None:
                result = await asyncio.to_thread(
                    _chunk_document, blocks, config, document_id, runtime
                )
                if not config.enable_llm:
                    runtime.cache.set_chunk(
                        doc.file_hash, config_hash, result.to_dict(), variant
                    )

            # 重切：清理旧 chunks 与旧向量
            await session.execute(
                delete(Chunk).where(Chunk.document_id == document_id)
            )
            try:
                runtime.vector_store.delete_by_document(document_id)
            except Exception:  # noqa: BLE001
                logger.warning("清理旧向量失败（不阻断切分）", exc_info=True)

            rows = [
                _chunk_to_orm(item, document_id) for item in result.all_chunks
            ]
            session.add_all(rows)
            doc.status = DocStatus.CHUNKED.value
            doc.chunk_count = len(rows)
            doc.embedded_count = 0

            # 统计快照落库 + 每文档保留最近 5 版
            stats = compute_stats(result.all_chunks)
            max_version = (
                await session.execute(
                    select(func.max(ChunkStatsSnapshot.version)).where(
                        ChunkStatsSnapshot.document_id == document_id
                    )
                )
            ).scalar() or 0
            session.add(
                ChunkStatsSnapshot(
                    document_id=document_id,
                    config_hash=config_hash,
                    version=max_version + 1,
                    histogram=stats.histogram,
                    type_counts=stats.type_counts,
                    mean_tokens=stats.mean_tokens,
                    median_tokens=stats.median_tokens,
                    chunk_count=stats.chunk_count,
                )
            )
            await session.flush()
            snapshot_ids = (
                (
                    await session.execute(
                        select(ChunkStatsSnapshot.id)
                        .where(ChunkStatsSnapshot.document_id == document_id)
                        .order_by(ChunkStatsSnapshot.version.desc())
                    )
                )
                .scalars()
                .all()
            )
            for stale_id in snapshot_ids[_SNAPSHOT_KEEP:]:
                await session.execute(
                    delete(ChunkStatsSnapshot).where(
                        ChunkStatsSnapshot.id == stale_id
                    )
                )

            _finish_task(task, True, f"切分完成：{len(rows)} 块")
            await session.commit()
            await _push(task, DocStatus.CHUNKED.value, 100, "切分完成")
        except Exception as exc:  # noqa: BLE001
            logger.exception("切分失败：%s", document_id)
            doc.status = DocStatus.CHUNK_FAILED.value
            doc.error_message = str(exc)
            _finish_task(task, False, error=str(exc))
            await session.commit()
            await _push(task, DocStatus.CHUNK_FAILED.value, 0, str(exc))


# ---------------------------------------------------------------------------
# 向量化 + 入库（embedding 缓存复用，store_failed 可重试）
# ---------------------------------------------------------------------------


def _dump_vector(vector: list[float]) -> bytes:
    """向量序列化为 bytes（优先 numpy .npy，缺失时 struct float32）。"""
    try:
        import numpy as np

        buf = io.BytesIO()
        np.save(buf, np.asarray(vector, dtype=np.float32))
        return buf.getvalue()
    except ImportError:
        return b"RAWF" + struct.pack(f"<{len(vector)}f", *vector)


def _load_vector(blob: bytes) -> list[float] | None:
    try:
        if blob[:4] == b"RAWF":
            count = (len(blob) - 4) // 4
            return list(struct.unpack(f"<{count}f", blob[4:]))
        import numpy as np

        return np.load(io.BytesIO(blob)).astype(float).tolist()
    except Exception:  # noqa: BLE001
        return None


async def run_embed(
    document_id: str,
    runtime: RuntimeContext,
    session_factory: SessionFactory,
) -> None:
    """向量化任务：缓存命中跳过重算；写库失败置 store_failed 可重试。"""
    async with session_factory() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            return
        doc.status = DocStatus.EMBEDDING.value
        doc.error_message = None
        task = await _create_task(session, document_id, "embed")
        await session.commit()
        await _push(task, DocStatus.EMBEDDING.value, 10, "向量化中")

        # 子块 = 未被任何块引用为 parent 的块（父块不向量化）
        parent_ids = select(Chunk.parent_id).where(
            Chunk.parent_id.is_not(None)
        ).distinct()
        children = (
            (
                await session.execute(
                    select(Chunk)
                    .where(Chunk.document_id == document_id, ~Chunk.id.in_(parent_ids))
                    .order_by(Chunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )

        embedder = runtime.embedder
        model_name = embedder.model_name()
        vectors: dict[str, list[float]] = {}
        sparse_weights: dict[str, dict[int, float]] = {}
        missing: list[Chunk] = []
        for child in children:
            blob = runtime.cache.get_embed(text_hash(child.content), model_name)
            vector = _load_vector(blob) if blob else None
            if vector is not None:
                vectors[child.id] = vector
            else:
                missing.append(child)

        try:
            for start in range(0, len(missing), _EMBED_BATCH_SIZE):
                batch = missing[start : start + _EMBED_BATCH_SIZE]
                result = await asyncio.to_thread(
                    embedder.embed, [c.content for c in batch]
                )
                for i, child in enumerate(batch):
                    vectors[child.id] = result.dense[i]
                    if result.sparse:
                        sparse_weights[child.id] = result.sparse[i]
                    runtime.cache.set_embed(
                        text_hash(child.content),
                        model_name,
                        _dump_vector(result.dense[i]),
                    )
                progress = 10 + int(
                    60 * min(start + len(batch), len(missing)) / max(len(missing), 1)
                )
                await _push(task, DocStatus.EMBEDDING.value, progress, "向量化中")
        except Exception as exc:  # noqa: BLE001
            logger.exception("向量化失败：%s", document_id)
            doc.status = DocStatus.EMBED_FAILED.value
            doc.error_message = str(exc)
            _finish_task(task, False, error=str(exc))
            await session.commit()
            await _push(task, DocStatus.EMBED_FAILED.value, 0, str(exc))
            return

        items = [
            VectorItem(
                id=child.id,
                vector=vectors[child.id],
                sparse=sparse_weights.get(child.id),
                content=child.content,
                document_id=document_id,
                chunk_type=child.chunk_type,
                metadata={
                    "breadcrumb": child.breadcrumb,
                    "page_number": child.page_number,
                    "source_file": doc.filename,
                },
            )
            for child in children
        ]
        try:
            await asyncio.to_thread(
                runtime.vector_store.upsert, items, MAIN_TABLE
            )
        except Exception as exc:  # noqa: BLE001
            # 写库失败：重试时 embedding 缓存命中，跳过重算
            logger.exception("向量写库失败：%s", document_id)
            doc.status = DocStatus.STORE_FAILED.value
            doc.error_message = str(exc)
            _finish_task(task, False, error=str(exc))
            await session.commit()
            await _push(task, DocStatus.STORE_FAILED.value, 0, str(exc))
            return

        for child in children:
            child.is_embedded = True
            child.embedding_model = model_name
            child.vector_id = child.id
        doc.status = DocStatus.INDEXED.value
        doc.embedded_count = len(children)
        _finish_task(task, True, f"入库完成：{len(children)} 向量")
        await session.commit()
        await _push(task, DocStatus.INDEXED.value, 100, "入库完成")


# ---------------------------------------------------------------------------
# 评测实验
# ---------------------------------------------------------------------------


async def run_experiment_task(
    experiment_id: str,
    runtime: RuntimeContext,
    session_factory: SessionFactory,
) -> None:
    """实验任务：按实验配置重切（或取缓存）→ 独立表索引 → 逐条检索 → 指标。"""
    async with session_factory() as session:
        exp = await session.get(Experiment, experiment_id)
        if exp is None:
            return
        exp.status = ExpStatus.RUNNING.value
        await session.commit()
        _ws().push_experiment_progress(experiment_id, 0, 1)

        try:
            qa_rows = (
                (
                    await session.execute(
                        select(QAItem).where(QAItem.qa_set_id == exp.qa_set_id)
                    )
                )
                .scalars()
                .all()
            )
            qa_items = [
                QAItemData(
                    id=row.id,
                    question=row.question,
                    answer=row.answer,
                    source_chunk_id=row.source_chunk_id,
                    source_content=row.source_content,
                    meta=row.meta,
                )
                for row in qa_rows
            ]
            config = ChunkingConfig.from_dict(exp.chunking_config)
            config_hash = config.config_hash()

            all_chunks = []
            doc_names: dict[str, str] = {}
            for doc_id in exp.document_ids:
                doc = await session.get(Document, doc_id)
                if doc is None:
                    continue
                doc_names[doc_id] = doc.filename
                blocks = await _load_blocks(doc, runtime)
                variant = getattr(doc, "parse_variant", "raw") or "raw"
                result: ChunkResult | None = None
                if not config.enable_llm:
                    cached = runtime.cache.get_chunk(
                        doc.file_hash, config_hash, variant
                    )
                    if cached is not None:
                        result = ChunkResult.from_dict(cached)
                if result is None:
                    result = await asyncio.to_thread(
                        _chunk_document, blocks, config, doc_id, runtime
                    )
                # 实验重切使用独立 chunk id 命名空间，避免与主库冲突
                for chunk in result.all_chunks:
                    chunk.id = f"{experiment_id[:8]}_{chunk.id}"
                    if chunk.parent_id:
                        chunk.parent_id = f"{experiment_id[:8]}_{chunk.parent_id}"
                all_chunks.extend(result.all_chunks)

            evaluator = Evaluator(
                embedder=runtime.embedder,
                store=runtime.vector_store,
                reranker=None,  # 实验聚焦切分质量，不启用 rerank
            )
            progress_cb = lambda cur, total: _ws().push_experiment_progress(  # noqa: E731
                experiment_id, cur, total
            )
            metrics = await asyncio.to_thread(
                evaluator.run_experiment,
                all_chunks,
                qa_items,
                exp.vector_table,
                10,
                progress_cb,
                doc_names,
            )
            exp.recall_at_1 = metrics.recall_at_1
            exp.recall_at_5 = metrics.recall_at_5
            exp.recall_at_10 = metrics.recall_at_10
            exp.mrr = metrics.mrr
            exp.status = ExpStatus.COMPLETED.value
            exp.completed_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("实验失败：%s", experiment_id)
            exp.status = ExpStatus.FAILED.value
            exp.error_message = str(exc)
            await session.commit()


# ---------------------------------------------------------------------------
# 崩溃恢复
# ---------------------------------------------------------------------------


async def recover_interrupted_tasks(session_factory: SessionFactory) -> int:
    """启动扫描：pending/running 任务标记 failed，中断文档回退到对应失败态。

    Returns:
        被标记失败的任务数。
    """
    async with session_factory() as session:
        result = await session.execute(
            update(Task)
            .where(Task.status.in_([TaskStatus.PENDING.value, TaskStatus.RUNNING.value]))
            .values(
                status=TaskStatus.FAILED.value,
                error="服务重启，任务中断",
                completed_at=datetime.now(timezone.utc),
            )
        )
        affected = result.rowcount or 0
        # 中断状态机回退
        status_map = {
            DocStatus.PARSING.value: DocStatus.PARSE_FAILED.value,
            DocStatus.CHUNKING.value: DocStatus.CHUNK_FAILED.value,
            DocStatus.EMBEDDING.value: DocStatus.EMBED_FAILED.value,
        }
        for intermediate, failed in status_map.items():
            await session.execute(
                update(Document)
                .where(Document.status == intermediate)
                .values(status=failed, error_message="服务重启，任务中断")
            )
        await session.commit()
        if affected:
            logger.warning("崩溃恢复：标记 %d 个中断任务为 failed", affected)
        return affected
