"""应用配置：环境变量读取 + 数据目录初始化。

所有运行时数据（上传文件、解析缓存、SQLite、向量库文件、模型、日志）
默认存于项目内 ``backend/data``，可通过环境变量 ``TESSERA_DATA_DIR`` 覆盖。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置，环境变量前缀 TESSERA_。"""

    model_config = SettingsConfigDict(
        env_prefix="TESSERA_", env_file=".env", extra="ignore"
    )

    app_name: str = "Tessera"
    version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8766
    # 数据根目录（全链路 pathlib + UTF-8，兼容 Windows 中文路径）
    # 默认项目内 backend/data，避免往用户主目录（常位于 C 盘）写大文件
    data_dir: Path = Path(__file__).resolve().parents[1] / "data"
    max_file_size_mb: int = 500
    cache_size_limit_gb: float = 2.0
    log_level: str = "INFO"

    # ---- 派生路径 ----

    @property
    def db_path(self) -> Path:
        """SQLite 元数据库路径。"""
        return self.data_dir / "tessera.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def vector_dir(self) -> Path:
        """本地向量库（LanceDB / sqlite-vec）文件目录。"""
        return self.data_dir / "vector_store"

    @property
    def models_dir(self) -> Path:
        """本地模型（BGE-M3 / Qwen3 / reranker）下载目录。"""
        return self.data_dir / "models"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    def ensure_dirs(self) -> None:
        """创建所有运行时目录（幂等）。"""
        for d in (
            self.data_dir,
            self.upload_dir,
            self.cache_dir,
            self.vector_dir,
            self.models_dir,
            self.export_dir,
            self.log_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """配置单例（lru_cache 保证进程内唯一）。"""
    settings = Settings()
    settings.ensure_dirs()
    return settings
