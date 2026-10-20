"""SQLAlchemy 2.0 ORM 模型。

包含：Document / Chunk / Task / Settings / QASet / QAItem / Experiment /
ChunkStatsSnapshot。状态机枚举与前端 ``lib/constants.ts`` 保持一致。
时间一律 ISO 8601 UTC 存储。
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    """生成 UUID 主键。"""
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    """当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """ORM 基类。"""


class DocStatus(str, enum.Enum):
    """文档状态机（前后端共用常量，见共享知识 #2）。"""

    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    CHUNKING = "chunking"
    CHUNKED = "chunked"
    EMBEDDING = "embedding"
    INDEXED = "indexed"
    ENCRYPTED = "encrypted"
    PARSE_FAILED = "parse_failed"
    CHUNK_FAILED = "chunk_failed"
    EMBED_FAILED = "embed_failed"
    STORE_FAILED = "store_failed"


# 供前端 constants.ts 对齐使用
DOC_STATUS_VALUES: list[str] = [s.value for s in DocStatus]


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExpStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class QASetSource(str, enum.Enum):
    UPLOAD = "upload"
    LLM_GENERATED = "llm_generated"


class ChunkTypeStr(str, enum.Enum):
    """chunk_type 枚举（共享知识 #6）。"""

    TEXT = "text"
    TABLE = "table"
    CODE = "code"
    IMAGE = "image"
    HEADING = "heading"


CHUNK_TYPE_VALUES: list[str] = [t.value for t in ChunkTypeStr]


class Document(Base):
    """文档表。"""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(sa.String(1024), nullable=False)
    file_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    file_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    file_size: Mapped[int] = mapped_column(sa.Integer, default=0)
    page_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    status: Mapped[str] = mapped_column(
        sa.String(32), default=DocStatus.UPLOADED.value, index=True
    )
    title: Mapped[str] = mapped_column(sa.String(512), default="")
    author: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    lang: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    parse_cache_path: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    # 切分链路使用的解析版本：raw（Docling 原始）/ cleaned（LLM 清洗版）
    parse_variant: Mapped[str] = mapped_column(sa.String(16), default="raw")
    chunk_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    embedded_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Chunk(Base):
    """切分块表（父块不向量化，子块 is_embedded 指向 parent_id）。"""

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        sa.String(64),
        sa.ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[str | None] = mapped_column(
        sa.String(64), nullable=True, index=True
    )
    chunk_index: Mapped[int] = mapped_column(sa.Integer, default=0)
    content: Mapped[str] = mapped_column(sa.Text, default="")
    chunk_type: Mapped[str] = mapped_column(
        sa.String(16), default=ChunkTypeStr.TEXT.value
    )
    breadcrumb: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
    page_number: Mapped[int] = mapped_column(sa.Integer, default=0)
    end_page: Mapped[int] = mapped_column(sa.Integer, default=0)
    token_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    char_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    strategy: Mapped[str] = mapped_column(sa.String(16), default="structure")
    bbox: Mapped[list[float]] = mapped_column(sa.JSON, default=list)
    block_ids: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
    table_data: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    heading_text: Mapped[str] = mapped_column(sa.String(512), default="")
    embedding_model: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    is_embedded: Mapped[bool] = mapped_column(sa.Boolean, default=False, index=True)
    vector_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        sa.Index("ix_chunks_doc_index", "document_id", "chunk_index"),
    )


class Task(Base):
    """后台任务表（崩溃恢复：重启扫描 pending/running 标记 failed）。"""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True, default=_uuid)
    document_id: Mapped[str | None] = mapped_column(
        sa.String(64), nullable=True, index=True
    )
    task_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), default=TaskStatus.PENDING.value, index=True
    )
    progress: Mapped[int] = mapped_column(sa.Integer, default=0)
    message: Mapped[str] = mapped_column(sa.String(512), default="")
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )


class Setting(Base):
    """单行配置表（key 唯一，value 为 JSON）。

    存放 AppConfig / LLMProvider 列表 / EmbeddingConfig / RerankerConfig /
    VectorStoreConfig / ChunkingConfig 等。
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class QASet(Base):
    """评测 Q&A 测试集。"""

    __tablename__ = "qa_sets"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    source: Mapped[str] = mapped_column(
        sa.String(32), default=QASetSource.UPLOAD.value
    )
    document_id: Mapped[str | None] = mapped_column(
        sa.String(64), nullable=True, index=True
    )
    item_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    file_path: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow
    )


class QAItem(Base):
    """测试集条目（source_chunk_id 用于命中判定）。"""

    __tablename__ = "qa_items"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True, default=_uuid)
    qa_set_id: Mapped[str] = mapped_column(
        sa.String(64),
        sa.ForeignKey("qa_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question: Mapped[str] = mapped_column(sa.Text, nullable=False)
    answer: Mapped[str] = mapped_column(sa.Text, default="")
    source_chunk_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    # 源 chunk 内容快照：实验按新配置重切后 chunk id 全变，
    # 命中判定用内容重叠兜底（存量库由 database.init_db 迁移补列）
    source_content: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)


class Experiment(Base):
    """评测实验（独立向量表 exp_{id前8位}，删除实验级联 drop_table）。"""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(sa.String(256), default="")
    qa_set_id: Mapped[str] = mapped_column(
        sa.String(64),
        sa.ForeignKey("qa_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunking_config: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    embedding_model: Mapped[str] = mapped_column(sa.String(256), default="")
    vector_table: Mapped[str] = mapped_column(sa.String(128), default="")
    document_ids: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
    recall_at_1: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    recall_at_5: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    recall_at_10: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    mrr: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    status: Mapped[str] = mapped_column(
        sa.String(16), default=ExpStatus.PENDING.value, index=True
    )
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )


class ChunkStatsSnapshot(Base):
    """切分统计快照（每文档保留最近 5 版）。"""

    __tablename__ = "chunk_stats_snapshots"

    id: Mapped[str] = mapped_column(sa.String(64), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        sa.String(64),
        sa.ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    config_hash: Mapped[str] = mapped_column(sa.String(64), default="")
    version: Mapped[int] = mapped_column(sa.Integer, default=1)
    histogram: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, default=list)
    type_counts: Mapped[dict[str, int]] = mapped_column(sa.JSON, default=dict)
    mean_tokens: Mapped[float] = mapped_column(sa.Float, default=0.0)
    median_tokens: Mapped[float] = mapped_column(sa.Float, default=0.0)
    chunk_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        sa.Index("ix_stats_doc_version", "document_id", "version"),
    )
