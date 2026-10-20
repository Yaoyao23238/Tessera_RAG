"""设置 API：全配置 GET/PUT（热更新）+ LLM CRUD + 连通性测试 +
本地模型清单/下载 + 缓存管理。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
import sqlalchemy as sa
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import (
    CODE_EMBED_REBUILD_CONFIRM,
    CODE_LLM_CONNECTION_FAILED,
    CODE_MODEL_DOWNLOAD_FAILED,
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    fail,
    ok,
)
from app.api.ws import push_model_download
from app.core.embedder import create_embedder
from app.core.reranker import Reranker
from app.core.types import ScoredChunk
from app.core.vector_store import MAIN_TABLE
from app.database import get_db
from app.models.db import Chunk, DocStatus, Document
from app.models.schemas import (
    EmbeddingConfigSchema,
    LLMProviderSchema,
    RerankerConfigSchema,
)
from app.services.llm import create_llm_client
from app.services.model_manager import (
    DOCLING_BUNDLE_ID,
    MODEL_REGISTRY,
    ModelDownloadError,
)
from app.services.runtime import RuntimeContext
from app.services.settings_store import SETTING_KEYS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


def _runtime(request: Request) -> RuntimeContext:
    return request.app.state.runtime


# ---------------------------------------------------------------------------
# 全量配置
# ---------------------------------------------------------------------------


@router.get("")
async def get_settings(request: Request):
    """获取所有配置。"""
    return ok(_runtime(request).all_settings())


async def _count_embedded_docs(session: AsyncSession) -> int:
    """统计已入库（有向量）的文档数。"""
    result = await session.execute(
        sa.select(sa.func.count(Document.id)).where(
            or_(
                Document.status == DocStatus.INDEXED.value,
                Document.embedded_count > 0,
            )
        )
    )
    return int(result.scalar() or 0)


async def _reset_vector_on_embedding_change(
    runtime: RuntimeContext, session: AsyncSession
) -> None:
    """切换 Embedding 模型后清空已入库向量并重置文档为 chunked，等待重新入库。

    只清主库（正式入库文档）；评测实验表（exp_*）由用户按需在实验页重建。
    """
    try:
        runtime.vector_store.drop_table(MAIN_TABLE)
    except Exception:  # noqa: BLE001
        logger.warning("清空向量库主表失败（可能本就不存在）", exc_info=True)
    await session.execute(
        sa.update(Document)
        .where(
            or_(
                Document.status == DocStatus.INDEXED.value,
                Document.embedded_count > 0,
            )
        )
        .values(status=DocStatus.CHUNKED.value, embedded_count=0)
    )
    await session.execute(
        sa.update(Chunk)
        .where(Chunk.is_embedded.is_(True))
        .values(is_embedded=False, embedding_model=None)
    )
    await session.commit()


@router.put("")
async def update_settings(
    body: dict[str, Any],
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """更新配置（按分组部分更新），写入后热更新对应单例。

    Embedding 模型切换会触发"强制重建向量"确认：若存在已入库文档且
    模型标识（provider_type/model_name）变化，未带 confirm 时返回
    CODE_EMBED_REBUILD_CONFIRM（data.affected=受影响文档数）；前端确认后
    带 ``_confirm_rebuild: true`` 重发，后端清空向量库并重置文档状态。
    """
    runtime = _runtime(request)
    updated: list[str] = []
    confirm_rebuild = bool(body.get("_confirm_rebuild"))
    for key, value in body.items():
        if key in ("_confirm_rebuild",):
            continue
        if key not in SETTING_KEYS:
            return fail(CODE_PARAM_ERROR, f"未知设置分组：{key}")
        if key == "embedding":
            old = runtime.get("embedding") or {}
            model_changed = (
                value.get("provider_type") != old.get("provider_type")
                or value.get("model_name") != old.get("model_name")
            )
            if model_changed:
                affected = await _count_embedded_docs(session)
                if affected > 0 and not confirm_rebuild:
                    return fail(
                        CODE_EMBED_REBUILD_CONFIRM,
                        "切换 Embedding 模型需清空已入库向量并重新入库",
                        {
                            "affected": affected,
                            "need_rebuild": True,
                            "current_model": old.get("model_name"),
                        },
                    )
                if affected > 0 and confirm_rebuild:
                    await _reset_vector_on_embedding_change(runtime, session)
        await runtime.update(session, key, value)
        updated.append(key)
    return ok({"updated": updated, "settings": runtime.all_settings()})


# ---------------------------------------------------------------------------
# LLM provider CRUD + 连通性测试
# ---------------------------------------------------------------------------


@router.get("/llm")
async def list_llm_providers(request: Request):
    return ok(_runtime(request).get("llm_providers"))


@router.post("/llm")
async def add_llm_provider(
    body: LLMProviderSchema, request: Request, session: AsyncSession = Depends(get_db)
):
    runtime = _runtime(request)
    providers = list(runtime.get("llm_providers"))
    provider = body.model_dump()
    provider["id"] = provider["id"] or uuid.uuid4().hex
    if provider.get("is_default"):
        for p in providers:
            p["is_default"] = False
    providers.append(provider)
    await runtime.update(session, "llm_providers", providers)
    return ok(provider)


@router.put("/llm/{provider_id}")
async def update_llm_provider(
    provider_id: str,
    body: LLMProviderSchema,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    runtime = _runtime(request)
    providers = list(runtime.get("llm_providers"))
    for i, p in enumerate(providers):
        if p.get("id") == provider_id:
            updated = body.model_dump()
            updated["id"] = provider_id
            if updated.get("is_default"):
                for other in providers:
                    other["is_default"] = False
            providers[i] = updated
            await runtime.update(session, "llm_providers", providers)
            return ok(updated)
    return fail(CODE_NOT_FOUND, "provider 不存在")


@router.delete("/llm/{provider_id}")
async def delete_llm_provider(
    provider_id: str, request: Request, session: AsyncSession = Depends(get_db)
):
    runtime = _runtime(request)
    providers = [
        p for p in runtime.get("llm_providers") if p.get("id") != provider_id
    ]
    await runtime.update(session, "llm_providers", providers)
    return ok({"deleted": provider_id})


@router.post("/llm/test")
async def test_llm(body: LLMProviderSchema):
    """LLM 连通性测试（会消耗少量 token）。"""
    client = create_llm_client(body.model_dump())
    connected = await asyncio.to_thread(client.test_connection)
    if connected:
        return ok({"connected": True})
    return fail(CODE_LLM_CONNECTION_FAILED, "LLM 连接失败，请检查 base_url / api_key")


@router.post("/embedding/test")
async def test_embedding(body: EmbeddingConfigSchema, request: Request):
    """Embedding 连通性/就绪测试：本地模型查下载状态，API 模型发真实请求。"""
    runtime = _runtime(request)
    config = body.model_dump()
    if config.get("provider_type") == "local":
        downloaded = runtime.model_manager.is_downloaded(config["model_name"])
        return ok(
            {
                "ready": downloaded,
                "model_name": config["model_name"],
                "reason": None if downloaded else "模型未下载",
            }
        )
    try:
        embedder = create_embedder(config, runtime.model_manager)
        await asyncio.to_thread(embedder.embed, ["连接测试"])
        return ok({"ready": True})
    except Exception as exc:  # noqa: BLE001
        return fail(CODE_LLM_CONNECTION_FAILED, f"Embedding 连接失败：{exc}")


@router.post("/reranker/test")
async def test_reranker(body: RerankerConfigSchema, request: Request):
    """Reranker 加载测试（模型未下载时返回 ready=false）。"""
    runtime = _runtime(request)
    if not runtime.model_manager.is_downloaded(body.model_name):
        return ok({"ready": False, "reason": "模型未下载"})
    reranker = Reranker(
        model_name=body.model_name,
        enabled=True,
        top_n=body.top_n,
        model_manager=runtime.model_manager,
    )
    try:
        await asyncio.to_thread(
            reranker.rerank, "测试", [ScoredChunk(chunk_id="t", content="测试内容")]
        )
        return ok({"ready": True})
    except Exception as exc:  # noqa: BLE001
        return fail(CODE_LLM_CONNECTION_FAILED, f"Reranker 加载失败：{exc}")


# ---------------------------------------------------------------------------
# 本地模型清单与下载
# ---------------------------------------------------------------------------


@router.get("/models")
async def list_models(request: Request):
    """本地模型清单与下载状态（含 Docling 模型包）。"""
    runtime = _runtime(request)
    statuses = runtime.model_manager.list_models()
    mm = runtime.model_manager
    items = [
        {
            "model_id": s.model_id,
            "kind": s.kind,
            "size_mb": s.size_mb,
            "downloaded": s.downloaded,
            "downloading": s.downloading,
            "local_path": s.local_path,
            "description": MODEL_REGISTRY.get(s.model_id).description
            if s.model_id in MODEL_REGISTRY
            else "",
        }
        for s in statuses
    ]
    # Docling 模型包（kind=docling）
    items.append(
        {
            "model_id": DOCLING_BUNDLE_ID,
            "kind": "docling",
            "size_mb": 6000.0,
            "downloaded": mm.is_docling_downloaded(),
            "downloading": mm.is_downloading(DOCLING_BUNDLE_ID),
            "local_path": str(mm.docling_dir()) if mm.is_docling_downloaded() else None,
            "description": "Docling 解析模型包（layout/表格/OCR，PDF 解析必需）",
        }
    )
    return ok({"items": items, "download_source": mm.download_source})


@router.post("/models/download")
async def download_model(
    body: dict,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """触发模型下载（后台线程，进度经 WS model_download 推送）。

    body 可选带 source：同时更新全局下载源后按新源下载。
    """
    model_id = body.get("model_id", "")
    if model_id not in MODEL_REGISTRY:
        return fail(CODE_PARAM_ERROR, f"未知模型：{model_id}")
    runtime = _runtime(request)
    # 更新下载源（若传入且合法）
    source = body.get("source")
    if source:
        current = runtime.get("download") or {}
        if source in ("huggingface", "hf_mirror", "modelscope"):
            await runtime.update(session, "download", {**current, "source": source})
    if runtime.model_manager.is_downloaded(model_id):
        return ok({"started": False, "reason": "模型已下载"})

    def _download() -> None:
        try:
            runtime.model_manager.ensure_model(
                model_id,
                progress_cb=lambda d, t: push_model_download(model_id, d, t),
            )
        except ModelDownloadError:
            logger.exception("模型下载失败：%s", model_id)
            push_model_download(model_id, -1, 0)  # downloaded_mb=-1 表示失败

    threading.Thread(target=_download, daemon=True).start()
    return ok({"started": True, "model_id": model_id})


@router.post("/models/download/docling")
async def download_docling_bundle(request: Request):
    """下载 Docling 模型包（后台线程；固定经 hf-mirror，进度推 WS）。"""
    runtime = _runtime(request)
    mm = runtime.model_manager
    if mm.is_docling_downloaded():
        return ok({"started": False, "reason": "Docling 模型包已下载"})
    if mm.is_downloading(DOCLING_BUNDLE_ID):
        return fail(CODE_PARAM_ERROR, "Docling 模型包正在下载中")

    def _download() -> None:
        try:
            mm.download_docling_bundle(
                progress_cb=lambda d, t: push_model_download(DOCLING_BUNDLE_ID, d, t)
            )
        except ModelDownloadError:
            logger.exception("Docling 模型包下载失败")
            push_model_download(DOCLING_BUNDLE_ID, -1, 0)

    threading.Thread(target=_download, daemon=True).start()
    return ok({"started": True, "model_id": DOCLING_BUNDLE_ID})


@router.post("/models/download/cancel")
async def cancel_model_download(body: dict, request: Request):
    """中断下载并清理残留目录（下载失败后重试前也可调用）。"""
    model_id = body.get("model_id", "")
    if not model_id:
        return fail(CODE_PARAM_ERROR, "缺少 model_id")
    cancelled = _runtime(request).model_manager.cancel_download(model_id)
    return ok({"cancelled": cancelled, "model_id": model_id})


# ---------------------------------------------------------------------------
# 缓存管理
# ---------------------------------------------------------------------------


@router.get("/cache")
async def cache_status(request: Request):
    """缓存状态（大小、各类型占用）。"""
    return ok(_runtime(request).cache.sizes())


@router.delete("/cache")
async def clear_cache(request: Request, kind: str = "all"):
    """清理缓存（parse / chunk / embed / all）。"""
    if kind not in ("parse", "chunk", "embed", "all"):
        return fail(CODE_PARAM_ERROR, "kind 须为 parse/chunk/embed/all")
    freed = _runtime(request).cache.clear(kind)
    return ok({"cleared": kind, "freed_bytes": freed})
