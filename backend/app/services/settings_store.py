"""设置持久化：Setting 表（key/value JSON）读写与默认值合并。

设置分组 key：app / llm_providers / embedding / reranker / vector_store /
chunking。读取时与默认值深度合并，保证新增字段向后兼容。
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import ChunkingConfig
from app.models.db import Setting

SETTING_KEYS = (
    "app",
    "llm_providers",
    "embedding",
    "reranker",
    "vector_store",
    "chunking",
    "download",
)

# 默认配置（DESIGN 3.2 Settings 变更口径）
DEFAULT_SETTINGS: dict[str, Any] = {
    "app": {
        "data_dir": "",
        "max_file_size_mb": 500,
        "cache_size_limit_gb": 2.0,
    },
    "llm_providers": [],
    "embedding": {
        "provider_type": "local",
        "model_name": "BAAI/bge-m3",
        "api_key": None,
        "base_url": None,
        "dimensions": 1024,
    },
    "reranker": {
        "enabled": False,
        "model_name": "BAAI/bge-reranker-v2-m3",
        "top_n": 20,
        # 精排最低分阈值。0 = 不过滤；0.1 校准于 bge-reranker-v2-m3 的 sigmoid
        # 双峰（实测正例 ~0.99 / 负例 ~0.01，差百倍）。负例（全库无答案的查询）
        # 靠它返回空而不是噪声。换 reranker 模型需重新校准此值。
        "min_score": 0.1,
    },
    "vector_store": {
        "backend": "lancedb",
        "connection_url": None,
        "collection_name": "tessera",
        "config": {},
    },
    "chunking": ChunkingConfig().to_dict(),
    "download": {
        # huggingface（直连）/ hf_mirror（huggingface 镜像，国内推荐）/ modelscope
        "source": "hf_mirror",
    },
}


def default_settings() -> dict[str, Any]:
    """返回默认配置的深拷贝。"""
    return copy.deepcopy(DEFAULT_SETTINGS)


def deep_merge(base: Any, override: Any) -> Any:
    """把 override 深度合并进 base 的拷贝（override 非 dict 时整体替换）。

    分组值为 dict 时逐键合并，保证「已落库的旧分组缺新字段」时新字段仍取默认值；
    列表（如 llm_providers）按整体替换处理，不做元素级合并。
    """
    if not isinstance(override, dict):
        return copy.deepcopy(override)
    merged = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


async def load_settings(session: AsyncSession) -> dict[str, Any]:
    """读取全部设置，与默认值深度合并。"""
    merged = default_settings()
    rows = (await session.execute(select(Setting))).scalars().all()
    for row in rows:
        if row.key in SETTING_KEYS:
            merged[row.key] = deep_merge(merged[row.key], row.value)
    return merged


async def save_setting(
    session: AsyncSession, key: str, value: Any
) -> None:
    """写入单个设置分组（存在即更新）。"""
    if key not in SETTING_KEYS:
        raise ValueError(f"未知设置分组：{key}")
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.commit()
