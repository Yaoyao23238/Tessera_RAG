"""检索测试 API：稠密+稀疏 → RRF → Rerank（可选）→ 父块展开。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request

from app.api import CODE_VECTOR_STORE_FAILED, fail, ok
from app.core.reranker import Reranker
from app.core.retriever import Retriever
from app.models.schemas import SearchRequest

router = APIRouter(tags=["search"])


def make_parent_resolver(
    db_path: str | Path,
) -> Callable[[list[str]], dict[str, dict[str, Any]]]:
    """父块展开 resolver：同步 sqlite3 直读（retriever 为同步调用链）。

    命中子块 → parent_id → 父块全文 + 来源文件 + 页码 + 面包屑。
    """

    def resolver(chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not chunk_ids:
            return {}
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            placeholders = ",".join("?" for _ in chunk_ids)
            children = conn.execute(
                f"""SELECT c.*, d.filename AS source_file FROM chunks c
                    LEFT JOIN documents d ON d.id = c.document_id
                    WHERE c.id IN ({placeholders})""",
                chunk_ids,
            ).fetchall()
            parent_ids = [row["parent_id"] for row in children if row["parent_id"]]
            parents: dict[str, sqlite3.Row] = {}
            if parent_ids:
                pp = ",".join("?" for _ in parent_ids)
                for row in conn.execute(
                    f"SELECT * FROM chunks WHERE id IN ({pp})", parent_ids
                ).fetchall():
                    parents[row["id"]] = row
            resolved: dict[str, dict[str, Any]] = {}
            for row in children:
                parent = parents.get(row["parent_id"] or "")
                resolved[row["id"]] = {
                    "parent_content": parent["content"]
                    if parent is not None
                    else row["content"],
                    "matched_child_text": row["content"],
                    "source_file": row["source_file"] or "",
                    "page_number": row["page_number"],
                    "breadcrumb": json.loads(row["breadcrumb"] or "[]"),
                    "chunk_type": row["chunk_type"],
                }
            return resolved
        finally:
            conn.close()

    return resolver


@router.post("/search")
async def search(body: SearchRequest, request: Request):
    """混合检索测试入口。"""
    runtime = request.app.state.runtime
    if not body.query.strip():
        return fail(1001, "query 不能为空")
    filters: dict[str, list[str]] = {}
    if body.document_ids:
        filters["document_ids"] = body.document_ids
    if body.chunk_types:
        filters["chunk_types"] = body.chunk_types
    try:
        # Rerank 模型未下载时跳过精排：避免每次检索等待下载超时（数十秒），
        # 响应标注 rerank_skipped 供前端提示
        reranker = runtime.reranker
        rerank_skipped: str | None = None
        if reranker.enabled and not runtime.model_manager.is_downloaded(
            reranker.model_name
        ):
            rerank_skipped = f"Rerank 模型未下载，已跳过精排"
            reranker = Reranker(
                model_name=reranker.model_name,
                enabled=False,
                top_n=reranker.top_n,
                min_score=reranker.min_score,
                model_manager=runtime.model_manager,
            )
        retriever = Retriever(
            embedder=runtime.embedder,
            store=runtime.vector_store,
            reranker=reranker,
            parent_resolver=make_parent_resolver(runtime.app_settings.db_path),
        )
        # 检索是同步阻塞调用（embed + 向量检索 + 精排，冷启动含模型加载
        # 可达 40s+），必须移入线程池——直接在事件循环上跑会卡死整个后端
        # （WS / health 一并无响应）。与 tasks.py / settings.py 的既有约定
        # 一致；embedder/reranker 的加载已用 _load_lock 保证线程安全。
        response = await asyncio.to_thread(
            retriever.search,
            body.query,
            top_k=body.top_k,
            filters=filters or None,
            use_rerank=body.use_rerank,
        )
        used_rerank = body.use_rerank if body.use_rerank is not None else reranker.enabled
        if rerank_skipped:
            used_rerank = False
    except Exception as exc:  # noqa: BLE001
        return fail(CODE_VECTOR_STORE_FAILED, f"检索失败：{exc}")
    return ok(
        {
            "query": response.query,
            "used_rerank": used_rerank,
            "rerank_skipped": rerank_skipped,
            "results": [
                {
                    "chunk_id": r.chunk_id,
                    "parent_content": r.parent_content,
                    "matched_child_text": r.matched_child_text,
                    "source_file": r.source_file,
                    "page_number": r.page_number,
                    "breadcrumb": r.breadcrumb,
                    "chunk_type": r.chunk_type,
                    "score": r.score,
                    "dense_rank": r.dense_rank,
                    "sparse_rank": r.sparse_rank,
                    # 各通道原始分（稠密相似度 / FTS BM25）：RRF 分是排名倒数和、
                    # 无绝对语义，没有原始分就没法诊断结果来自哪个通道、分高分低
                    "dense_score": r.dense_score,
                    "sparse_score": r.sparse_score,
                    "rerank_score": r.rerank_score,
                }
                for r in response.results
            ],
            "total": response.total,
            "latency_ms": response.latency_ms,
            # 被 min_score 阈值滤掉的候选数：>0 且 results 为空时前端显示
            # 「无足够相关的结果」而非「无召回结果」，两者语义不同（见 types.py）
            "threshold_dropped": response.threshold_dropped,
        }
    )
