"""API 路由层：只做 HTTP 参数校验与响应格式化，业务调用 core/services。

统一响应 {code, data, message}，code=0 成功。错误码分段：
- 1xxx 通用（1001 参数错误、1002 资源不存在）
- 2xxx 文档链路（2001 格式不支持、2002 加密 PDF、2003 解析失败、
  2004 切分失败、2005 向量化失败、2006 写库失败）
- 3xxx 模型（3001 LLM 连接失败、3002 模型下载失败）
- 4xxx 存储（4001 向量库连接失败、4002 已自动降级 sqlite-vec）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

# 错误码常量（共享知识 #1）
CODE_PARAM_ERROR = 1001
CODE_NOT_FOUND = 1002
CODE_UNSUPPORTED_FORMAT = 2001
CODE_ENCRYPTED_PDF = 2002
CODE_PARSE_FAILED = 2003
CODE_CHUNK_FAILED = 2004
CODE_EMBED_FAILED = 2005
CODE_STORE_FAILED = 2006
CODE_LLM_CONNECTION_FAILED = 3001
CODE_MODEL_DOWNLOAD_FAILED = 3002
CODE_VECTOR_STORE_FAILED = 4001
CODE_STORE_DEGRADED = 4002
# 切换 embedding 模型需前端确认重建向量（带 affected 数量）
CODE_EMBED_REBUILD_CONFIRM = 4100


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """成功响应。"""
    return {"code": 0, "data": data, "message": message}


def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """业务错误响应（HTTP 200，错误码在 envelope 中）。"""
    return {"code": code, "data": data, "message": message}


api_router = APIRouter(prefix="/api")

from app.api import (  # noqa: E402  # 放在 helper 定义之后，避免循环导入
    chunks,
    documents,
    evaluation,
    export,
    parse_preview,
    search,
    settings as settings_api,
    stats,
)

api_router.include_router(documents.router)
api_router.include_router(chunks.router)
api_router.include_router(parse_preview.router)
api_router.include_router(search.router)
api_router.include_router(evaluation.router)
api_router.include_router(export.router)
api_router.include_router(stats.router)
api_router.include_router(settings_api.router)
