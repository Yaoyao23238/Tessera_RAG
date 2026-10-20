"""评测 API：Q&A 测试集管理 + 实验运行/历史/删除（级联 drop_table）。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import (
    CODE_LLM_CONNECTION_FAILED,
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    fail,
    ok,
)
from app.core.evaluator import Evaluator
from app.core.types import Chunk, ChunkType
from app.core.vector_store import experiment_table_name
from app.database import get_db, get_session_factory
from app.models.db import (
    Chunk as ChunkRow,
    Document,
    Experiment,
    ExpStatus,
    QAItem,
    QASet,
    QASetSource,
    _uuid,
)
from app.models.schemas import (
    ExperimentCreateRequest,
    ExperimentOut,
    QASetGenerateRequest,
    QASetOut,
)
from app.services.runtime import RuntimeContext
from app.workers import tasks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


def _runtime(request: Request) -> RuntimeContext:
    return request.app.state.runtime


# ---------------------------------------------------------------------------
# Q&A 测试集
# ---------------------------------------------------------------------------


def _parse_qa_file(raw: bytes) -> list[dict]:
    """解析上传的 Q&A 文件（JSON 数组 / {"items":[...]} / JSONL）。"""
    # utf-8-sig：兼容带 BOM 的文件（PS 5.1 / Windows 记事本导出默认带 BOM，
    # 不剥 BOM 会让首行 JSON 解析失败）；无 BOM 时行为与 utf-8 一致
    text = raw.decode("utf-8-sig").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("items", [])
        return [item for item in data if isinstance(item, dict) and item.get("question")]
    except json.JSONDecodeError:
        items = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("question"):
                items.append(item)
        return items


@router.post("/qasets")
async def upload_qaset(
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Form(None),
    session: AsyncSession = Depends(get_db),
):
    """上传 Q&A 测试集（JSON/JSONL）。name 为 multipart 表单字段（可选）。"""
    raw = await file.read()
    items = _parse_qa_file(raw)
    if not items:
        return fail(CODE_PARAM_ERROR, "未解析到有效 Q&A 条目（需含 question 字段）")
    runtime = _runtime(request)
    qaset_dir = runtime.app_settings.data_dir / "qasets"
    qaset_dir.mkdir(parents=True, exist_ok=True)
    # id 必须显式生成：列默认值 default=_uuid 在 flush 时才赋值，
    # 用 qaset.id 拼文件名会拼出 None.json，多测试集互相覆盖
    qaset = QASet(
        id=_uuid(),
        name=name or (file.filename or "未命名测试集"),
        source=QASetSource.UPLOAD.value,
        item_count=len(items),
    )
    file_path = qaset_dir / f"{qaset.id}.json"
    file_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    qaset.file_path = str(file_path)
    session.add(qaset)
    await session.flush()
    for item in items:
        source_chunk_id = item.get("source_chunk_id")
        source_content = item.get("content")
        # 上传带 source_chunk_id 的测试集：反查主库 chunk 内容作快照，
        # 否则实验重切后无法命中
        if source_chunk_id and not source_content:
            row = await session.get(ChunkRow, source_chunk_id)
            if row is not None:
                source_content = row.content
        # 文档级期望（expect_document）并入 meta 供命中判定，
        # 免加 DB 列（QAItem.meta 直通 evaluator）
        meta = dict(item.get("meta") or {})
        expect_document = item.get("expect_document")
        if expect_document:
            meta.setdefault("expect_document", expect_document)
        session.add(
            QAItem(
                qa_set_id=qaset.id,
                question=str(item["question"]),
                answer=str(item.get("answer", "")),
                source_chunk_id=source_chunk_id,
                source_content=source_content,
                meta=meta,
            )
        )
    await session.commit()
    return ok(QASetOut.model_validate(qaset).model_dump(mode="json"))


@router.get("/qasets")
async def list_qasets(session: AsyncSession = Depends(get_db)):
    rows = (
        (await session.execute(select(QASet).order_by(QASet.created_at.desc())))
        .scalars()
        .all()
    )
    return ok(
        [QASetOut.model_validate(row).model_dump(mode="json") for row in rows]
    )


@router.delete("/qasets/{qaset_id}")
async def delete_qaset(qaset_id: str, session: AsyncSession = Depends(get_db)):
    qaset = await session.get(QASet, qaset_id)
    if qaset is None:
        return fail(CODE_NOT_FOUND, "测试集不存在")
    await session.execute(delete(QAItem).where(QAItem.qa_set_id == qaset_id))
    if qaset.file_path:
        from app.services.file_storage import delete_file

        delete_file(qaset.file_path)
    await session.delete(qaset)
    await session.commit()
    return ok({"deleted": qaset_id})


@router.post("/qasets/generate")
async def generate_qaset(
    body: QASetGenerateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """LLM 自动生成 Q&A（按 chunk 均匀抽样，默认 20 条）。"""
    runtime = _runtime(request)
    llm = runtime.llm_client()
    if llm is None:
        return fail(CODE_LLM_CONNECTION_FAILED, "未配置 LLM provider")
    doc = await session.get(Document, body.document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    rows = (
        (
            await session.execute(
                select(ChunkRow).where(ChunkRow.document_id == body.document_id)
            )
        )
        .scalars()
        .all()
    )
    chunks = [
        Chunk(
            id=row.id,
            content=row.content,
            chunk_type=ChunkType(row.chunk_type),
            breadcrumb=row.breadcrumb,
            page_number=row.page_number,
            token_count=row.token_count,
        )
        for row in rows
    ]
    if not chunks:
        return fail(CODE_PARAM_ERROR, "文档尚未切分")
    evaluator = Evaluator(runtime.embedder, runtime.vector_store, llm_client=llm)
    items = await asyncio.to_thread(evaluator.generate_qa, chunks, body.count)
    if not items:
        return fail(CODE_LLM_CONNECTION_FAILED, "LLM 生成失败或无有效输出")

    qaset = QASet(
        name=f"{doc.title} 自动生成（{len(items)} 条）",
        source=QASetSource.LLM_GENERATED.value,
        document_id=doc.id,
        item_count=len(items),
    )
    session.add(qaset)
    await session.flush()
    for item in items:
        session.add(
            QAItem(
                qa_set_id=qaset.id,
                question=item.question,
                answer=item.answer,
                source_chunk_id=item.source_chunk_id,
                source_content=item.source_content,
                meta=item.meta,
            )
        )
    await session.commit()
    return ok(QASetOut.model_validate(qaset).model_dump(mode="json"))


# ---------------------------------------------------------------------------
# 实验
# ---------------------------------------------------------------------------


@router.post("/experiments")
async def create_experiment(
    body: ExperimentCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """创建并后台运行实验（独立向量表 exp_{id前8位}）。"""
    qaset = await session.get(QASet, body.qa_set_id)
    if qaset is None:
        return fail(CODE_NOT_FOUND, "测试集不存在")
    if not body.document_ids:
        return fail(CODE_PARAM_ERROR, "document_ids 不能为空")
    runtime = _runtime(request)
    exp = Experiment(
        id=uuid.uuid4().hex,
        name=body.name
        or f"exp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        qa_set_id=body.qa_set_id,
        chunking_config=body.chunking_config.model_dump(),
        embedding_model=runtime.get("embedding").get("model_name", ""),
        document_ids=body.document_ids,
        status=ExpStatus.PENDING.value,
    )
    exp.vector_table = experiment_table_name(exp.id)
    session.add(exp)
    await session.commit()
    tasks.dispatch_background(
        tasks.run_experiment_task, exp.id, runtime, get_session_factory(), runtime=runtime
    )
    return ok(ExperimentOut.model_validate(exp).model_dump(mode="json"))


@router.get("/experiments")
async def list_experiments(session: AsyncSession = Depends(get_db)):
    rows = (
        (
            await session.execute(
                select(Experiment).order_by(Experiment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return ok(
        [ExperimentOut.model_validate(row).model_dump(mode="json") for row in rows]
    )


@router.delete("/experiments/{experiment_id}")
async def delete_experiment(
    request: Request, experiment_id: str, session: AsyncSession = Depends(get_db)
):
    """删除实验并级联 drop_table 清理独立索引。"""
    exp = await session.get(Experiment, experiment_id)
    if exp is None:
        return fail(CODE_NOT_FOUND, "实验不存在")
    runtime = _runtime(request)
    if exp.vector_table:
        try:
            runtime.vector_store.drop_table(exp.vector_table)
        except Exception:  # noqa: BLE001
            logger.warning("实验表删除失败：%s", exp.vector_table, exc_info=True)
    await session.delete(exp)
    await session.commit()
    return ok({"deleted": experiment_id, "dropped_table": exp.vector_table})
