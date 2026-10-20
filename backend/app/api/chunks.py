"""切分 API：触发切分 / 结果查询（分页、类型筛选、关键词）/ 确认入库。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import CODE_NOT_FOUND, CODE_PARAM_ERROR, fail, ok
from app.database import get_db, get_session_factory
from app.models.db import Chunk, Document, DocStatus
from app.models.schemas import ChunkOut, ChunkRequest
from app.services.runtime import RuntimeContext
from app.workers import tasks

router = APIRouter(prefix="/documents", tags=["chunks"])

# 允许触发切分的状态
_CHUNKABLE = {
    DocStatus.PARSED.value,
    DocStatus.CHUNKED.value,
    DocStatus.INDEXED.value,
    DocStatus.CHUNK_FAILED.value,
}


def _runtime(request: Request) -> RuntimeContext:
    return request.app.state.runtime


@router.post("/{document_id}/chunk")
async def trigger_chunk(
    request: Request,
    document_id: str,
    body: ChunkRequest | None = None,
    session: AsyncSession = Depends(get_db),
):
    """触发切分（body 可带参数覆盖全局设置）。调参重切从 chunked/indexed 回退。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    if doc.status not in _CHUNKABLE:
        return fail(
            CODE_PARAM_ERROR, f"当前状态 {doc.status} 不可切分（需先完成解析）"
        )
    override = body.config.model_dump() if body and body.config else None
    runtime = _runtime(request)
    tasks.dispatch_background(
        tasks.run_chunk,
        doc.id,
        runtime,
        get_session_factory(),
        config_override=override,
        runtime=runtime,
    )
    return ok({"dispatched": "chunk", "document_id": doc.id})


@router.get("/{document_id}/chunks")
async def list_chunks(
    document_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    chunk_type: str | None = Query(None),
    keyword: str | None = Query(None),
    role: str = Query("all", pattern="^(all|parent|child)$"),
    session: AsyncSession = Depends(get_db),
):
    """切分结果查询（分页 / 类型筛选 / 关键词 / 父子角色）。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")

    query = select(Chunk).where(Chunk.document_id == document_id)
    count_query = select(func.count(Chunk.id)).where(
        Chunk.document_id == document_id
    )
    if chunk_type:
        query = query.where(Chunk.chunk_type == chunk_type)
        count_query = count_query.where(Chunk.chunk_type == chunk_type)
    if keyword:
        like = f"%{keyword}%"
        query = query.where(Chunk.content.like(like))
        count_query = count_query.where(Chunk.content.like(like))
    if role != "all":
        parent_ids = (
            select(Chunk.parent_id)
            .where(Chunk.parent_id.is_not(None))
            .distinct()
        )
        if role == "parent":
            query = query.where(Chunk.id.in_(parent_ids))
            count_query = count_query.where(Chunk.id.in_(parent_ids))
        else:
            query = query.where(~Chunk.id.in_(parent_ids))
            count_query = count_query.where(~Chunk.id.in_(parent_ids))

    total = (await session.execute(count_query)).scalar() or 0
    rows = (
        (
            await session.execute(
                query.order_by(Chunk.created_at, Chunk.chunk_index)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return ok(
        {
            "items": [
                ChunkOut.model_validate(row).model_dump(mode="json") for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/{document_id}/chunks/{chunk_id}")
async def get_chunk(
    document_id: str, chunk_id: str, session: AsyncSession = Depends(get_db)
):
    row = await session.get(Chunk, chunk_id)
    if row is None or row.document_id != document_id:
        return fail(CODE_NOT_FOUND, "chunk 不存在")
    return ok(ChunkOut.model_validate(row).model_dump(mode="json"))


@router.post("/{document_id}/index")
async def confirm_index(
    request: Request, document_id: str, session: AsyncSession = Depends(get_db)
):
    """确认入库（向量化 + 写向量库）。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    if doc.status not in {
        DocStatus.CHUNKED.value,
        DocStatus.INDEXED.value,
        DocStatus.EMBED_FAILED.value,
        DocStatus.STORE_FAILED.value,
    }:
        return fail(
            CODE_PARAM_ERROR, f"当前状态 {doc.status} 不可入库（需先完成切分）"
        )
    runtime = _runtime(request)
    tasks.dispatch_background(
        tasks.run_embed, doc.id, runtime, get_session_factory(), runtime=runtime
    )
    return ok({"dispatched": "embed", "document_id": doc.id})
