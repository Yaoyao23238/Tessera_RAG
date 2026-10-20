"""缓存管理测试：切分缓存按变体清理 / embedding 缓存版本段与按内容清理。

对应缓存失效缺口修复：
- 重解析/清洗后切分缓存需可按变体失效（clear_chunk）
- embedding 缓存 key 含模型名 + 版本段，删除文档时按内容哈希清理（clear_embed）
"""

from __future__ import annotations

from app.services.cache import _EMBED_CACHE_VERSION, CacheManager


def test_clear_chunk_by_variant(tmp_path):
    cache = CacheManager(tmp_path / "cache")
    payload = {"parents": [], "children": [], "config_hash": "cfg"}
    cache.set_chunk("fh", "cfg", payload, variant="raw")
    cache.set_chunk("fh", "cfg", payload, variant="cleaned")
    cache.set_chunk("other", "cfg", payload, variant="raw")

    freed = cache.clear_chunk("fh", variant="cleaned")
    assert freed > 0
    assert cache.get_chunk("fh", "cfg", "cleaned") is None
    # raw 变体与其他文档不受影响
    assert cache.get_chunk("fh", "cfg", "raw") is not None
    assert cache.get_chunk("other", "cfg", "raw") is not None

    # 不指定 variant：全变体清理
    cache.clear_chunk("fh")
    assert cache.get_chunk("fh", "cfg", "raw") is None
    assert cache.get_chunk("other", "cfg", "raw") is not None


def test_embed_path_includes_cache_version(tmp_path):
    """embed 缓存 key 含模型名 + 版本段：模型实现变更时递增版本即可失效旧缓存。"""
    cache = CacheManager(tmp_path / "cache")
    path = cache.embed_path("th", "BAAI/bge-m3")
    assert path.name == f"embed_th_BAAI_bge-m3_{_EMBED_CACHE_VERSION}.npy"


def test_clear_embed_all_model_variants(tmp_path):
    cache = CacheManager(tmp_path / "cache")
    cache.set_embed("th", "model-a", b"x")
    cache.set_embed("th", "model-b", b"yy")
    cache.set_embed("other", "model-a", b"z")

    freed = cache.clear_embed("th")
    assert freed == 3  # x + yy
    assert cache.get_embed("th", "model-a") is None
    assert cache.get_embed("th", "model-b") is None
    # 其他内容哈希不受影响
    assert cache.get_embed("other", "model-a") is not None
