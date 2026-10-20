"""运行时上下文：全局单例容器 + 配置热更新。

持有 CacheManager / ModelManager / Embedder / VectorStore / Reranker /
LLMClient 单例；设置写入 SQLite 后调用 ``update`` 即失效对应单例，
下次访问时按新配置重建（共享知识 #10）。

Embedder/VectorStore/Reranker 均为懒加载单例：首次访问才创建，
不拖慢启动。测试可经 set_* 方法注入 fake。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.embedder import Embedder, create_embedder
from app.core.reranker import Reranker
from app.core.types import ChunkingConfig
from app.core.vector_store import VectorStore, create_vector_store
from app.services.cache import CacheManager
from app.services.llm import LLMClient, create_llm_client
from app.services.model_manager import ModelManager
from app.services.settings_store import (
    deep_merge,
    default_settings,
    load_settings,
    save_setting,
)

logger = logging.getLogger(__name__)


class RuntimeContext:
    """应用级运行时单例容器。"""

    #: 测试注入开关：False 时 api 层不实际分发后台任务
    dispatch_enabled: bool = True

    def __init__(self, app_settings: Settings) -> None:
        self.app_settings = app_settings
        app_settings.ensure_dirs()
        self.cache = CacheManager(
            app_settings.cache_dir, app_settings.cache_size_limit_gb
        )
        self.model_manager = ModelManager(app_settings.models_dir)
        self._settings: dict[str, Any] = default_settings()
        self._embedder: Embedder | None = None
        self._store: VectorStore | None = None
        self._reranker: Reranker | None = None
        self._llm_override: LLMClient | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 设置读写 + 热更新
    # ------------------------------------------------------------------

    async def load(self, session: AsyncSession) -> None:
        """启动时从 SQLite 加载设置。"""
        self._settings = await load_settings(session)

    def get(self, key: str) -> Any:
        return self._settings.get(key, default_settings().get(key))

    def all_settings(self) -> dict[str, Any]:
        settings = dict(self._settings)
        # data_dir 以运行时生效值为准（默认配置里为空串，直接展示会误导）
        app_cfg = dict(settings.get("app") or {})
        if not app_cfg.get("data_dir"):
            app_cfg["data_dir"] = str(self.app_settings.data_dir)
        settings["app"] = app_cfg
        return settings

    async def update(self, session: AsyncSession, key: str, value: Any) -> None:
        """写入设置并失效对应单例（配置热更新）。

        与 load_settings 口径一致：新值**深度合并**到该分组当前值上，
        既支持 PUT /settings 的「按分组部分更新」语义，也保证重启前后
        同一份持久化数据得到同一份生效配置（整体替换会让两者分叉）。
        """
        current = self._settings.get(key, default_settings().get(key))
        merged = deep_merge(current, value)
        await save_setting(session, key, merged)
        self._settings[key] = merged
        self._on_settings_changed(key)

    def _on_settings_changed(self, key: str) -> None:
        with self._lock:
            if key == "embedding":
                self._embedder = None  # 切换模型后下次访问重建
            elif key == "vector_store":
                self._store = None
            elif key == "reranker":
                self._reranker = None
            elif key == "app":
                app_cfg = self.get("app")
                self.cache.limit_bytes = int(
                    float(app_cfg.get("cache_size_limit_gb", 2.0)) * (1024**3)
                )
            elif key == "download":
                self.apply_download_source()

    def apply_download_source(self) -> None:
        """按当前下载源设置同步 ModelManager 与 HF_ENDPOINT 环境变量。"""
        source = (self.get("download") or {}).get("source", "huggingface")
        self.model_manager.set_download_source(source)

    # ------------------------------------------------------------------
    # 单例访问（懒构建）
    # ------------------------------------------------------------------

    @property
    def embedder(self) -> Embedder:
        with self._lock:
            if self._embedder is None:
                self._embedder = create_embedder(
                    self.get("embedding"), self.model_manager
                )
            return self._embedder

    @property
    def vector_store(self) -> VectorStore:
        with self._lock:
            if self._store is None:
                self._store = create_vector_store(
                    self.get("vector_store"), self.app_settings.vector_dir
                )
                if self._store.degraded_from:
                    logger.warning(
                        "向量库 %s 不可用，已自动降级 sqlite-vec",
                        self._store.degraded_from,
                    )
            return self._store

    @property
    def reranker(self) -> Reranker:
        with self._lock:
            if self._reranker is None:
                cfg = self.get("reranker")
                # 显式 0.0（用户选"不过滤"）必须保留，故不用 `or` 兜底；
                # 缺键回落默认值时引用 DEFAULT_SETTINGS 而非硬编码，改默认一处生效
                min_score = cfg.get("min_score")
                if min_score is None:
                    min_score = default_settings()["reranker"]["min_score"]
                self._reranker = Reranker(
                    model_name=cfg.get("model_name", "BAAI/bge-reranker-v2-m3"),
                    enabled=bool(cfg.get("enabled", False)),
                    top_n=int(cfg.get("top_n", 20)),
                    min_score=float(min_score),
                    model_manager=self.model_manager,
                )
            return self._reranker

    def model_ready_status(self) -> dict:
        """模型就绪状态（不触发实例构建/权重加载，供 /api/health 展示）。"""
        embedder = self._embedder
        reranker = self._reranker
        return {
            "embedder_ready": bool(embedder is not None and embedder.is_loaded()),
            "reranker_ready": bool(reranker is not None and reranker.is_loaded()),
        }

    def llm_client(self) -> LLMClient | None:
        """默认 LLM provider 的客户端；未配置返回 None。"""
        if self._llm_override is not None:
            return self._llm_override
        providers = self.get("llm_providers") or []
        default = next(
            (p for p in providers if p.get("is_default") and p.get("enabled")),
            None,
        ) or next((p for p in providers if p.get("enabled")), None)
        if default is None:
            return None
        return create_llm_client(default)

    def chunking_config(self) -> ChunkingConfig:
        return ChunkingConfig.from_dict(self.get("chunking"))

    # ------------------------------------------------------------------
    # 测试 / 热更新注入
    # ------------------------------------------------------------------

    def set_embedder(self, embedder: Embedder | None) -> None:
        with self._lock:
            self._embedder = embedder

    def set_vector_store(self, store: VectorStore | None) -> None:
        with self._lock:
            self._store = store

    def set_reranker(self, reranker: Reranker | None) -> None:
        with self._lock:
            self._reranker = reranker

    def set_llm_client(self, client: LLMClient | None) -> None:
        self._llm_override = client
