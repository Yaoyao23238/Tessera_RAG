"""FastAPI 应用入口。

lifespan 负责：目录初始化、建表、日志双写（控制台 + {data_dir}/logs/）、
运行时单例容器初始化、向量库降级检测（LanceDB 失败 → sqlite-vec）、
中断任务恢复扫描（pending/running → failed）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logger = logging.getLogger("tessera")


def setup_logging() -> None:
    """日志双写：控制台 + 轮转文件（data_dir/logs/tessera.log）。"""
    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        root.addHandler(console)
    log_file = settings.log_dir / "tessera.log"
    if not any(
        isinstance(h, RotatingFileHandler)
        and getattr(h, "baseFilename", "") == str(log_file)
        for h in root.handlers
    ):
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)


async def _warmup_models(runtime) -> None:
    """启动后后台预热模型权重（to_thread 避免阻塞事件循环）。

    目的：首次检索不再等待 40s+ 的冷加载（面试演示/重启后首查的卡顿源）。
    只预热**已下载**的模型——未下载时跳过（启动期悄悄触发 GB 级下载会
    拖死测试与低配环境，下载仍留给设置页/首查按需触发）。
    预热失败只记日志不影响启动——首次检索仍走懒加载兜底。
    """
    try:
        embedder = runtime.embedder
        if runtime.model_manager.is_downloaded(embedder.model_name()):
            await asyncio.to_thread(embedder.ensure_loaded)
            logger.info("Embedding 模型预热完成")
        else:
            logger.info("Embedding 模型未下载，跳过预热（首查时按需下载）")
    except Exception:  # noqa: BLE001
        logger.exception("Embedding 模型预热失败（不影响启动，首次检索时懒加载）")
    try:
        reranker = runtime.reranker
        # reranker 语义：未下载不触发下载（_load 内秒抛），此处显式跳过
        if reranker.enabled and runtime.model_manager.is_downloaded(
            reranker.model_name
        ):
            await asyncio.to_thread(reranker.ensure_loaded)
            logger.info("Reranker 模型预热完成")
    except Exception:  # noqa: BLE001
        logger.exception("Reranker 模型预热失败（不影响启动）")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动 / 关闭钩子。"""
    setup_logging()
    settings = get_settings()
    settings.ensure_dirs()
    logger.info("Tessera 启动，数据目录：%s", settings.data_dir)

    from app.database import dispose_db, get_session_factory, init_db
    from app.services.runtime import RuntimeContext
    from app.workers.pool import shutdown_pool
    from app.workers.tasks import recover_interrupted_tasks

    await init_db()

    # 运行时单例容器（Embedder/VectorStore/Reranker 懒加载）
    runtime = RuntimeContext(settings)
    factory = get_session_factory()
    async with factory() as session:
        await runtime.load(session)
    app.state.runtime = runtime

    # Docling 模型包目录：设为 artifacts 路径后解析 PDF 不再联网下载模型。
    # 需在 docling 首次 import 前设置（docling 懒加载于后台任务线程，
    # 此处设置必然早于其首次 import；进程池子进程经 spawn 继承环境变量）。
    os.environ["DOCLING_ARTIFACTS_PATH"] = str(runtime.model_manager.docling_dir())
    # 禁用 Xet 高速下载（huggingface_hub 1.27+ 对大文件默认走 xet，
    # 但 hf-mirror 镜像不代理 xet 认证端点 cas-server.xethub.hf.co，会 401
    # 失败；禁用后回退普通 HTTP 下载，hf-mirror 完整支持）。
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    # 关闭 docling 模型 torch.compile：Windows 无 triton（仅 Linux），
    # layout(rt_detr_v2)/tableformer 编译必然失败，置 0 走 eager 推理。
    os.environ["DOCLING_INFERENCE_COMPILE_TORCH_MODELS"] = "0"
    # 应用持久化的下载源（hf_mirror 时设置 HF_ENDPOINT 镜像）
    runtime.apply_download_source()

    # 向量库降级检测（LanceDB 初始化失败自动降级 sqlite-vec 并提示）
    try:
        store = runtime.vector_store
        if getattr(store, "degraded_from", None):
            logger.warning(
                "向量库 %s 初始化失败，已自动降级为 sqlite-vec（错误码 4002）",
                store.degraded_from,
            )
    except Exception:  # noqa: BLE001 - 启动期兜底，不阻断服务
        logger.exception("向量库初始化异常，检索功能暂不可用")

    # 崩溃恢复：中断任务标记 failed，允许重试，已完成结果不丢
    await recover_interrupted_tasks(factory)

    # 模型预热：后台任务加载权重（embedder + 已下载的 reranker），
    # 必须在上述环境变量设置之后；持引用防 GC（asyncio 任务无强引用会被回收）
    app.state.warmup_task = asyncio.create_task(_warmup_models(runtime))

    yield

    # 关停：取消未完成的预热任务（不 cancel 的话测试/热重载会留悬挂线程）
    warmup_task = getattr(app.state, "warmup_task", None)
    if warmup_task is not None and not warmup_task.done():
        warmup_task.cancel()

    store = getattr(runtime, "_store", None)
    if store is not None and hasattr(store, "close"):
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass
    shutdown_pool(wait=False)
    await dispose_db()
    logger.info("Tessera 已关闭")


def create_app() -> FastAPI:
    """应用工厂。"""
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
    )
    # CORS 白名单：仅本机前端（5173 默认 / 5174 为端口占用时的 vite 备选）。
    # 不用 "*"：与 allow_credentials 组合在浏览器端本就被拒，
    # 且 0.0.0.0 / Docker 部署时放开跨域是真实漏洞面。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict:
        # models 字段只读已构建实例的加载状态（不触发构建/加载，零副作用），
        # 供前端/演示判断「预热中 → 就绪」
        runtime = getattr(app.state, "runtime", None)
        models = (
            runtime.model_ready_status()
            if runtime is not None
            else {"embedder_ready": False, "reranker_ready": False}
        )
        return {"code": 0, "data": {"status": "ok", "models": models}, "message": "ok"}

    from app.api import api_router
    from app.api.ws import ws_router

    app.include_router(api_router)
    app.include_router(ws_router)
    return app


app = create_app()


if __name__ == "__main__":
    # Windows spawn 保护：ProcessPoolExecutor 要求入口有此守卫
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
