"""API 层测试：FastAPI TestClient + 临时数据目录 + fake core 注入。

后台任务分发关闭（runtime.dispatch_enabled=False），只验证
参数校验 / 响应格式 / DB 落库 / 级联行为。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from conftest import FakeEmbedder, FakeVectorStore
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db import (
    Chunk,
    ChunkStatsSnapshot,
    Document,
    DocStatus,
    QAItem,
    QASet,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """带 lifespan 的测试客户端；core 单例替换为 fake。"""
    monkeypatch.setenv("TESSERA_DATA_DIR", str(tmp_path / "data"))
    from app.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        runtime = test_client.app.state.runtime
        runtime.dispatch_enabled = False  # 不实际跑后台任务
        runtime.set_embedder(FakeEmbedder())
        fake_store = FakeVectorStore()
        runtime.set_vector_store(fake_store)
        test_client.fake_store = fake_store  # type: ignore[attr-defined]
        test_client.data_dir = tmp_path / "data"  # type: ignore[attr-defined]
        yield test_client
    get_settings.cache_clear()


def seed(client, *objects) -> None:
    """用独立 engine 向测试库写入种子数据。"""
    db_path = Path(client.data_dir) / "tessera.db"

    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add_all(objects)
            await session.commit()
        await engine.dispose()

    asyncio.run(_run())


def make_doc(doc_id: str = "doc1", **kwargs) -> Document:
    defaults = {
        "id": doc_id,
        "filename": "论文.pdf",
        "file_path": "/tmp/论文.pdf",
        "file_hash": f"hash_{doc_id}",
        "file_type": "pdf",
        "status": DocStatus.CHUNKED.value,
        "title": "论文",
    }
    defaults.update(kwargs)
    return Document(**defaults)


def make_chunk(chunk_id: str, doc_id: str = "doc1", **kwargs) -> Chunk:
    defaults = {
        "id": chunk_id,
        "document_id": doc_id,
        "content": f"内容{chunk_id}",
        "chunk_index": 0,
        "token_count": 10,
    }
    defaults.update(kwargs)
    return Chunk(**defaults)


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------


class TestDocuments:
    def test_health(self, client):
        resp = client.get("/api/health")
        body = resp.json()
        assert body["code"] == 0
        # 模型就绪状态字段（预热进度可观测；不触发加载）
        models = body["data"]["models"]
        assert set(models) == {"embedder_ready", "reranker_ready"}

    def test_upload_txt_then_duplicate(self, client):
        files = [("files", ("笔记.txt", "第一行\n第二行".encode("utf-8"), "text/plain"))]
        resp = client.post("/api/documents/upload", files=files)
        body = resp.json()
        assert body["code"] == 0
        result = body["data"]["results"][0]
        assert result["status"] == "created"
        doc_id = result["document"]["id"]

        # 同内容再传 → exists 提示
        resp = client.post("/api/documents/upload", files=files)
        assert resp.json()["data"]["results"][0]["status"] == "exists"

        # 列表与详情
        listing = client.get("/api/documents").json()["data"]
        assert listing["total"] == 1
        detail = client.get(f"/api/documents/{doc_id}").json()["data"]
        assert detail["filename"] == "笔记.txt"
        assert detail["status"] == "uploaded"  # dispatch 关闭，停在 uploaded

        # 删除
        deleted = client.delete(f"/api/documents/{doc_id}").json()
        assert deleted["code"] == 0
        missing = client.get(f"/api/documents/{doc_id}").json()
        assert missing["code"] == 1002

    def test_upload_reject_unsupported(self, client):
        files = [("files", ("evil.exe", b"MZ", "application/octet-stream"))]
        resp = client.post("/api/documents/upload", files=files)
        result = resp.json()["data"]["results"][0]
        assert result["status"] == "rejected"
        assert result["code"] == 2001

    def test_batch_delete(self, client):
        seed(client, make_doc("d1"), make_doc("d2"))
        resp = client.post(
            "/api/documents/batch", json={"action": "delete", "ids": ["d1", "d2"]}
        )
        assert resp.json()["data"]["affected"] == ["d1", "d2"]
        assert client.get("/api/documents").json()["data"]["total"] == 0

    def test_delete_document_clears_embed_cache(self, client):
        """删除文档级联清理其 chunk 内容对应的 embedding 缓存（所有模型变体）。"""
        from app.services.file_storage import text_hash

        seed(client, make_doc("d1"), make_chunk("c1", "d1"), make_chunk("c2", "d1"))
        cache = client.app.state.runtime.cache
        hashes = [text_hash(f"内容{cid}") for cid in ("c1", "c2")]
        for h in hashes:
            cache.set_embed(h, "model-a", b"x")
            cache.set_embed(h, "model-b", b"y")
        other_hash = text_hash("无关内容")
        cache.set_embed(other_hash, "model-a", b"z")

        deleted = client.delete("/api/documents/d1").json()
        assert deleted["code"] == 0
        for h in hashes:
            assert cache.get_embed(h, "model-a") is None
            assert cache.get_embed(h, "model-b") is None
        # 不属于该文档的内容缓存保留
        assert cache.get_embed(other_hash, "model-a") is not None


# ---------------------------------------------------------------------------
# URL 导入
# ---------------------------------------------------------------------------


class _FakeUrlResponse:
    """伪造 httpx 响应：固定 HTML 内容 + text/html 头。"""

    def __init__(self, content: bytes, content_type: str = "text/html; charset=utf-8"):
        self.headers = {"content-type": content_type}
        self._content = content

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        yield self._content


class _FakeUrlClient:
    """伪造 httpx.AsyncClient：stream 返回固定响应。"""

    def __init__(self, *args, **kwargs):
        self._response = _FakeUrlResponse(b'<html><body>hello \xe4\xb8\x96\xe7\x95\x8c</body></html>')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args, **kwargs):
        return False

    def stream(self, method, url, **kwargs):
        return _FakeUrlCtx(self._response)


class _FakeUrlCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args, **kwargs):
        return False


class TestUrlImport:
    def test_import_url_creates_then_duplicate(self, client, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _FakeUrlClient)
        resp = client.post(
            "/api/documents/import-url", json={"url": "https://example.com/page"}
        )
        body = resp.json()
        assert body["code"] == 0
        result = body["data"]["results"][0]
        assert result["status"] == "created"
        assert result["filename"].endswith(".html")

        # 同内容再导入 → exists 提示
        resp2 = client.post(
            "/api/documents/import-url", json={"url": "https://example.com/page"}
        )
        assert resp2.json()["data"]["results"][0]["status"] == "exists"

    def test_import_url_invalid_scheme(self, client):
        for bad in ("", "ftp://x", "javascript:alert(1)", "not-a-url"):
            resp = client.post("/api/documents/import-url", json={"url": bad})
            assert resp.json()["code"] == 1001

    def test_url_filename_extension_mapping(self):
        from app.api.documents import _url_filename

        assert _url_filename("https://x.com/a/b", "text/html") == "x.com_b.html"
        assert _url_filename("https://x.com/report.pdf", "application/pdf") == "x.com_report.pdf"
        # URL 路径已有受支持扩展名且 Content-Type 未知 → 回退路径扩展名
        assert _url_filename("https://x.com/doc.docx", "application/octet-stream") == "x.com_doc.docx"
        # 未知 Content-Type 且路径无扩展名 → 默认 .html
        assert _url_filename("https://x.com/news", "application/octet-stream") == "x.com_news.html"


# ---------------------------------------------------------------------------
# 切分查询与入库
# ---------------------------------------------------------------------------


class TestChunks:
    def _seed_chunks(self, client):
        parent = make_chunk("p1", content="父块内容", parent_id=None)
        child = make_chunk("c1", parent_id="p1", chunk_type="text")
        table = make_chunk("t1", chunk_type="table", parent_id="p1")
        seed(client, make_doc(), parent, child, table)

    def test_chunk_list_filters(self, client):
        self._seed_chunks(client)
        data = client.get("/api/documents/doc1/chunks").json()["data"]
        assert data["total"] == 3
        by_type = client.get("/api/documents/doc1/chunks?chunk_type=table").json()["data"]
        assert by_type["total"] == 1
        by_role = client.get("/api/documents/doc1/chunks?role=parent").json()["data"]
        assert [c["id"] for c in by_role["items"]] == ["p1"]
        by_child = client.get("/api/documents/doc1/chunks?role=child").json()["data"]
        assert by_child["total"] == 2
        by_keyword = client.get(
            "/api/documents/doc1/chunks?keyword=父块"
        ).json()["data"]
        assert by_keyword["total"] == 1
        page = client.get("/api/documents/doc1/chunks?page_size=2&page=2").json()["data"]
        assert len(page["items"]) == 1

    def test_chunk_detail_and_404(self, client):
        self._seed_chunks(client)
        detail = client.get("/api/documents/doc1/chunks/c1").json()
        assert detail["data"]["id"] == "c1"
        missing = client.get("/api/documents/doc1/chunks/nope").json()
        assert missing["code"] == 1002

    def test_trigger_chunk_and_index(self, client):
        seed(client, make_doc(status=DocStatus.PARSED.value))
        resp = client.post("/api/documents/doc1/chunk", json={"config": None})
        assert resp.json()["code"] == 0
        seed(client, make_doc("d9", status=DocStatus.UPLOADED.value))
        bad = client.post("/api/documents/d9/chunk", json={})
        assert bad.json()["code"] == 1001
        # 确认入库
        seed(client, make_doc("d8", status=DocStatus.CHUNKED.value))
        ok_resp = client.post("/api/documents/d8/index")
        assert ok_resp.json()["data"]["dispatched"] == "embed"


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_with_parent_expansion(self, client):
        parent = make_chunk("p1", content="父块完整内容")
        child = make_chunk("c1", content="子块片段", parent_id="p1", page_number=7)
        seed(client, make_doc(), parent, child)
        from app.core.vector_store import VectorItem

        client.fake_store.upsert(
            [VectorItem(id="c1", vector=[1.0] * 8, content="子块片段")]
        )
        resp = client.post("/api/search", json={"query": "实验数据集", "top_k": 5})
        body = resp.json()
        assert body["code"] == 0
        results = body["data"]["results"]
        assert len(results) == 1
        assert results[0]["chunk_id"] == "c1"
        assert results[0]["parent_content"] == "父块完整内容"
        assert results[0]["matched_child_text"] == "子块片段"
        assert results[0]["source_file"] == "论文.pdf"
        assert results[0]["page_number"] == 7

    def test_search_empty_query(self, client):
        resp = client.post("/api/search", json={"query": "  "})
        assert resp.json()["code"] == 1001


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------


class TestSettings:
    def test_get_and_put_hot_reload(self, client):
        settings = client.get("/api/settings").json()["data"]
        assert settings["embedding"]["model_name"] == "BAAI/bge-m3"

        runtime = client.app.state.runtime
        _ = runtime.reranker  # 先构建单例
        assert runtime._reranker is not None
        resp = client.put(
            "/api/settings",
            json={"reranker": {"enabled": True, "model_name": "BAAI/bge-reranker-v2-m3", "top_n": 10}},
        )
        assert resp.json()["code"] == 0
        # 热更新：reranker 单例已失效，且配置已持久化
        assert runtime._reranker is None
        assert client.get("/api/settings").json()["data"]["reranker"]["enabled"] is True

    def test_put_rejects_unknown_key(self, client):
        resp = client.put("/api/settings", json={"nope": {}})
        assert resp.json()["code"] == 1001

    def test_partial_group_update_keeps_other_keys(self, client):
        """PUT /settings 按分组部分更新：同组未提交的键取默认值而非丢失。

        回归：update 曾是整体替换，同一份持久化数据在重启前后生效配置不一致。
        """
        resp = client.put("/api/settings", json={"app": {"max_file_size_mb": 123}})
        assert resp.json()["code"] == 0
        app_cfg = client.get("/api/settings").json()["data"]["app"]
        assert app_cfg["max_file_size_mb"] == 123
        assert app_cfg["cache_size_limit_gb"] == 2.0

    def test_partial_group_update_persists_merged_value(self, client):
        """持久化的应是合并后的完整分组，否则重启后配置分叉。"""
        client.put("/api/settings", json={"reranker": {"top_n": 7}})
        runtime = client.app.state.runtime
        stored = runtime._settings["reranker"]
        assert stored["top_n"] == 7
        assert stored["enabled"] is False
        assert stored["model_name"] == "BAAI/bge-reranker-v2-m3"

    def test_reranker_min_score_default_calibrated(self, client):
        """精排最低分阈值默认 0.1（校准于 bge-reranker-v2-m3）。"""
        cfg = client.get("/api/settings").json()["data"]["reranker"]
        assert cfg["min_score"] == 0.1

    def test_reranker_explicit_zero_min_score_preserved(self, client):
        """用户显式设 0（不过滤）必须保留，不能被默认值/兜底改写。"""
        resp = client.put("/api/settings", json={"reranker": {"min_score": 0.0}})
        assert resp.json()["code"] == 0
        cfg = client.get("/api/settings").json()["data"]["reranker"]
        assert cfg["min_score"] == 0.0

    def test_stored_group_without_min_score_picks_up_default(self, client):
        """旧落库分组缺 min_score 键时，改默认值要能直接生效（deep_merge 语义）。"""
        import json as _json

        runtime = client.app.state.runtime
        # 模拟历史落库：分组里没有 min_score 键
        runtime._settings["reranker"] = {"enabled": True, "top_n": 20}
        from app.services.settings_store import deep_merge, default_settings

        merged = deep_merge(default_settings()["reranker"], runtime._settings["reranker"])
        assert merged["min_score"] == 0.1
        assert merged["enabled"] is True
        assert merged["top_n"] == 20
        assert _json.dumps(merged)  # 可序列化（落库无异常）

    def test_llm_crud(self, client):
        provider = {
            "name": "DeepSeek",
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-x",
            "model_name": "deepseek-v4-flash",
            "is_default": True,
        }
        added = client.post("/api/settings/llm", json=provider).json()["data"]
        assert added["id"]
        providers = client.get("/api/settings/llm").json()["data"]
        assert len(providers) == 1
        provider["display_name"] = "DS"
        updated = client.put(
            f"/api/settings/llm/{added['id']}", json=provider
        ).json()["data"]
        assert updated["display_name"] == "DS"
        client.delete(f"/api/settings/llm/{added['id']}")
        assert client.get("/api/settings/llm").json()["data"] == []

    def test_llm_test_connection_fail(self, client):
        # 连接关闭端口，快速失败，无需真实网络
        resp = client.post(
            "/api/settings/llm/test",
            json={
                "provider_type": "custom",
                "base_url": "http://127.0.0.1:9",
                "api_key": "x",
                "model_name": "m",
            },
        )
        assert resp.json()["code"] == 3001

    def test_models_list(self, client):
        data = client.get("/api/settings/models").json()["data"]
        # 响应结构：{items, download_source}；4 个注册模型 + Docling 模型包
        models = data["items"]
        assert len(models) == 5
        assert all(not m["downloaded"] for m in models)
        assert data["download_source"] in ("huggingface", "hf_mirror", "modelscope")
        assert any(m["kind"] == "docling" for m in models)

    def test_download_unknown_model(self, client):
        resp = client.post("/api/settings/models/download", json={"model_id": "x/y"})
        assert resp.json()["code"] == 1001

    def test_cache_endpoints(self, client):
        status = client.get("/api/settings/cache").json()["data"]
        assert "total_bytes" in status
        cleared = client.delete("/api/settings/cache?kind=all").json()["data"]
        assert cleared["cleared"] == "all"
        bad = client.delete("/api/settings/cache?kind=nope")
        assert bad.json()["code"] == 1001


# ---------------------------------------------------------------------------
# 评测
# ---------------------------------------------------------------------------


class TestEvaluation:
    def test_qaset_upload_list_delete(self, client):
        items = [{"question": "q1", "answer": "a1"}, {"question": "q2"}]
        files = [("file", ("qa.json", json.dumps(items).encode(), "application/json"))]
        created = client.post("/api/evaluation/qasets", files=files).json()["data"]
        assert created["item_count"] == 2
        assert created["source"] == "upload"
        listing = client.get("/api/evaluation/qasets").json()["data"]
        assert len(listing) == 1
        deleted = client.delete(f"/api/evaluation/qasets/{created['id']}").json()
        assert deleted["code"] == 0
        assert client.get("/api/evaluation/qasets").json()["data"] == []

    def test_qaset_upload_invalid(self, client):
        files = [("file", ("qa.json", b"[]", "application/json"))]
        assert client.post("/api/evaluation/qasets", files=files).json()["code"] == 1001

    def test_qaset_upload_bom_and_form_name(self, client):
        """回归：带 UTF-8 BOM 的文件（PS/记事本导出）应能解析；name 走 form 字段。"""
        items = [{"question": "q1", "answer": "a1"}]
        raw = b"\xef\xbb\xbf" + json.dumps(items).encode("utf-8")  # 前置 BOM
        files = [("file", ("qa.json", raw, "application/json"))]
        resp = client.post(
            "/api/evaluation/qasets", files=files, data={"name": "带BOM测试集"}
        ).json()
        assert resp["code"] == 0
        assert resp["data"]["item_count"] == 1
        assert resp["data"]["name"] == "带BOM测试集"
        client.delete(f"/api/evaluation/qasets/{resp['data']['id']}")

    def test_qaset_file_names_unique(self, client):
        """回归：qaset.id 曾是 flush 时才生成的列默认值，拼出的文件名恒为
        None.json——多个测试集互相覆盖，删除任一会连带删掉其余的数据文件。"""
        from pathlib import Path

        items_a = [{"question": f"qa-{i}"} for i in range(3)]
        items_b = [{"question": "qb-1"}]
        files_a = [
            ("file", ("a.json", json.dumps(items_a).encode(), "application/json"))
        ]
        files_b = [
            ("file", ("b.json", json.dumps(items_b).encode(), "application/json"))
        ]
        a = client.post("/api/evaluation/qasets", files=files_a).json()["data"]
        b = client.post("/api/evaluation/qasets", files=files_b).json()["data"]
        assert Path(a["file_path"]).name == f"{a['id']}.json"
        assert Path(b["file_path"]).name == f"{b['id']}.json"
        assert a["file_path"] != b["file_path"]
        # 删除 a 不得连带删掉 b 的数据文件
        assert client.delete(f"/api/evaluation/qasets/{a['id']}").json()["code"] == 0
        assert Path(b["file_path"]).exists()
        assert client.delete(f"/api/evaluation/qasets/{b['id']}").json()["code"] == 0

    def test_generate_requires_llm(self, client):
        seed(client, make_doc())
        resp = client.post(
            "/api/evaluation/qasets/generate",
            json={"document_id": "doc1", "count": 5},
        )
        assert resp.json()["code"] == 3001

    def test_experiment_create_and_delete_drops_table(self, client):
        seed(client, make_doc(), QASet(id="qs1", name="测试集", item_count=2))
        resp = client.post(
            "/api/evaluation/experiments",
            json={"qa_set_id": "qs1", "document_ids": ["doc1"]},
        )
        exp = resp.json()["data"]
        assert exp["vector_table"].startswith("exp_")
        assert exp["status"] == "pending"  # dispatch 关闭

        # 在 fake store 中放入实验表，删除实验应级联 drop
        from app.core.vector_store import VectorItem

        client.fake_store.upsert(
            [VectorItem(id="x", vector=[1.0] * 8)], table=exp["vector_table"]
        )
        assert exp["vector_table"] in client.fake_store.tables
        deleted = client.delete(f"/api/evaluation/experiments/{exp['id']}").json()
        assert deleted["code"] == 0
        assert exp["vector_table"] not in client.fake_store.tables
        assert client.get("/api/evaluation/experiments").json()["data"] == []


# ---------------------------------------------------------------------------
# 导出 / 统计 / 仪表盘
# ---------------------------------------------------------------------------


class TestExportAndStats:
    def test_export_jsonl(self, client):
        parent = make_chunk("p1", content="父块")
        child = make_chunk(
            "c1", parent_id="p1", breadcrumb=["第一章"], content="导出内容"
        )
        seed(client, make_doc(), parent, child)
        resp = client.post(
            "/api/export/jsonl",
            json={"document_ids": ["doc1"], "format": "alpaca"},
        )
        data = resp.json()["data"]
        assert data["line_count"] == 1  # 父块不导出
        assert Path(data["file_path"]).exists()

    def test_export_bad_format(self, client):
        resp = client.post(
            "/api/export/jsonl", json={"document_ids": ["d"], "format": "csv"}
        )
        assert resp.json()["code"] == 1001

    def test_export_strip_cot(self, client):
        """strip_cot=true：思维链被剥离，响应带 stripped_chunks 计数。"""
        cot = make_chunk(
            "c1",
            content="前言\n【AI 推理过程】\n思考\n【AI 回复】\n正文回复",
        )
        seed(client, make_doc(), cot)
        resp = client.post(
            "/api/export/jsonl",
            json={"document_ids": ["doc1"], "format": "alpaca", "strip_cot": True},
        )
        data = resp.json()["data"]
        assert data["line_count"] == 1
        assert data["stripped_chunks"] == 1
        text = Path(data["file_path"]).read_text(encoding="utf-8")
        assert "推理过程" not in text
        assert "正文回复" in text

    def test_export_strip_cot_default_off(self, client):
        """缺省不清洗：输出与现状一致（思维链原样保留）。"""
        cot = make_chunk(
            "c1",
            content="前言\n【AI 推理过程】\n思考\n【AI 回复】\n正文回复",
        )
        seed(client, make_doc(), cot)
        resp = client.post(
            "/api/export/jsonl", json={"document_ids": ["doc1"], "format": "alpaca"}
        )
        data = resp.json()["data"]
        assert data["stripped_chunks"] == 0
        text = Path(data["file_path"]).read_text(encoding="utf-8")
        assert "【AI 推理过程】" in text

    def test_chunk_stats_with_compare(self, client):
        snap1 = ChunkStatsSnapshot(
            document_id="doc1", version=1, chunk_count=5, mean_tokens=10.0
        )
        snap2 = ChunkStatsSnapshot(
            document_id="doc1", version=2, chunk_count=8, mean_tokens=12.0
        )
        seed(client, make_doc(), snap1, snap2)
        data = client.get("/api/documents/doc1/chunk-stats?compare=prev").json()["data"]
        assert data["current"]["version"] == 2
        assert data["previous"]["version"] == 1
        no_compare = client.get("/api/documents/doc1/chunk-stats").json()["data"]
        assert no_compare["previous"] is None

    def test_dashboard(self, client):
        seed(client, make_doc(), make_chunk("c1", is_embedded=True))
        data = client.get("/api/stats").json()["data"]
        assert data["document_count"] == 1
        assert data["chunk_count"] == 1
        assert data["embedded_count"] == 1


class TestWebSocket:
    def test_ping_pong(self, client):
        with client.websocket_connect("/ws/tasks") as ws:
            ws.send_text("ping")
            assert ws.receive_json()["type"] == "pong"


# ---------------------------------------------------------------------------
# 解析预览（blocks / parse-report / clean / parse-variant）
# ---------------------------------------------------------------------------


def _seed_parse_cache(client, file_hash: str = "hash_doc1") -> None:
    """向测试缓存目录写入原始解析缓存（标题 + 正文）。"""
    from conftest import make_heading, make_paragraph

    blocks = [
        make_heading("第一章 概述", level=1, page=1).to_dict(),
        make_paragraph("这是第一章的正文内容，足够长的文本。", page=1).to_dict(),
        make_paragraph("第二段正文内容，同样足够长。", page=2).to_dict(),
    ]
    cache_dir = Path(client.data_dir) / "cache" / "parse"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"parse_{file_hash}.json").write_text(
        json.dumps(blocks, ensure_ascii=False), encoding="utf-8"
    )


class TestParsePreview:
    def test_blocks_with_breadcrumb(self, client):
        seed(client, make_doc(status=DocStatus.PARSED.value))
        _seed_parse_cache(client)
        resp = client.get("/api/documents/doc1/blocks")
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["variant"] == "raw"
        assert len(data["blocks"]) == 3
        # breadcrumb 由 API 现挂：正文块应带章节路径
        assert data["blocks"][1]["breadcrumb"] == ["第一章 概述"]
        assert data["blocks"][0]["level"] == 1

    def test_blocks_cleaned_missing(self, client):
        seed(client, make_doc(status=DocStatus.PARSED.value))
        _seed_parse_cache(client)
        resp = client.get("/api/documents/doc1/blocks?variant=cleaned")
        assert resp.json()["code"] == 1002

    def test_blocks_no_cache(self, client):
        seed(client, make_doc(status=DocStatus.UPLOADED.value))
        resp = client.get("/api/documents/doc1/blocks")
        assert resp.json()["code"] == 1002

    def test_parse_report(self, client):
        seed(client, make_doc(status=DocStatus.PARSED.value))
        _seed_parse_cache(client)
        data = client.get("/api/documents/doc1/parse-report").json()["data"]
        assert data["total_blocks"] == 3
        assert data["type_counts"]["heading"] == 1
        assert data["type_counts"]["paragraph"] == 2
        assert data["page_count"] == 2
        assert data["has_cleaned"] is False
        assert data["est_tokens_text"] > 0
        # 有标题、无乱码、页数少：不应有 warning 级警告
        kinds = [w["kind"] for w in data["warnings"]]
        assert "garbled_text" not in kinds

    def test_clean_validation(self, client):
        seed(client, make_doc(status=DocStatus.PARSED.value))
        _seed_parse_cache(client)
        # 两档全关 → 参数错误
        resp = client.post(
            "/api/documents/doc1/clean",
            json={"text_scope": "off", "clean_table": False},
        )
        assert resp.json()["code"] == 1001
        # 未配置 LLM → 参数错误
        resp = client.post(
            "/api/documents/doc1/clean",
            json={"text_scope": "full", "clean_table": False},
        )
        assert resp.json()["code"] == 1001

    def test_clean_dispatched(self, client):
        from conftest import FakeLLMClient

        seed(client, make_doc(status=DocStatus.PARSED.value))
        _seed_parse_cache(client)
        client.app.state.runtime.set_llm_client(FakeLLMClient())
        resp = client.post(
            "/api/documents/doc1/clean",
            json={"text_scope": "full", "clean_table": True},
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["dispatched"] == "clean"

    def test_clean_bad_status(self, client):
        seed(client, make_doc(status=DocStatus.PARSING.value))
        resp = client.post(
            "/api/documents/doc1/clean",
            json={"text_scope": "full"},
        )
        assert resp.json()["code"] == 1001

    def test_parse_variant_switch(self, client):
        seed(client, make_doc(status=DocStatus.PARSED.value))
        _seed_parse_cache(client)
        # 无清洗缓存时切 cleaned → 1002
        resp = client.post(
            "/api/documents/doc1/parse-variant", json={"variant": "cleaned"}
        )
        assert resp.json()["code"] == 1002
        # 非法 variant → 1001
        resp = client.post(
            "/api/documents/doc1/parse-variant", json={"variant": "dirty"}
        )
        assert resp.json()["code"] == 1001
        # 写入清洗缓存后切换成功，文档详情 variant 更新
        cache_dir = Path(client.data_dir) / "cache" / "parse"
        (cache_dir / "parse_hash_doc1_cleaned.json").write_text(
            json.dumps(
                {"meta": {"prompt_version": "clean-v1"}, "blocks": []},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        resp = client.post(
            "/api/documents/doc1/parse-variant", json={"variant": "cleaned"}
        )
        assert resp.json()["code"] == 0
        detail = client.get("/api/documents/doc1").json()["data"]
        assert detail["parse_variant"] == "cleaned"
        # 切回 raw
        resp = client.post(
            "/api/documents/doc1/parse-variant", json={"variant": "raw"}
        )
        assert resp.json()["code"] == 0
        detail = client.get("/api/documents/doc1").json()["data"]
        assert detail["parse_variant"] == "raw"
