"""解析预览 API：块列表 / 质量报告 / LLM 清洗 / 解析版本切换。

数据源为解析缓存（raw / cleaned 两版并存），不触发 Docling；
breadcrumb 在返回前由 structure.attach_breadcrumbs 现挂（解析阶段不挂）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import CODE_NOT_FOUND, CODE_PARAM_ERROR, fail, ok
from app.core.quality import compute_parse_report
from app.core.structure import attach_breadcrumbs
from app.core.types import Block
from app.database import get_db, get_session_factory
from app.models.db import Document, DocStatus
from app.models.schemas import CleanRequest, ParseVariantRequest
from app.services.runtime import RuntimeContext
from app.workers import tasks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["parse-preview"])

# 允许触发清洗的状态（解析已完成且不在忙碌链路中）
_CLEANABLE = {
    DocStatus.PARSED.value,
    DocStatus.CHUNKED.value,
    DocStatus.INDEXED.value,
    DocStatus.CHUNK_FAILED.value,
    DocStatus.EMBED_FAILED.value,
    DocStatus.STORE_FAILED.value,
}


def _runtime(request: Request) -> RuntimeContext:
    return request.app.state.runtime


def _load_variant_blocks(
    doc: Document, runtime: RuntimeContext, variant: str
) -> tuple[list[Block], dict | None, str | None]:
    """按指定版本读解析缓存。

    Returns:
        (blocks, cleaned_meta, error)：error 非空时前两者无效。
    """
    if variant == "cleaned":
        cleaned = runtime.cache.get_parse_cleaned(doc.file_hash)
        if cleaned is None:
            return [], None, "清洗版缓存不存在（可能已被清理），请重新清洗"
        return (
            [Block.from_dict(d) for d in cleaned.get("blocks", [])],
            cleaned.get("meta"),
            None,
        )
    raw = runtime.cache.get_parse(doc.file_hash)
    if raw is None:
        return [], None, "解析缓存不存在，请先解析文档"
    return [Block.from_dict(d) for d in raw], None, None


@router.get("/{document_id}/blocks")
async def list_parse_blocks(
    request: Request,
    document_id: str,
    variant: str | None = Query(None, pattern="^(raw|cleaned)$"),
    session: AsyncSession = Depends(get_db),
):
    """解析块列表（全量；默认当前生效版本；返回前挂 breadcrumb）。

    全量返回与前端 useAllChunks 模式一致：一般文档数百至数千块，
    类型筛选/搜索在前端完成，保证 chips 角标与过滤结果一致。
    """
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    runtime = _runtime(request)
    effective = variant or getattr(doc, "parse_variant", "raw") or "raw"
    blocks, meta, error = _load_variant_blocks(doc, runtime, effective)
    if error:
        return fail(CODE_NOT_FOUND, error)
    attach_breadcrumbs(blocks)
    return ok(
        {
            "variant": effective,
            "blocks": [b.to_dict() for b in blocks],
            "cleaned_meta": meta,
        }
    )


@router.get("/{document_id}/parse-report")
async def get_parse_report(
    request: Request,
    document_id: str,
    variant: str | None = Query(None, pattern="^(raw|cleaned)$"),
    session: AsyncSession = Depends(get_db),
):
    """解析质量报告：统计摘要 + 4 类质量警告 + LLM 清洗 token 预估。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    runtime = _runtime(request)
    effective = variant or getattr(doc, "parse_variant", "raw") or "raw"
    blocks, meta, error = _load_variant_blocks(doc, runtime, effective)
    if error:
        return fail(CODE_NOT_FOUND, error)
    report = compute_parse_report(blocks)
    data = report.to_dict()
    data["variant"] = effective
    data["cleaned_meta"] = meta
    # 是否有清洗版可用（前端据此显示「切换到清洗版」入口）
    data["has_cleaned"] = runtime.cache.parse_cleaned_path(doc.file_hash).is_file()
    return ok(data)


@router.post("/{document_id}/clean")
async def trigger_clean(
    request: Request,
    document_id: str,
    body: CleanRequest,
    session: AsyncSession = Depends(get_db),
):
    """触发 LLM 清洗（后台任务，task_type=clean，WS 推送进度）。

    清洗始终基于原始解析缓存；产物写 cleaned 缓存并切换 parse_variant，
    完成后自动重切让清洗生效。
    """
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    if doc.status not in _CLEANABLE:
        return fail(
            CODE_PARAM_ERROR, f"当前状态 {doc.status} 不可清洗（需先完成解析）"
        )
    if body.text_scope not in ("off", "problem", "full"):
        return fail(
            CODE_PARAM_ERROR, "text_scope 须为 off / problem / full"
        )
    if body.text_scope == "off" and not body.clean_table:
        return fail(CODE_PARAM_ERROR, "至少启用一项清洗（文本清洗或表格增强）")
    runtime = _runtime(request)
    if runtime.llm_client() is None:
        return fail(CODE_PARAM_ERROR, "未配置 LLM，请先在设置页配置并启用模型")
    tasks.dispatch_background(
        tasks.run_clean,
        doc.id,
        runtime,
        get_session_factory(),
        text_scope=body.text_scope,
        clean_table=body.clean_table,
        runtime=runtime,
    )
    return ok({"dispatched": "clean", "document_id": doc.id})


@router.post("/{document_id}/parse-variant")
async def switch_parse_variant(
    request: Request,
    document_id: str,
    body: ParseVariantRequest,
    session: AsyncSession = Depends(get_db),
):
    """切换切分链路使用的解析版本（raw ↔ cleaned），切换后自动重切。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    if body.variant not in ("raw", "cleaned"):
        return fail(CODE_PARAM_ERROR, "variant 须为 raw 或 cleaned")
    if doc.status not in _CLEANABLE:
        return fail(
            CODE_PARAM_ERROR, f"当前状态 {doc.status} 不可切换版本"
        )
    runtime = _runtime(request)
    if body.variant == "cleaned" and not runtime.cache.parse_cleaned_path(
        doc.file_hash
    ).is_file():
        return fail(CODE_NOT_FOUND, "清洗版缓存不存在，请先执行 LLM 清洗")
    doc.parse_variant = body.variant
    await session.commit()
    tasks.dispatch_background(
        tasks.run_chunk, doc.id, runtime, get_session_factory(), runtime=runtime
    )
    return ok({"variant": body.variant, "dispatched": "chunk"})
