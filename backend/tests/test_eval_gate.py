"""评测集回归门（需运行中的后端，默认跳过）。

运行方式：
    TESSERA_API=http://127.0.0.1:8766 pytest tests/test_eval_gate.py -q

守护目标（2026-10-08 拍板 min_score 默认 0.1 时定下的口径）：
- 12 条正例全部 hit@5
- 6 条负例（全库无答案）top5 **全空** —— 依赖 rerank 开启 + min_score > 0

负例"全空"不是"查不到"，而是"候选都在精排相关性门槛之下"（threshold_dropped > 0）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

API = os.getenv("TESSERA_API", "").rstrip("/")
EVAL_PATH = Path(__file__).resolve().parents[2] / "docs" / "retrieval_eval.json"

pytestmark = pytest.mark.skipif(
    not API,
    reason="评测回归门需运行中的后端：TESSERA_API=http://127.0.0.1:8766 pytest tests/test_eval_gate.py",
)


def _search(query: str, top_k: int = 5) -> dict:
    req = urllib.request.Request(
        f"{API}/api/search",
        data=json.dumps(
            {"query": query, "top_k": top_k, "use_rerank": True}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover
        pytest.skip(f"后端不可达：{exc}")
    assert payload.get("code") == 0, payload
    return payload["data"]


@pytest.fixture(scope="module")
def eval_items() -> list[dict]:
    assert EVAL_PATH.is_file(), f"评测集不存在：{EVAL_PATH}"
    return json.loads(EVAL_PATH.read_text(encoding="utf-8"))["items"]


def _partition(items: list[dict]) -> tuple[list[dict], list[dict]]:
    pos = [i for i in items if i["meta"]["kind"] == "positive"]
    neg = [i for i in items if i["meta"]["kind"] == "negative"]
    return pos, neg


def test_eval_set_shape(eval_items):
    pos, neg = _partition(eval_items)
    assert len(pos) == 12, f"正例应 12 条，实际 {len(pos)}"
    assert len(neg) == 6, f"负例应 6 条，实际 {len(neg)}"


def test_positive_queries_hit_at_5(eval_items):
    """12 条正例全部命中期望文档（hit@5）。"""
    pos, _ = _partition(eval_items)
    misses: list[str] = []
    for item in pos:
        data = _search(item["question"], top_k=5)
        expect = item["expect_document"]
        sources = [r.get("source_file") or "" for r in data["results"]]
        if not any(s and s in expect for s in sources):
            misses.append(f"{item['question']}（期望 {expect}，实得 {sources}）")
    assert not misses, "正例未全部 hit@5：\n" + "\n".join(misses)


def test_negative_queries_return_empty(eval_items):
    """6 条负例（全库无答案）top5 全空。

    依赖 rerank 开启 + min_score > 0：候选会被精排分压到门槛之下而整体丢弃。
    若后端未启用精排（模型未下载/开关关闭），本例失败是**预期的**——
    说明负例期望的前提不成立，而非检索退化。
    """
    _, neg = _partition(eval_items)
    bad: list[str] = []
    for item in neg:
        data = _search(item["question"], top_k=5)
        if not data.get("used_rerank"):
            pytest.fail(
                "负例期望依赖 rerank 开启，请先在「设置 → Rerank」启用并下载模型"
            )
        if data["results"]:
            bad.append(
                f"{item['question']}（应为空，实得 "
                f"{[r.get('source_file') for r in data['results']]}）"
            )
        elif data.get("threshold_dropped", 0) <= 0:
            bad.append(f"{item['question']}（既无结果也无被阈值滤掉的候选，语义不符）")
    assert not bad, "负例未全部返回空：\n" + "\n".join(bad)
