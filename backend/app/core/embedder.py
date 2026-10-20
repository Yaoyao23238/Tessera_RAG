"""Embedder 抽象与实现。

- LocalEmbedder：BGE-M3（FlagEmbedding，稠密+稀疏权重）/
  Qwen3-Embedding 0.6B/4B（sentence-transformers）；懒加载单例，
  模型首次使用经 ModelManager 按需下载
- APIEmbedder：OpenAI 兼容 /embeddings（仅稠密；稀疏路走 FTS）

约束：不同 embedding 模型向量空间不同，不可混用；切换模型必须全量重建。
重依赖（FlagEmbedding / torch / httpx）全部懒导入。
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import Any

from app.core.types import EmbeddingResult
from app.services.model_manager import MODEL_REGISTRY, ModelManager

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 32


def cuda_fp16_available() -> bool:
    """检测是否可用 CUDA fp16 推理。

    有 GPU 时 fp16 显存占用减半（BGE-M3 约 2.2GB → 1.1GB）且有 Tensor Core
    加速；CPU 或 torch 未安装时回退 fp32。reranker 复用本函数。
    """
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


class EmbedderError(Exception):
    """向量化失败。"""


class Embedder(ABC):
    """Embedder 抽象基类。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> EmbeddingResult:
        """批量编码文本，返回稠密向量（+ 可选稀疏权重）。"""

    @abstractmethod
    def dimensions(self) -> int:
        """向量维度。"""

    @abstractmethod
    def model_name(self) -> str:
        """模型标识（embedding 缓存 key 组成部分）。"""

    def is_loaded(self) -> bool:
        """模型权重是否已加载（APIEmbedder 无本地权重，默认视为就绪）。"""
        return True

    def ensure_loaded(self) -> None:
        """显式加载模型权重（预热用；APIEmbedder 无需加载）。"""


class LocalEmbedder(Embedder):
    """本地模型 Embedder（懒加载单例语义由工厂保证）。"""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        model_manager: ModelManager | None = None,
    ) -> None:
        self._model_name = model_name
        self._model_manager = model_manager
        self._model: Any = None
        # 加载互斥：预热线程与首次检索并发触发时只加载一次（权重 ~2GB）
        self._load_lock = threading.Lock()
        info = MODEL_REGISTRY.get(model_name)
        self._dimensions = info.dimensions if info else 1024
        self._loader = info.loader if info else "sentence_transformers"

    def ensure_model(self, progress_cb=None) -> None:
        """确保模型已下载（经 ModelManager）。"""
        if self._model_manager is not None:
            self._model_manager.ensure_model(self._model_name, progress_cb)

    def is_loaded(self) -> bool:
        return self._model is not None

    def ensure_loaded(self) -> None:
        self._load()

    def _load(self) -> None:
        """首次使用时加载模型到内存（不拖慢启动）。"""
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            self._load_unlocked()

    def _load_unlocked(self) -> None:
        # 确保权重就绪（未配置 ModelManager 时假定已就绪/走默认缓存）
        if self._model_manager is not None:
            model_path = str(self._model_manager.ensure_model(self._model_name))
        else:
            model_path = self._model_name

        if self._loader == "flagembedding":
            try:
                from FlagEmbedding import BGEM3FlagModel
            except ImportError as exc:
                raise EmbedderError(
                    "FlagEmbedding 未安装（pip install FlagEmbedding）"
                ) from exc
            logger.info("加载 BGE-M3：%s", model_path)
            self._model = BGEM3FlagModel(model_path, use_fp16=cuda_fp16_available())
        else:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbedderError(
                    "sentence-transformers 未安装"
                ) from exc
            logger.info("加载 sentence-transformers 模型：%s", model_path)
            self._model = SentenceTransformer(model_path)

    def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult()
        self._load()
        try:
            if self._loader == "flagembedding":
                output = self._model.encode(
                    texts,
                    batch_size=_DEFAULT_BATCH_SIZE,
                    return_dense=True,
                    return_sparse=True,
                    return_colbert_vecs=False,
                )
                dense = [list(map(float, vec)) for vec in output["dense_vecs"]]
                sparse = [
                    {int(k): float(v) for k, v in weights.items()}
                    for weights in output["lexical_weights"]
                ]
                return EmbeddingResult(dense=dense, sparse=sparse)
            vectors = self._model.encode(
                texts,
                batch_size=_DEFAULT_BATCH_SIZE,
                normalize_embeddings=True,
            )
            dense = [list(map(float, vec)) for vec in vectors]
            return EmbeddingResult(dense=dense, sparse=None)
        except EmbedderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbedderError(f"向量化失败：{exc}") from exc

    def dimensions(self) -> int:
        return self._dimensions

    def model_name(self) -> str:
        return self._model_name


class APIEmbedder(Embedder):
    """OpenAI 兼容 /embeddings 客户端（仅稠密向量）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        dimensions: int = 1024,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._model_name = model_name
        self._dimensions = dimensions
        self.timeout = timeout

    def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult()
        try:
            import httpx
        except ImportError as exc:
            raise EmbedderError("httpx 未安装") from exc

        dense: list[list[float]] = []
        # 分批调用，避免单次请求过大
        for start in range(0, len(texts), _DEFAULT_BATCH_SIZE):
            batch = texts[start : start + _DEFAULT_BATCH_SIZE]
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        f"{self.base_url}/embeddings",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={"model": self._model_name, "input": batch},
                    )
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as exc:  # noqa: BLE001
                raise EmbedderError(f"Embedding API 调用失败：{exc}") from exc
            try:
                items = sorted(data["data"], key=lambda item: item["index"])
                dense.extend([list(map(float, item["embedding"])) for item in items])
            except (KeyError, TypeError) as exc:
                raise EmbedderError(f"Embedding API 响应格式异常：{data}") from exc
        return EmbeddingResult(dense=dense, sparse=None)

    def dimensions(self) -> int:
        return self._dimensions

    def model_name(self) -> str:
        return self._model_name


def create_embedder(
    config: dict[str, Any], model_manager: ModelManager | None = None
) -> Embedder:
    """按 EmbeddingConfig 创建 Embedder。

    config 字段见 schemas.EmbeddingConfigSchema：
    provider_type=local / openai_compatible。
    """
    provider_type = config.get("provider_type", "local")
    model_name = config.get("model_name", "BAAI/bge-m3")
    if provider_type == "openai_compatible":
        return APIEmbedder(
            base_url=config.get("base_url") or "",
            api_key=config.get("api_key") or "",
            model_name=model_name,
            dimensions=int(config.get("dimensions", 1024)),
        )
    return LocalEmbedder(model_name=model_name, model_manager=model_manager)
