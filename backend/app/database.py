"""SQLAlchemy 2.0 异步引擎与会话管理。

并发策略：WAL 模式（读写不互斥，设置一次持久存于 DB 文件）+
busy_timeout=5000（每连接生效，后台向量化写库与前端查询并发时
等待而非直接报 database is locked）。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.models.db import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _on_connect(dbapi_conn, _record) -> None:
    """每连接 PRAGMA：busy_timeout 不持久，必须连接级设置。"""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def get_engine():
    """懒创建异步引擎（避免 import 时即触盘）。"""
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, echo=False, future=True)
        event.listen(_engine.sync_engine, "connect", _on_connect)
        _session_factory = async_sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：请求级异步会话。"""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_db() -> None:
    """建表（幂等）+ WAL + 轻量迁移，应用启动时调用。

    create_all 只会新建缺失的表，不会给已存在的表补列；
    这里用 PRAGMA 检查后 ALTER 补齐新增列（SQLite 兼容）。
    """
    engine = get_engine()
    async with engine.begin() as conn:
        # WAL 读写不互斥；设置持久存于 DB 文件，幂等
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.run_sync(Base.metadata.create_all)
        # 存量库迁移：qa_items.source_content（QA 内容快照，用于实验重切后的命中判定）
        result = await conn.execute(text("PRAGMA table_info(qa_items)"))
        columns = {row[1] for row in result.fetchall()}
        if "source_content" not in columns:
            await conn.execute(
                text("ALTER TABLE qa_items ADD COLUMN source_content TEXT")
            )
        # 存量库迁移：documents.parse_variant（切分链路使用的解析版本 raw/cleaned）
        result = await conn.execute(text("PRAGMA table_info(documents)"))
        columns = {row[1] for row in result.fetchall()}
        if "parse_variant" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN parse_variant "
                    "VARCHAR(16) DEFAULT 'raw' NOT NULL"
                )
            )


async def dispose_db() -> None:
    """关闭引擎，应用关闭时调用。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
