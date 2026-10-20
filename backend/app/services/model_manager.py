"""本地模型按需下载与管理。

- 模型注册表：BGE-M3 / Qwen3-Embedding-0.6B / Qwen3-Embedding-4B /
  bge-reranker-v2-m3（下载地址、体积、维度、加载器）
- ensure_model：huggingface_hub.snapshot_download 按需下载，
  后台线程轮询目录大小回调进度（downloaded_mb / total_mb），
  进度经 WebSocket model_download 频道推送（api 层接线）
- huggingface_hub 懒导入，未安装时仅在真正下载时报错
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 下载源枚举
DOWNLOAD_SOURCE_HF = "huggingface"
DOWNLOAD_SOURCE_HF_MIRROR = "hf_mirror"
DOWNLOAD_SOURCE_MODELSCOPE = "modelscope"
DOWNLOAD_SOURCES = (
    DOWNLOAD_SOURCE_HF,
    DOWNLOAD_SOURCE_HF_MIRROR,
    DOWNLOAD_SOURCE_MODELSCOPE,
)

# hf-mirror.com 镜像地址（国内连接 huggingface.co 会超时）
HF_MIRROR_ENDPOINT = "https://hf-mirror.com"

# Docling 模型包虚拟 id（设置页「下载 Docling 模型包」用）
DOCLING_BUNDLE_ID = "docling-bundle"

ProgressCallback = Callable[[float, float], None]  # (downloaded_mb, total_mb)


@dataclass(frozen=True)
class ModelInfo:
    """模型注册信息。"""

    model_id: str
    kind: str  # embedding / reranker
    size_mb: float
    dimensions: int = 0
    loader: str = "sentence_transformers"  # flagembedding / sentence_transformers
    description: str = ""


# 本地模型注册表（设置页「模型清单」数据源）
MODEL_REGISTRY: dict[str, ModelInfo] = {
    "BAAI/bge-m3": ModelInfo(
        model_id="BAAI/bge-m3",
        kind="embedding",
        size_mb=2270.0,
        dimensions=1024,
        loader="flagembedding",
        description="BGE-M3 中英多语，稠密+稀疏权重，默认推荐",
    ),
    "Qwen/Qwen3-Embedding-0.6B": ModelInfo(
        model_id="Qwen/Qwen3-Embedding-0.6B",
        kind="embedding",
        size_mb=1200.0,
        dimensions=1024,
        loader="sentence_transformers",
        description="Qwen3-Embedding 0.6B，低显存友好",
    ),
    "Qwen/Qwen3-Embedding-4B": ModelInfo(
        model_id="Qwen/Qwen3-Embedding-4B",
        kind="embedding",
        size_mb=8000.0,
        dimensions=2560,
        loader="sentence_transformers",
        description="Qwen3-Embedding 4B，高精度（显存要求高）",
    ),
    "BAAI/bge-reranker-v2-m3": ModelInfo(
        model_id="BAAI/bge-reranker-v2-m3",
        kind="reranker",
        size_mb=2270.0,
        loader="flagembedding",
        description="BGE Reranker v2 M3，检索精排",
    ),
}


class ModelDownloadError(Exception):
    """模型下载失败。"""


@dataclass
class ModelStatus:
    """模型下载状态。"""

    model_id: str
    kind: str = "embedding"
    size_mb: float = 0.0
    downloaded: bool = False
    downloading: bool = False
    local_path: str | None = None


class ModelManager:
    """本地模型下载与状态管理。

    下载源由外部（设置热更新）经 ``set_download_source`` 同步；
    hf_mirror 源通过 HF_ENDPOINT 环境变量全局生效（huggingface_hub 全家
    含 docling/sentence-transformers 都读它），modelscope 源仅对本模块
    ``ensure_model`` 生效（SDK 下载）。
    """

    def __init__(self, models_dir: str | Path) -> None:
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._downloading: set[str] = set()
        self._lock = threading.Lock()
        self._download_source: str = DOWNLOAD_SOURCE_HF

    # ------------------------------------------------------------------
    # 下载源
    # ------------------------------------------------------------------

    def set_download_source(self, source: str) -> None:
        """切换下载源；hf_mirror 时设置 HF_ENDPOINT 全局镜像。

        huggingface_hub（模型、docling、sentence-transformers 等）
        全部读取 HF_ENDPOINT，因此一个环境变量覆盖所有 HF 下载路径。
        """
        if source not in DOWNLOAD_SOURCES:
            logger.warning("未知下载源：%s，忽略", source)
            return
        self._download_source = source
        if source == DOWNLOAD_SOURCE_HF_MIRROR:
            os.environ["HF_ENDPOINT"] = HF_MIRROR_ENDPOINT
            logger.info("下载源切换为 hf-mirror（HF_ENDPOINT 已设置）")
        else:
            os.environ.pop("HF_ENDPOINT", None)
            logger.info("下载源切换为 %s（HF_ENDPOINT 已清除）", source)

    @property
    def download_source(self) -> str:
        return self._download_source

    # ------------------------------------------------------------------
    # Docling 模型包
    # ------------------------------------------------------------------

    def docling_dir(self) -> Path:
        """Docling 模型包目录（启动时经 DOCLING_ARTIFACTS_PATH 指向）。"""
        return self.models_dir / "docling"

    def is_docling_downloaded(self) -> bool:
        """判定 Docling 模型包已就绪：layout + tableformer 权重均存在。

        仅 layout 模型（约 160MB）不算完整，TableFormer/OCR 缺失时 PDF
        表格与扫描件无法解析。下载中断会留下只有 layout 的半成品目录。
        """
        path = self.docling_dir()
        if not path.is_dir():
            return False
        # TableFormer 是 PDF 表格解析的核心模型，目录名由 docling 内部定义
        tableformer_dir = path / "docling-project--docling-models"
        if not tableformer_dir.is_dir():
            return False
        weight_exts = (".safetensors", ".bin", ".onnx", ".pt", ".pth")
        has_layout = any(
            p.is_file() and p.suffix.lower() in weight_exts
            for p in path.rglob("*")
        )
        has_tableformer = any(
            p.is_file() and p.suffix.lower() in weight_exts
            for p in tableformer_dir.rglob("*")
        )
        return has_layout and has_tableformer

    def download_docling_bundle(self, progress_cb: ProgressCallback | None = None) -> Path:
        """下载 Docling 官方默认模型包（layout/table/OCR 等）。

        固定经 hf-mirror 下载（ModelScope 无 docling 镜像，直连会超时）。
        进度用轮询目录体积估算。
        """
        path = self.docling_dir()
        if self.is_docling_downloaded():
            return path
        with self._lock:
            if DOCLING_BUNDLE_ID in self._downloading:
                raise ModelDownloadError("Docling 模型包正在下载中")
            self._downloading.add(DOCLING_BUNDLE_ID)

        total_mb = 6000.0  # 估算体积（layout+table+ocr 模型包）
        stop_event = threading.Event()

        def _poll() -> None:
            while not stop_event.is_set():
                if progress_cb:
                    downloaded = _dir_size_mb(path)
                    progress_cb(min(downloaded, total_mb), total_mb)
                time.sleep(1.0)

        poller = threading.Thread(target=_poll, daemon=True)
        poller.start()
        try:
            # 强制走 hf-mirror（docling 模型不在 ModelScope 上）
            had_endpoint = os.environ.get("HF_ENDPOINT")
            os.environ["HF_ENDPOINT"] = HF_MIRROR_ENDPOINT
            # 禁用 Xet 协议：docling 大模型已迁移到 xethub 存储，hf-mirror
            # 镜像对该 CAS 服务返回 401，须回落标准 HTTP 下载（实测有效）
            had_disable_xet = os.environ.get("HF_HUB_DISABLE_XET")
            os.environ["HF_HUB_DISABLE_XET"] = "1"
            try:
                from docling.utils.model_downloader import download_models

                download_models(output_dir=path, progress=False)
            finally:
                if had_endpoint:
                    os.environ["HF_ENDPOINT"] = had_endpoint
                else:
                    os.environ.pop("HF_ENDPOINT", None)
                if had_disable_xet:
                    os.environ["HF_HUB_DISABLE_XET"] = had_disable_xet
                else:
                    os.environ.pop("HF_HUB_DISABLE_XET", None)
            if progress_cb:
                progress_cb(total_mb, total_mb)
            logger.info("Docling 模型包下载完成：%s", path)
            return path
        except Exception as exc:  # noqa: BLE001
            raise ModelDownloadError(f"Docling 模型包下载失败：{exc}") from exc
        finally:
            stop_event.set()
            with self._lock:
                self._downloading.discard(DOCLING_BUNDLE_ID)

    def cancel_download(self, model_id: str) -> bool:
        """中断下载：清下载标记 + 删除残留目录（尽力而为）。

        snapshot_download 本身无法中断，删目录后其内部会抛错退出；
        残留的未完成文件一并清理，下次可重新下载。
        """
        with self._lock:
            downloading = model_id in self._downloading
            self._downloading.discard(model_id)
        if model_id == DOCLING_BUNDLE_ID:
            target = self.docling_dir()
        else:
            target = self.local_path(model_id)
        if target.exists():
            try:
                shutil.rmtree(target, ignore_errors=True)
                logger.info("已清理模型残留目录：%s", target)
            except OSError:
                logger.warning("清理模型残留目录失败：%s", target, exc_info=True)
        return downloading

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def local_path(self, model_id: str) -> Path:
        """模型本地目录。"""
        return self.models_dir / model_id.replace("/", "_")

    def is_downloaded(self, model_id: str) -> bool:
        """判定已下载：目录存在且非空。"""
        path = self.local_path(model_id)
        return path.is_dir() and any(path.rglob("*"))

    def is_downloading(self, model_id: str) -> bool:
        """是否正在下载（含 Docling 模型包虚拟 id）。"""
        with self._lock:
            return model_id in self._downloading

    def status(self, model_id: str) -> ModelStatus:
        info = MODEL_REGISTRY.get(model_id)
        with self._lock:
            downloading = model_id in self._downloading
        return ModelStatus(
            model_id=model_id,
            kind=info.kind if info else "embedding",
            size_mb=info.size_mb if info else 0.0,
            downloaded=self.is_downloaded(model_id),
            downloading=downloading,
            local_path=str(self.local_path(model_id))
            if self.is_downloaded(model_id)
            else None,
        )

    def list_models(self) -> list[ModelStatus]:
        """注册表全部模型的状态清单。"""
        return [self.status(model_id) for model_id in MODEL_REGISTRY]

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------

    def ensure_model(
        self, model_id: str, progress_cb: ProgressCallback | None = None
    ) -> Path:
        """确保模型已下载，返回本地路径（幂等）。

        下载期间用后台线程轮询目录体积，按注册表体积估算进度。
        """
        path = self.local_path(model_id)
        if self.is_downloaded(model_id):
            return path

        with self._lock:
            if model_id in self._downloading:
                raise ModelDownloadError(f"模型 {model_id} 正在下载中")
            self._downloading.add(model_id)

        info = MODEL_REGISTRY.get(model_id)
        total_mb = info.size_mb if info else 0.0
        stop_event = threading.Event()

        def _poll_progress() -> None:
            while not stop_event.is_set():
                if progress_cb and total_mb > 0:
                    downloaded = _dir_size_mb(path)
                    progress_cb(min(downloaded, total_mb), total_mb)
                time.sleep(1.0)

        poller = threading.Thread(target=_poll_progress, daemon=True)
        poller.start()
        try:
            path.mkdir(parents=True, exist_ok=True)
            if self._download_source == DOWNLOAD_SOURCE_MODELSCOPE:
                # ModelScope 国内可达，SDK 下载（模型 id 与 HF 同格式）
                try:
                    from modelscope import snapshot_download as ms_download
                except ImportError as exc:
                    raise ModelDownloadError(
                        "modelscope 未安装，无法从 ModelScope 下载"
                        "（pip install modelscope；或切换下载源为 hf_mirror）"
                    ) from exc
                logger.info(
                    "开始从 ModelScope 下载模型 %s → %s", model_id, path
                )
                ms_download(model_id, local_dir=str(path))
            else:
                # huggingface / hf_mirror：snapshot_download 统一走
                # HF_ENDPOINT（hf_mirror 源时由 set_download_source 设置镜像）
                try:
                    from huggingface_hub import snapshot_download
                except ImportError as exc:
                    raise ModelDownloadError(
                        "huggingface_hub 未安装，无法下载模型"
                    ) from exc
                logger.info(
                    "开始下载模型 %s（源：%s）→ %s",
                    model_id,
                    self._download_source,
                    path,
                )
                snapshot_download(repo_id=model_id, local_dir=str(path))
            if progress_cb and total_mb > 0:
                progress_cb(total_mb, total_mb)
            logger.info("模型下载完成：%s", model_id)
            return path
        except ModelDownloadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ModelDownloadError(f"模型 {model_id} 下载失败：{exc}") from exc
        finally:
            stop_event.set()
            with self._lock:
                self._downloading.discard(model_id)


def _dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)
