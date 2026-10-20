"""三级缓存 + LRU 淘汰（ARCHITECTURE 第十一章）。

- 解析缓存：parse_{file_hash}.json（Docling 原始块流）；
  parse_{file_hash}_cleaned.json（LLM 清洗版，含 meta：prompt 版本/token 消耗）
- 切分缓存：chunk_{file_hash}_{variant}_{config_hash}.json（LLM 开启时不缓存；
  variant 为解析版本 raw/cleaned，防止两版切分结果互相污染）
- Embedding 缓存：embed_{text_hash}_{model_name}.npy（原始字节读写，
  numpy 序列化由调用方负责，本模块保持纯 stdlib）

缓存总大小超上限（默认 2GB）时按最后访问时间 LRU 淘汰。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_KINDS = ("parse", "chunk", "embed")

#: Embedding 缓存格式/模型实现版本：本地 embedding 模型实现变更（如预处理、
#: 池化方式修正）而 model_name 未变时递增，使旧向量缓存自动失效。
_EMBED_CACHE_VERSION = "v1"


def _safe_unlink(path: Path) -> bool:
    """尽力删除文件：失败（如权限/占用/外部安全策略拦截）记警告但不抛错。

    缓存删除属可降级操作——删不掉只是多占磁盘，不应让主流程（如删文档）失败。
    """
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        logger.warning("缓存文件删除失败（已跳过）：%s（%s）", path, exc)
        return False


class CacheManager:
    """缓存管理器（线程内无状态，可安全共享）。"""

    def __init__(self, cache_dir: str | Path, size_limit_gb: float = 2.0) -> None:
        self.root = Path(cache_dir)
        self.limit_bytes = int(size_limit_gb * (1024**3))
        for kind in _CACHE_KINDS:
            (self.root / kind).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------

    def parse_path(self, file_hash: str) -> Path:
        return self.root / "parse" / f"parse_{file_hash}.json"

    def parse_cleaned_path(self, file_hash: str) -> Path:
        """LLM 清洗版解析缓存（与原始缓存并存，支持回退）。"""
        return self.root / "parse" / f"parse_{file_hash}_cleaned.json"

    def chunk_path(self, file_hash: str, config_hash: str, variant: str = "raw") -> Path:
        # variant 混入 key：清洗版与原始版的切分结果互不污染
        return self.root / "chunk" / f"chunk_{file_hash}_{variant}_{config_hash}.json"

    def embed_path(self, text_hash_value: str, model_name: str) -> Path:
        # key 含模型名 + 缓存版本：模型实现变更（version 递增）后旧缓存自动失效
        safe_model = model_name.replace("/", "_")
        return (
            self.root
            / "embed"
            / f"embed_{text_hash_value}_{safe_model}_{_EMBED_CACHE_VERSION}.npy"
        )

    # ------------------------------------------------------------------
    # 解析缓存
    # ------------------------------------------------------------------

    def get_parse(self, file_hash: str) -> list[dict[str, Any]] | None:
        data = self._read_json(self.parse_path(file_hash))
        if data is None:
            return None
        return self._apply_heading_inference(data)

    def set_parse(self, file_hash: str, blocks: list[dict[str, Any]]) -> Path:
        return self._write_json(self.parse_path(file_hash), blocks)

    def get_parse_cleaned(self, file_hash: str) -> dict[str, Any] | None:
        """读取清洗版缓存：{"meta": {...}, "blocks": [...]}。"""
        data = self._read_json(self.parse_cleaned_path(file_hash))
        if data is None:
            return None
        if "blocks" in data:
            data["blocks"] = self._apply_heading_inference(data["blocks"])
        return data

    def set_parse_cleaned(
        self, file_hash: str, blocks: list[dict[str, Any]], meta: dict[str, Any]
    ) -> Path:
        return self._write_json(
            self.parse_cleaned_path(file_hash), {"meta": meta, "blocks": blocks}
        )

    def clear_parse_cleaned(self, file_hash: str) -> None:
        """删除清洗版缓存（重新解析/回退原始版时调用）。"""
        _safe_unlink(self.parse_cleaned_path(file_hash))

    @staticmethod
    def _apply_heading_inference(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """读取时惰性标题推断（幂等）：旧缓存早于推断功能生成时无标题。

        只提升 paragraph 类型，已推断过的缓存不受影响（推断只处理
        paragraph，heading 原样保留）。为零成本纯函数，115 块级别
        开销微秒级，远低于 JSON 反序列化本身。
        """
        from app.core.heading_infer import infer_headings
        from app.core.types import Block

        parsed = [Block.from_dict(d) for d in blocks]
        infer_headings(parsed)
        return [b.to_dict() for b in parsed]

    # ------------------------------------------------------------------
    # 切分缓存
    # ------------------------------------------------------------------

    def get_chunk(
        self, file_hash: str, config_hash: str, variant: str = "raw"
    ) -> dict[str, Any] | None:
        return self._read_json(self.chunk_path(file_hash, config_hash, variant))

    def set_chunk(
        self,
        file_hash: str,
        config_hash: str,
        result: dict[str, Any],
        variant: str = "raw",
    ) -> Path:
        return self._write_json(
            self.chunk_path(file_hash, config_hash, variant), result
        )

    # ------------------------------------------------------------------
    # Embedding 缓存（原始字节）
    # ------------------------------------------------------------------

    def get_embed(self, text_hash_value: str, model_name: str) -> bytes | None:
        path = self.embed_path(text_hash_value, model_name)
        if not path.is_file():
            return None
        self._touch(path)
        try:
            return path.read_bytes()
        except OSError:
            return None

    def set_embed(
        self, text_hash_value: str, model_name: str, data: bytes
    ) -> Path:
        path = self.embed_path(text_hash_value, model_name)
        path.write_bytes(data)
        self._enforce_lru()
        return path

    # ------------------------------------------------------------------
    # 管理
    # ------------------------------------------------------------------

    def sizes(self) -> dict[str, int]:
        """各类型缓存占用（字节）与总量。"""
        result = {f"{kind}_bytes": self._kind_size(kind) for kind in _CACHE_KINDS}
        result["total_bytes"] = sum(result.values())
        result["limit_bytes"] = self.limit_bytes
        return result

    def clear(self, kind: str | None = None) -> int:
        """清理缓存（按类型或全部），返回释放字节数。"""
        kinds = _CACHE_KINDS if kind in (None, "all") else (kind,)
        freed = 0
        for k in kinds:
            if k not in _CACHE_KINDS:
                continue
            directory = self.root / k
            for file in directory.glob("*"):
                if file.is_file():
                    size = file.stat().st_size
                    if _safe_unlink(file):
                        freed += size
        return freed

    def clear_chunk(self, file_hash: str, variant: str | None = None) -> int:
        """清理指定文档的切分缓存；variant 指定时只清该变体（raw/cleaned）。

        用途：重解析后全部切分缓存作废；LLM 清洗产物更新后仅 cleaned
        变体作废（raw 变体缓存仍可复用）。
        """
        freed = 0
        pattern = (
            f"chunk_{file_hash}_{variant}_*.json"
            if variant
            else f"chunk_{file_hash}_*.json"
        )
        for file in (self.root / "chunk").glob(pattern):
            size = file.stat().st_size
            if _safe_unlink(file):
                freed += size
        return freed

    def clear_embed(self, text_hash_value: str) -> int:
        """按内容哈希清理 embedding 缓存（所有模型与版本变体）。

        注意：embed 缓存按内容哈希跨文档共享，删除文档时调用会使
        含相同文本的其他文档缓存一并失效（代价是重算一次 embedding）。
        """
        freed = 0
        for file in (self.root / "embed").glob(f"embed_{text_hash_value}_*.npy"):
            size = file.stat().st_size
            if _safe_unlink(file):
                freed += size
        return freed

    def clear_document_caches(self, file_hash: str) -> int:
        """删除文档时级联清理其切分缓存（解析缓存保留供同 hash 复用）。"""
        return self.clear_chunk(file_hash)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _kind_size(self, kind: str) -> int:
        directory = self.root / kind
        if not directory.exists():
            return 0
        return sum(f.stat().st_size for f in directory.glob("*") if f.is_file())

    def _read_json(self, path: Path) -> Any | None:
        if not path.is_file():
            return None
        self._touch(path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("缓存读取失败，视为未命中：%s", path)
            return None

    def _write_json(self, path: Path, data: Any) -> Path:
        path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        self._enforce_lru()
        return path

    @staticmethod
    def _touch(path: Path) -> None:
        """刷新访问时间（LRU 依据）。"""
        try:
            now = time.time()
            os.utime(path, (now, now))
        except OSError:
            pass

    def _enforce_lru(self) -> None:
        """总大小超限时按最后访问时间淘汰最旧文件。"""
        total = sum(self._kind_size(kind) for kind in _CACHE_KINDS)
        if total <= self.limit_bytes:
            return
        files: list[tuple[float, int, Path]] = []
        for kind in _CACHE_KINDS:
            for file in (self.root / kind).glob("*"):
                if file.is_file():
                    stat = file.stat()
                    files.append((stat.st_atime, stat.st_size, file))
        files.sort(key=lambda item: item[0])  # 最旧优先
        for _, size, file in files:
            if total <= self.limit_bytes:
                break
            if _safe_unlink(file):
                total -= size
                logger.info("LRU 淘汰缓存：%s（%d 字节）", file.name, size)
