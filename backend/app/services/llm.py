"""OpenAI 兼容 LLM 客户端（DeepSeek / 自定义端点）。

httpx 懒导入，未安装时实例化不报错、调用时才抛错；
失败重试 1 次（指数退避）。
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0
_MAX_RETRIES = 2  # 首次 + 重试 1 次


class LLMError(Exception):
    """LLM 调用失败。"""


class LLMClient:
    """OpenAI Chat Completions 兼容客户端。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        # 约定：base_url 不以 / 结尾，/chat/completions 由客户端补充
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """发起一次 chat 请求，返回 assistant 文本。

        Raises:
            LLMError: 重试后仍失败。
        """
        text, _usage = self.chat_with_usage(messages, **kwargs)
        return text

    def chat_with_usage(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> tuple[str, dict[str, int]]:
        """发起一次 chat 请求，返回 (assistant 文本, token usage)。

        usage 取自响应 usage 字段（prompt_tokens/completion_tokens/total_tokens），
        端点不返回时为空 dict。供计费敏感场景（LLM 清洗）记录真实消耗。
        """
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": kwargs.pop("temperature", 0.0),
        }
        payload.update(kwargs)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                data = self._request_raw(payload)
                return self._extract_text(data), self._extract_usage(data)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    backoff = 2**attempt
                    logger.warning(
                        "LLM 调用失败（第 %d 次），%ds 后重试：%s",
                        attempt + 1,
                        backoff,
                        exc,
                    )
                    time.sleep(backoff)
        raise LLMError(f"LLM 调用失败：{last_error}") from last_error

    def test_connection(self) -> bool:
        """连通性测试（会消耗少量 token）。"""
        try:
            self.chat(
                [{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except LLMError:
            return False

    def _request(self, payload: dict[str, Any]) -> str:
        return self._extract_text(self._request_raw(payload))

    def _request_raw(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:
            raise LLMError("httpx 未安装") from exc

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM 响应格式异常：{data}") from exc

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> dict[str, int]:
        usage = data.get("usage") or {}
        return {
            key: int(usage[key])
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(usage.get(key), (int, float))
        }


def create_llm_client(provider_config: dict[str, Any]) -> LLMClient:
    """按 LLMProvider 配置创建客户端。"""
    return LLMClient(
        base_url=provider_config.get("base_url", "https://api.deepseek.com"),
        api_key=provider_config.get("api_key", ""),
        model_name=provider_config.get("model_name", "deepseek-v4-flash"),
    )
