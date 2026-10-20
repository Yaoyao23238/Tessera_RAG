"""文档管理 API：上传/列表/详情/删除级联/重解析/解锁/批量/URL 导入。"""

from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import CODE_NOT_FOUND, CODE_PARAM_ERROR, CODE_UNSUPPORTED_FORMAT, fail, ok
from app.core.parser import SUPPORTED_EXTENSIONS
from app.database import get_db, get_session_factory
from app.models.db import (
    Chunk,
    ChunkStatsSnapshot,
    Document,
    DocStatus,
    Task,
)
from app.models.schemas import DocumentOut
from app.services.cache import _safe_unlink
from app.services.file_storage import (
    FileTooLargeError,
    InsufficientDiskSpaceError,
    delete_file,
    ensure_disk_space,
    sanitize_filename,
    save_upload_stream,
    text_hash,
)
from app.services.runtime import RuntimeContext
from app.workers import tasks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

# URL 导入：浏览器 UA，规避部分站点默认 UA 拦截
_URL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_URL_TIMEOUT = 30  # 抓取超时（秒）
_URL_MAX_ESTIMATE = 20 * 1024 * 1024  # 磁盘空间预估上限（无 Content-Length 时按 20MB 估）
# Content-Type → 文件扩展名（URL 导入时据此落盘）
_URL_CT_EXT = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/markdown": ".md",
    "text/plain": ".txt",
}


def _runtime(request: Request) -> RuntimeContext:
    return request.app.state.runtime


@router.post("/upload")
async def upload_documents(
    request: Request,
    files: list[UploadFile] = File(...),
    overwrite: bool = Query(False),
    session: AsyncSession = Depends(get_db),
):
    """上传文件（multipart，多文件）。

    同 hash 已存在且未指定 overwrite 时返回 status=exists 提示，
    由前端弹「该文件已存在，是否重新上传？」。
    """
    runtime = _runtime(request)
    results = []
    for file in files:
        filename = sanitize_filename(file.filename or "unnamed")
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in SUPPORTED_EXTENSIONS:
            results.append(
                {
                    "filename": filename,
                    "status": "rejected",
                    "reason": f"不支持的格式 {ext}，支持：{sorted(SUPPORTED_EXTENSIONS)}",
                    "code": CODE_UNSUPPORTED_FORMAT,
                }
            )
            continue
        max_bytes = runtime.app_settings.max_file_size_mb * 1024 * 1024
        # 磁盘空间预估：Content-Length 可用按实际值，否则按上限保守估计
        estimated = file.size if getattr(file, "size", None) else max_bytes
        try:
            ensure_disk_space(runtime.app_settings.upload_dir, estimated)
        except InsufficientDiskSpaceError as exc:
            return fail(CODE_PARAM_ERROR, str(exc))

        # 流式落盘：1MB 分块读，边算 SHA256 边写，不占大内存
        try:
            path, file_hash, size = await save_upload_stream(
                file, filename, runtime.app_settings.upload_dir, max_bytes
            )
        except FileTooLargeError:
            results.append(
                {
                    "filename": filename,
                    "status": "rejected",
                    "reason": f"超过大小上限 {runtime.app_settings.max_file_size_mb}MB",
                    "code": CODE_PARAM_ERROR,
                }
            )
            continue

        existing = (
            (
                await session.execute(
                    select(Document).where(Document.file_hash == file_hash)
                )
            )
            .scalars()
            .first()
        )
        if existing is not None and not overwrite:
            # 重复文件：刚落盘的副本删除，避免占用磁盘
            delete_file(path)
            results.append(
                {
                    "filename": filename,
                    "status": "exists",
                    "document_id": existing.id,
                    "reason": "该文件已存在，是否重新上传？",
                }
            )
            continue
        if existing is not None and overwrite:
            await _delete_document(existing, runtime, session)

        doc = Document(
            filename=filename,
            file_path=str(path),
            file_hash=file_hash,
            file_type=ext.lstrip("."),
            file_size=size,
            title=path.stem,
            status=DocStatus.UPLOADED.value,
        )
        session.add(doc)
        await session.commit()
        # 上传仅登记（status=uploaded），解析由用户点击「开始解析」触发
        results.append(
            {
                "filename": filename,
                "status": "created",
                "document": DocumentOut.model_validate(doc).model_dump(mode="json"),
            }
        )
    return ok({"results": results})


