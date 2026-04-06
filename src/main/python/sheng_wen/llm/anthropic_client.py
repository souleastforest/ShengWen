from __future__ import annotations

import asyncio
from typing import Any, Callable, List, Union

from .llm import LLM, LLMConfig, LLMMessage, LLMResponseError


class AnthropicClient(LLM):
    """使用 Anthropic Python SDK 的 LLM 客户端。"""

    def __init__(self, config: LLMConfig, **kwargs):
        super().__init__(config, **kwargs)
        self._client = self._create_client(config)

    @staticmethod
    def _create_client(config: LLMConfig) -> Any:
        from anthropic import AsyncAnthropic

        client_kw: dict[str, Any] = {}
        if config.api_key:
            client_kw["api_key"] = config.api_key
        if config.base_url:
            client_kw["base_url"] = config.base_url
        extra_headers = config.extra_headers
        if extra_headers:
            client_kw["default_headers"] = extra_headers
        return AsyncAnthropic(**client_kw)

    def update_runtime_config(self, config: LLMConfig):
        """运行时配置更新，重建 SDK 客户端。"""
        self.config = config
        self._client = self._create_client(config)

    async def response(
        self,
        messages: List[LLMMessage],
        resp_callback: Callable[[Union[str, LLMResponseError]], None],
        stream: bool = True,
        timeout: int = 60,
    ):
        # 将 LLMMessage 列表拆分为 system + anthropic_messages
        system_text: str | None = None
        anthropic_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                system_text = msg.content
            else:
                anthropic_messages.append({"role": msg.role, "content": msg.content})

        # Anthropic 要求至少一条 user 消息
        if not anthropic_messages:
            resp_callback(LLMResponseError("Anthropic 要求至少一条 user 消息。"))
            return

        model_id = self.config.model_id or "claude-sonnet-4-20250514"
        # Strip common provider prefix if present
        if model_id.startswith("anthropic/"):
            model_id = model_id[len("anthropic/"):]

        kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": anthropic_messages,
            "max_tokens": 8192,
            "timeout": timeout,
        }
        if system_text:
            kwargs["system"] = system_text
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        extra_headers = self.config.extra_headers
        if extra_headers:
            kwargs["extra_headers"] = extra_headers

        try:
            if stream:
                async with self._client.messages.stream(**kwargs) as stream_obj:
                    async for text in stream_obj.text_stream:
                        resp_callback(text)
            else:
                response = await self._client.messages.create(**kwargs)
                for block in response.content:
                    if block.type == "text" and block.text:
                        resp_callback(block.text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            resp_callback(LLMResponseError(f"Anthropic 请求失败: {e}"))
