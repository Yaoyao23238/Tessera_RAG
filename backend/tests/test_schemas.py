"""Schema 序列化测试：datetime 字段必须输出时区（补 UTC），前端才能正确转本地时区。

回归背景：SQLite 读回的 datetime 是 naive（不保留时区），此前 Pydantic 序列化
输出无 Z 的 ISO，前端按本地时区解析，导致显示时间偏差 8 小时。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.schemas import DocumentOut


def _doc(created_at: datetime) -> DocumentOut:
    return DocumentOut(
        id="doc1",
        filename="a.pdf",
        file_path="/tmp/a.pdf",
        file_hash="h",
        file_type="pdf",
        file_size=1,
        page_count=1,
        status="uploaded",
        title="a",
        created_at=created_at,
        updated_at=created_at,
    )


def test_naive_datetime_serialized_with_utc_offset():
    """naive（SQLite 读回）也被视为 UTC，序列化带 +00:00 时区后缀。"""
    naive = datetime(2026, 8, 14, 9, 30).replace(tzinfo=None)
    out = _doc(naive).model_dump(mode="json")
    assert out["created_at"].endswith("+00:00")


def test_aware_datetime_preserved():
    """已是 aware UTC 的输入原样输出，不二次处理。"""
    aware = datetime(2026, 8, 14, 9, 30, tzinfo=timezone.utc)
    out = _doc(aware).model_dump(mode="json")
    assert out["created_at"].endswith("+00:00")