"""设置读写：分组深度合并语义（无 DB / 无网络）。

覆盖「已落库的旧分组缺新字段」与「PUT /settings 按分组部分更新」两种场景：
两者都要求同组已有键不丢、缺失键回落默认值。整体替换会让重启前后的生效
配置分叉，故用测试锁住口径。
"""

from __future__ import annotations

from app.services.settings_store import DEFAULT_SETTINGS, deep_merge


class TestDeepMerge:
    def test_missing_keys_fall_back_to_base(self):
        assert deep_merge({"a": 1, "b": 2}, {"a": 9}) == {"a": 9, "b": 2}

    def test_new_key_in_override_is_added(self):
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_nested_dict_merged_not_replaced(self):
        merged = deep_merge({"x": {"p": 1, "q": 2}}, {"x": {"p": 9}})
        assert merged == {"x": {"p": 9, "q": 2}}

    def test_list_value_replaced_wholesale(self):
        # llm_providers 这类列表不做元素级合并
        merged = deep_merge({"providers": ["a", "b"]}, {"providers": ["c"]})
        assert merged == {"providers": ["c"]}

    def test_non_dict_override_replaces(self):
        assert deep_merge({"a": 1}, None) is None
        assert deep_merge({"a": 1}, [1, 2]) == [1, 2]

    def test_base_not_mutated(self):
        base = {"x": {"p": 1}}
        merged = deep_merge(base, {"x": {"q": 2}})
        assert base == {"x": {"p": 1}}
        assert merged == {"x": {"p": 1, "q": 2}}

    def test_non_dict_base_becomes_override(self):
        assert deep_merge(None, {"a": 1}) == {"a": 1}


class TestStoredGroupBackwardCompat:
    def test_partial_stored_group_keeps_defaults(self):
        """旧版本落库的分组缺新字段时，新字段取默认值而不是消失。"""
        stored = {"backend": "qdrant", "collection_name": "tessera"}
        merged = deep_merge(DEFAULT_SETTINGS["vector_store"], stored)
        assert merged["backend"] == "qdrant"
        assert merged["collection_name"] == "tessera"
        # 落库数据里没有的键 → 来自默认值
        assert merged["connection_url"] == DEFAULT_SETTINGS["vector_store"]["connection_url"]
        assert merged["config"] == DEFAULT_SETTINGS["vector_store"]["config"]

    def test_nested_config_dict_keeps_default_keys(self):
        merged = deep_merge(
            DEFAULT_SETTINGS["vector_store"], {"config": {"timeout": 5}}
        )
        assert merged["config"]["timeout"] == 5
        assert merged["config"] == {**DEFAULT_SETTINGS["vector_store"]["config"], "timeout": 5}
