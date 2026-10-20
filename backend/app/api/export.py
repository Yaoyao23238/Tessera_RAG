"""JSONL 导出 API：alpaca / Dify 双模板，章节勾选过滤。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import CODE_PARAM_ERROR, fail, ok
from app.core.exporter import (
    Exporter,
    document_flow_order,
    strip_cot_from_chunks,
)
from app.core.types import Chunk, ChunkType
from app.database import get_db
from app.models.db import Chunk as ChunkRow
from app.models.schemas import ExportRequest

router = APIRouter(prefix="/export", tags=["export"])


@router.post("/jsonl")
async def export_jsonl(
    body: ExportRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """导出 JSONL（只导出内容块：子块 + 独立块，父块不重复导出）。"""
    if not body.document_ids:
        return fail(CODE_PARAM_ERROR, "document_ids 不能为空")
    if body.format not in ("alpaca", "dify"):
        return fail(CODE_PARAM_ERROR, "format 须为 alpaca 或 dify")

    rows = (
        (
            await session.execute(
                select(ChunkRow)
                .where(ChunkRow.document_id.in_(body.document_ids))
                .order_by(ChunkRow.document_id, ChunkRow.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return fail(CODE_PARAM_ERROR, "所选文档没有可导出的 chunk")

    chunks = [
        Chunk(
            id=row.id,
            document_id=row.document_id,
            parent_id=row.parent_id,
            chunk_index=row.chunk_index,
            content=row.content,
            chunk_type=ChunkType(row.chunk_type),
            breadcrumb=row.breadcrumb,
            heading_text=row.heading_text,
            token_count=row.token_count,
        )
        for row in rows
    ]
    # 文档流重建后排除父块（父块不导出，与原 SQL 排除口径一致）：
    # chunk_index 子块/顶层两套编号空间混排会交错，乱序会让思维链
    # 状态机误删正文（详见 exporter.document_flow_order）
    flow = document_flow_order(chunks)
    referenced = {c.parent_id for c in chunks if c.parent_id}
    chunks = [c for c in flow if c.id not in referenced]
    if not chunks:
        return fail(CODE_PARAM_ERROR, "所选文档没有可导出的 chunk")
    runtime = request.app.state.runtime
    exporter = Exporter(runtime.app_settings.export_dir)
    # 思维链清洗在章节过滤之前（export_jsonl 内部做章节过滤）：
    # 状态机依赖 chunk 连续性，先清洗再过滤才能正确跨越思维链区间
    stripped = 0
    if body.strip_cot:
        chunks, stripped = strip_cot_from_chunks(chunks)
        if not chunks:
            return fail(CODE_PARAM_ERROR, "清洗思维链后无可导出的 chunk")
    path, count = exporter.export_jsonl(
        chunks, fmt=body.format, sections=body.sections
    )
    return ok(
        {
            "file_path": str(path),
            "line_count": count,
            "format": body.format,
            "stripped_chunks": stripped,
        }
    )
