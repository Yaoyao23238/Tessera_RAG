"""bge-reranker-v2-m3 本地精排。

- 仅对 RRF 融合后的 top_n（默认 20）候选重排
- min_score > 0 时按精排分截断（默认 0.1，设置页可调，0 = 不过滤）
- 模型首次使用经 ModelManager 按需下载（约 2GB，WS 推 model_download 进度）
- 加载/运行失败抛 RerankerError，由 Retriever 捕获并回退为无 rerank 结果
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.core.embedder import cuda_fp16_available
from app.core.types import ScoredChunk
from app.services.model_manager import ModelManager

logger = logging.getLogger(__name__)


class RerankerError(Exception):
    """Rerank 失败（调用方应回退无 rerank 链路）。"""


class Reranker:
    """本地 Reranker（懒加载单例语义由工厂/调用方保证）。"""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        enabled: bool = False,
        top_n: int = 20,
        min_score: float = 0.1,
        model_manager: ModelManager | None = None,
    ) -> None:
        self.model_name = model_name
        self.enabled = enabled
        self.top_n = top_n
        #: 精排最低分阈值（0 = 不过滤）。只对已精排的候选有意义；
        #: RRF 分是排名倒数和、无绝对语义，不能拿来设阈值。
        #: 默认 0.1 校准于 bge-reranker-v2-m3 的 sigmoid 双峰，换模型需重校准。
        self.min_score = min_score
        self._model_manager = model_manager
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        # 加载互斥：预热线程与首次检索并发触发时只加载一次
        self._load_lock = threading.Lock()

    def ensure_model(self, progress_cb=None) -> None:
        """确保模型已下载。"""
        if self._model_manager is not None:
            self._model_manager.ensure_model(self.model_name, progress_cb)

    def is_loaded(self) -> bool:
        return self._model is not None

    def ensure_loaded(self) -> None:
        self._load()

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            self._load_unlocked()

    def _load_unlocked(self) -> None:
        if self._model_manager is not None:
            # 模型未下载时直接报错（秒回），由 retriever 降级为无 rerank；
            # 不能在这里触发下载——检索路径上联网下载会挂住几十秒
            if not self._model_manager.is_downloaded(self.model_name):
                raise RerankerError(
                    f"Reranker 模型未下载：{self.model_name}（请到设置页下载）"
                )
            model_path = str(self._model_manager.local_path(self.model_name))
        else:
            model_path = self.model_name
        logger.info("加载 Reranker：%s", model_path)
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(model_path)
            self._torch = torch
            model = AutoModelForSequenceClassification.from_pretrained(model_path)
            if cuda_fp16_available() and torch.cuda.is_available():
                model = model.half().cuda()
            else:
                model = model.float()
            model.eval()
            self._model = model
        except Exception as exc:  # noqa: BLE001
            raise RerankerError(f"Reranker 加载失败：{exc}") from exc

    def rerank(
        self, query: str, candidates: list[ScoredChunk]
    ) -> list[ScoredChunk]:
        """对候选按 query 相关性重排，回填 rerank_score 并降序返回。"""
        if not candidates:
            return candidates
        self._load()
        pairs = [[query, c.content] for c in candidates]
        try:
            torch = self._torch
            all_scores: list[float] = []
            batch_size = 16
            with torch.no_grad():
                for i in range(0, len(pairs), batch_size):
                    batch = pairs[i : i + batch_size]
                    inputs = self._tokenizer(
                        batch,
                        padding=True,
                        truncation=True,
                        max_length=512,
                        return_tensors="pt",
                    )
                    if next(self._model.parameters()).is_cuda:
                        inputs = {
                            k: v.cuda() for k, v in inputs.items()
                        }
                    logits = self._model(**inputs).logits.view(-1).float()
                    all_scores.extend(torch.sigmoid(logits).cpu().tolist())
        except Exception as exc:  # noqa: BLE001
            raise RerankerError(f"Rerank 计算失败：{exc}") from exc
        for candidate, score in zip(candidates, all_scores):
            candidate.rerank_score = float(score)
        return sorted(
            candidates, key=lambda c: c.rerank_score or 0.0, reverse=True
        )
