from __future__ import annotations

import asyncio
from typing import Any, Callable, List, Union

from .llm import LLM, LLMConfig, LLMMessage, LLMResponseError


class OpenAiResponsesClient(LLM):
    """
    使用 OpenAI Responses API（/v1/responses）的 LLM 客户端。

    兼容支持 Responses API 的 OpenAI 兼容端点（如 DeepSeek）。
    消息协议：system 消息放入 input 数组（Responses API 支持 system role），
    其余消息原样透传。
    """

    def __init__(self, config: LLMConfig, **kwargs):
        super().__init__(config, **kwargs)
        self._client = self._create_client(config)

    @staticmethod
    def _create_client(config: LLMConfig) -> Any:
        from openai import AsyncOpenAI

        client_kw: dict[str, Any] = {
            "api_key": config.api_key or "sk-placeholder",
        }
        if config.base_url:
            client_kw["base_url"] = config.base_url
        extra_headers = config.extra_headers
        if extra_headers:
            client_kw["default_headers"] = extra_headers
        return AsyncOpenAI(**client_kw)

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
        # LLMMessage 列表 → Responses API input 数组
        input_items: list[dict[str, Any]] = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]
        has_non_empty_user = any(
            msg.role == "user" and str(msg.content or "").strip()
            for msg in messages
        )
        if not input_items or not has_non_empty_user:
            resp_callback(LLMResponseError("OpenAI Responses 请求内容为空，已跳过请求。"))
            return

        model_id = self.config.model_id or "deepseek-v4-flash"
        kwargs: dict[str, Any] = {
            "model": model_id,
            "input": input_items,
            "timeout": timeout,
        }
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature

        try:
            if stream:
                async with self._client.responses.stream(**kwargs) as stream_obj:
                    async for event in stream_obj:
                        if event.type == "response.output_text.delta":
                            delta = getattr(event, "delta", "")
                            if delta:
                                resp_callback(delta)
            else:
                response = await self._client.responses.create(**kwargs)
                output_text = getattr(response, "output_text", "") or ""
                if output_text:
                    resp_callback(output_text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            resp_callback(LLMResponseError(f"OpenAI Responses 请求失败: {e}"))
