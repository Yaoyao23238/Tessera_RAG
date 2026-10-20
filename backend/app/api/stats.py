"""统计 API：切分统计快照（含与上版对比）+ 仪表盘统计。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import CODE_NOT_FOUND, fail, ok
from app.database import get_db
from app.models.db import Chunk, ChunkStatsSnapshot, Document
from app.models.schemas import ChunkStatsOut
from app.services.file_storage import dir_size

router = APIRouter(tags=["stats"])


def _snapshot_out(row: ChunkStatsSnapshot) -> dict:
    return ChunkStatsOut.model_validate(row).model_dump(mode="json")


@router.get("/documents/{document_id}/chunk-stats")
async def chunk_stats(
    document_id: str,
    compare: str | None = Query(None),
    session: AsyncSession = Depends(get_db),
):
    """最新统计快照；compare=prev 时附带上一版对比。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    rows = (
        (
            await session.execute(
                select(ChunkStatsSnapshot)
                .where(ChunkStatsSnapshot.document_id == document_id)
                .order_by(ChunkStatsSnapshot.version.desc())
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return fail(CODE_NOT_FOUND, "尚无统计快照（请先完成切分）")
    data: dict = {"current": _snapshot_out(rows[0]), "previous": None}
    if compare == "prev" and len(rows) > 1:
        data["previous"] = _snapshot_out(rows[1])
    return ok(data)


@router.get("/stats")
async def dashboard(request: Request, session: AsyncSession = Depends(get_db)):
    """仪表盘统计：文档数 / chunk 数 / 向量数 / 存储大小。"""
    document_count = (
        await session.execute(select(func.count(Document.id)))
    ).scalar() or 0
    chunk_count = (
        await session.execute(select(func.count(Chunk.id)))
    ).scalar() or 0
    embedded_count = (
        await session.execute(
            select(func.count(Chunk.id)).where(Chunk.is_embedded.is_(True))
        )
    ).scalar() or 0
    runtime = request.app.state.runtime
    return ok(
        {
            "document_count": document_count,
            "chunk_count": chunk_count,
            "embedded_count": embedded_count,
            "storage_bytes": dir_size(runtime.app_settings.data_dir),
        }
    )