class _ByteStreamReader:
    """把 async 字节迭代器适配为 save_upload_stream 需要的 read(size) 接口。"""

    def __init__(self, aiter: Any) -> None:
        self._it = aiter.__aiter__()
        self._buf = b""

    async def read(self, size: int) -> bytes:
        while len(self._buf) < size:
            try:
                self._buf += await self._it.__anext__()
            except StopAsyncIteration:
                break
        out, self._buf = self._buf[:size], self._buf[size:]
        return out


def _url_filename(url: str, content_type: str) -> str:
    """由 URL 与 Content-Type 生成落盘文件名（如 example.com_report.pdf）。"""
    parsed = urlparse(url)
    host = sanitize_filename(parsed.netloc.replace(":", "_")) or "site"
    seg = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    base = Path(seg).stem if seg and "." in seg else (seg or "index")
    stem = sanitize_filename(base)
    if not stem or stem in ("index", "index.html"):
        stem = "page"
    ext = _URL_CT_EXT.get(content_type)
    if not ext:
        path_ext = Path(parsed.path).suffix.lower()
        ext = path_ext if path_ext in SUPPORTED_EXTENSIONS else ".html"
    return f"{host}_{stem}{ext}"


@router.post("/import-url")
async def import_url(
    request: Request,
    body: dict,
    overwrite: bool = Query(False),
    session: AsyncSession = Depends(get_db),
):
    """URL 导入：抓取网页/文件 → 流式落盘 → 登记文档（解析由用户触发）。

    抓取带超时与大小上限；按 Content-Type 落扩展名，复用现有解析链路。
    同 hash 已存在且未指定 overwrite 时返回 status=exists。
    """
    import httpx

    url = str(body.get("url", "")).strip()
    if not url:
        return fail(CODE_PARAM_ERROR, "缺少 url")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return fail(CODE_PARAM_ERROR, "url 须为以 http:// 或 https:// 开头的完整地址")

    runtime = _runtime(request)
    max_bytes = runtime.app_settings.max_file_size_mb * 1024 * 1024
    try:
        ensure_disk_space(runtime.app_settings.upload_dir, min(max_bytes, _URL_MAX_ESTIMATE))
    except InsufficientDiskSpaceError as exc:
        return fail(CODE_PARAM_ERROR, str(exc))

    content_type = ""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_URL_TIMEOUT) as client:
            async with client.stream(
                "GET", url, headers={"User-Agent": _URL_UA}
            ) as resp:
                resp.raise_for_status()
                content_type = (
                    resp.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                filename = _url_filename(url, content_type)
                path, file_hash, size = await save_upload_stream(
                    _ByteStreamReader(resp.aiter_bytes()),
                    filename,
                    runtime.app_settings.upload_dir,
                    max_bytes,
                )
    except FileTooLargeError:
        return fail(CODE_PARAM_ERROR, "页面/文件超过大小上限")
    except Exception as exc:  # noqa: BLE001 - httpx/网络/解析异常统一归为抓取失败
        logger.warning("URL 抓取失败：%s (%s)", url, exc)
        return fail(CODE_PARAM_ERROR, f"抓取失败：{exc}")

    existing = (
        (await session.execute(select(Document).where(Document.file_hash == file_hash)))
        .scalars()
        .first()
    )
    if existing is not None and not overwrite:
        delete_file(path)
        return ok({"results": [{"filename": filename, "status": "exists", "document_id": existing.id, "reason": "该页面已导入，是否重新导入？"}]})
    if existing is not None and overwrite:
        await _delete_document(existing, runtime, session)

    doc = Document(
        filename=filename,
        file_path=str(path),
        file_hash=file_hash,
        file_type=Path(filename).suffix.lstrip("."),
        file_size=size,
        title=Path(filename).stem,
        status=DocStatus.UPLOADED.value,
    )
    session.add(doc)
    await session.commit()
    return ok(
        {
            "results": [
                {
                    "filename": filename,
                    "status": "created",
                    "document": DocumentOut.model_validate(doc).model_dump(mode="json"),
                }
            ]
        }
    )


@router.get("")
async def list_documents(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
):
    """文档列表（状态筛选 + 分页）。"""
    query = select(Document).order_by(Document.created_at.desc())
    count_query = select(func.count(Document.id))
    if status:
        query = query.where(Document.status == status)
        count_query = count_query.where(Document.status == status)
    total = (await session.execute(count_query)).scalar() or 0
    rows = (
        (await session.execute(query.offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )
    return ok(
        {
            "items": [
                DocumentOut.model_validate(row).model_dump(mode="json")
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/{document_id}")
async def get_document(document_id: str, session: AsyncSession = Depends(get_db)):
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    return ok(DocumentOut.model_validate(doc).model_dump(mode="json"))


async def _delete_document(
    doc: Document, runtime: RuntimeContext, session: AsyncSession
) -> None:
    """级联删除：chunks / 快照 / 任务 / 向量 / 切分+embedding 缓存 / 文件。

    解析缓存保留（同 hash 重新上传可复用）。
    """
    # 删 chunks 前收集内容哈希，用于清理 embedding 缓存（embed 缓存按
    # 内容哈希跨文档共享，同文本的其他文档缓存会一并失效，代价是重算）
    content_hashes = (
        (
            await session.execute(
                select(Chunk.content).where(Chunk.document_id == doc.id)
            )
        )
        .scalars()
        .all()
    )
    await session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    await session.execute(
        delete(ChunkStatsSnapshot).where(ChunkStatsSnapshot.document_id == doc.id)
    )
    await session.execute(delete(Task).where(Task.document_id == doc.id))
    try:
        runtime.vector_store.delete_by_document(doc.id)
    except Exception:  # noqa: BLE001
        logger.warning("删除文档向量失败", exc_info=True)
    runtime.cache.clear_document_caches(doc.file_hash)
    for content in set(content_hashes):
        runtime.cache.clear_embed(text_hash(content))
    delete_file(doc.file_path)
    await session.delete(doc)


@router.delete("/{document_id}")
async def delete_document(
    request: Request, document_id: str, session: AsyncSession = Depends(get_db)
):
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    await _delete_document(doc, _runtime(request), session)
    await session.commit()
    return ok({"deleted": document_id})


@router.post("/{document_id}/reparse")
async def reparse_document(
    request: Request, document_id: str, session: AsyncSession = Depends(get_db)
):
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    runtime = _runtime(request)
    # 重解析：清理原始+清洗版解析缓存（尽力而为，删不掉不阻断），强制走 Docling；
    # variant 重置 raw，避免旧清洗版污染新解析结果
    _safe_unlink(runtime.cache.parse_path(doc.file_hash))
    runtime.cache.clear_parse_cleaned(doc.file_hash)
    doc.parse_variant = "raw"
    await session.commit()
    tasks.dispatch_background(
        tasks.run_parse, doc.id, runtime, get_session_factory(), runtime=runtime
    )
    return ok({"dispatched": "parse", "document_id": doc.id})


@router.post("/{document_id}/unlock")
async def unlock_document(
    request: Request,
    document_id: str,
    body: dict,
    session: AsyncSession = Depends(get_db),
):
    """提交 PDF 密码重新解析（密码不落盘）。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        return fail(CODE_NOT_FOUND, "文档不存在")
    password = str(body.get("password", ""))
    if not password:
        return fail(CODE_PARAM_ERROR, "缺少 password")
    runtime = _runtime(request)
    tasks.dispatch_background(
        tasks.run_parse,
        doc.id,
        runtime,
        get_session_factory(),
        password=password,
        runtime=runtime,
    )
    return ok({"dispatched": "parse", "document_id": doc.id})


@router.post("/batch")
async def batch_operation(
    request: Request, body: dict, session: AsyncSession = Depends(get_db)
):
    """批量操作：{"action": "delete"|"reparse"|"index", "ids": [...]}"""
    action = body.get("action")
    ids = body.get("ids") or []
    if action not in ("delete", "reparse", "index") or not ids:
        return fail(CODE_PARAM_ERROR, "action 须为 delete/reparse/index 且 ids 非空")
    runtime = _runtime(request)
    done: list[str] = []
    for doc_id in ids:
        doc = await session.get(Document, doc_id)
        if doc is None:
            continue
        if action == "delete":
            await _delete_document(doc, runtime, session)
        elif action == "reparse":
            tasks.dispatch_background(
                tasks.run_parse, doc.id, runtime, get_session_factory(), runtime=runtime
            )
        elif action == "index":
            tasks.dispatch_background(
                tasks.run_embed, doc.id, runtime, get_session_factory(), runtime=runtime
            )
        done.append(doc_id)
    await session.commit()
    return ok({"action": action, "affected": done})
