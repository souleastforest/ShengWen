# ruff: noqa: E402

import unittest
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, path)

from src.main.python.sheng_wen.llm.llm import LLMConfig, LLMMessage, LLMResponseError, get_llm
from src.main.python.sheng_wen.llm.anthropic_client import AnthropicClient
from src.main.python.sheng_wen.llm.litellm_client import LiteLLMClient
from src.main.python.sheng_wen.llm.provider_manager import LLMProviderManager


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# -------------------------------------------------------
# 1. Factory routing
# -------------------------------------------------------

class TestGetLLMFactory(unittest.TestCase):

    def test_default_provider_routes_to_litellm(self):
        cfg = LLMConfig(base_url="https://api.openai.com/v1", api_key="k", model_id="gpt-4o")
        client = get_llm(cfg)
        self.assertIsInstance(client, LiteLLMClient)

    def test_openai_routes_to_litellm(self):
        cfg = LLMConfig(base_url="", api_key="k", model_id="gpt-4o", provider="openai")
        client = get_llm(cfg)
        self.assertIsInstance(client, LiteLLMClient)

    def test_deepseek_routes_to_litellm(self):
        cfg = LLMConfig(base_url="", api_key="k", model_id="deepseek-chat", provider="deepseek")
        client = get_llm(cfg)
        self.assertIsInstance(client, LiteLLMClient)

    def test_openrouter_routes_to_litellm(self):
        cfg = LLMConfig(base_url="", api_key="k", model_id="openai/gpt-4o", provider="openrouter")
        client = get_llm(cfg)
        self.assertIsInstance(client, LiteLLMClient)

    def test_anthropic_routes_to_anthropic_client(self):
        cfg = LLMConfig(base_url="https://api.anthropic.com", api_key="k", model_id="claude-sonnet-4-20250514", provider="anthropic")
        client = get_llm(cfg)
        self.assertIsInstance(client, AnthropicClient)


# -------------------------------------------------------
# 2. AnthropicClient construction
# -------------------------------------------------------

class TestAnthropicClientConstruction(unittest.TestCase):

    def test_creates_client_with_api_key_and_base_url(self):
        cfg = LLMConfig(
            base_url="https://custom.anthropic.com",
            api_key="sk-test-123",
            model_id="claude-sonnet-4-20250514",
            provider="anthropic",
        )
        client = AnthropicClient(cfg)
        self.assertEqual(client.config.api_key, "sk-test-123")
        self.assertEqual(client.config.base_url, "https://custom.anthropic.com")
        self.assertIsNotNone(client._client)

    def test_creates_client_with_extra_headers(self):
        headers = {"X-Custom": "val", "anthropic-version": "2023-06-01"}
        cfg = LLMConfig(
            base_url="https://api.anthropic.com",
            api_key="k",
            model_id="claude",
            provider="anthropic",
            extra_headers=headers,
        )
        client = AnthropicClient(cfg)
        self.assertEqual(client.config.extra_headers, headers)

    def test_creates_client_without_extra_headers(self):
        cfg = LLMConfig(base_url="", api_key="k", model_id="claude", provider="anthropic")
        client = AnthropicClient(cfg)
        self.assertIsNone(client.config.extra_headers)


# -------------------------------------------------------
# 3. AnthropicClient streaming with mocked SDK
# -------------------------------------------------------

class TestAnthropicClientStreaming(unittest.TestCase):

    def _make_client(self, extra_headers=None):
        cfg = LLMConfig(
            base_url="https://api.anthropic.com",
            api_key="k",
            model_id="claude-sonnet-4-20250514",
            provider="anthropic",
            extra_headers=extra_headers,
        )
        return AnthropicClient(cfg)

    def test_streaming_calls_resp_callback(self):
        client = self._make_client()
        messages = [
            LLMMessage(role="system", content="You are helpful."),
            LLMMessage(role="user", content="Hello"),
        ]
        received = []

        async def _test():
            with patch.object(client, "_client") as mock_sdk:
                mock_stream = AsyncMock()
                mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
                mock_stream.__aexit__ = AsyncMock(return_value=False)

                async def _text_iter():
                    for chunk in ["Hello", " world"]:
                        yield chunk
                mock_stream.text_stream = _text_iter()

                mock_sdk.messages.stream = MagicMock(return_value=mock_stream)

                await client.response(
                    messages,
                    lambda c: received.append(c),
                    stream=True,
                )

        _run_async(_test())
        self.assertEqual(received, ["Hello", " world"])

    def test_streaming_passes_extra_headers(self):
        headers = {"X-Test": "1"}
        client = self._make_client(extra_headers=headers)
        messages = [LLMMessage(role="user", content="hi")]

        async def _test():
            with patch.object(client, "_client") as mock_sdk:
                mock_stream = AsyncMock()
                mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
                mock_stream.__aexit__ = AsyncMock(return_value=False)

                async def _text_iter():
                    yield "ok"
                mock_stream.text_stream = _text_iter()

                mock_sdk.messages.stream = MagicMock(return_value=mock_stream)

                await client.response(messages, lambda c: None, stream=True)

                call_kwargs = mock_sdk.messages.stream.call_args[1]
                self.assertEqual(call_kwargs["extra_headers"], {"X-Test": "1"})

        _run_async(_test())

    def test_streaming_strips_anthropic_prefix(self):
        cfg = LLMConfig(
            base_url="https://api.anthropic.com",
            api_key="k",
            model_id="anthropic/claude-sonnet-4-20250514",
            provider="anthropic",
        )
        client = AnthropicClient(cfg)
        messages = [LLMMessage(role="user", content="hi")]

        async def _test():
            with patch.object(client, "_client") as mock_sdk:
                mock_stream = AsyncMock()
                mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
                mock_stream.__aexit__ = AsyncMock(return_value=False)

                async def _text_iter():
                    yield "ok"
                mock_stream.text_stream = _text_iter()

                mock_sdk.messages.stream = MagicMock(return_value=mock_stream)

                await client.response(messages, lambda c: None, stream=True)

                call_kwargs = mock_sdk.messages.stream.call_args[1]
                self.assertEqual(call_kwargs["model"], "claude-sonnet-4-20250514")

        _run_async(_test())

    def test_streaming_separates_system_message(self):
        client = self._make_client()
        messages = [
            LLMMessage(role="system", content="Be concise."),
            LLMMessage(role="user", content="Hello"),
        ]

        async def _test():
            with patch.object(client, "_client") as mock_sdk:
                mock_stream = AsyncMock()
                mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
                mock_stream.__aexit__ = AsyncMock(return_value=False)

                async def _text_iter():
                    yield "ok"
                mock_stream.text_stream = _text_iter()

                mock_sdk.messages.stream = MagicMock(return_value=mock_stream)

                await client.response(messages, lambda c: None, stream=True)

                call_kwargs = mock_sdk.messages.stream.call_args[1]
                self.assertEqual(call_kwargs["system"], "Be concise.")
                self.assertEqual(call_kwargs["messages"], [{"role": "user", "content": "Hello"}])

        _run_async(_test())

    def test_empty_messages_returns_error(self):
        client = self._make_client()
        messages = [LLMMessage(role="system", content="system only")]

        received = []

        async def _test():
            await client.response(messages, lambda c: received.append(c), stream=True)

        _run_async(_test())
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], LLMResponseError)

    def test_sdk_error_returns_llm_response_error(self):
        client = self._make_client()
        messages = [LLMMessage(role="user", content="hi")]

        received = []

        async def _test():
            with patch.object(client, "_client") as mock_sdk:
                mock_sdk.messages.stream.side_effect = Exception("API error")
                await client.response(messages, lambda c: received.append(c), stream=True)

        _run_async(_test())
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], LLMResponseError)
        self.assertIn("API error", str(received[0]))


# -------------------------------------------------------
# 4. ProviderManager integration
# -------------------------------------------------------

class TestAnthropicProviderManager(unittest.TestCase):

    def test_anthropic_in_provider_list(self):
        cfg = LLMConfig(base_url="", api_key="k", model_id="m")
        mgr = LLMProviderManager(cfg)
        providers = mgr.list_providers()
        ids = [p["id"] for p in providers]
        self.assertIn("anthropic", ids)

    def test_anthropic_provider_has_correct_defaults(self):
        cfg = LLMConfig(base_url="", api_key="k", model_id="m")
        mgr = LLMProviderManager(cfg)
        providers = mgr.list_providers()
        anthropic = next(p for p in providers if p["id"] == "anthropic")
        self.assertEqual(anthropic["default_base_url"], "https://api.anthropic.com")
        self.assertIn("claude", anthropic["default_model_id"])

    def test_update_settings_with_extra_headers(self):
        cfg = LLMConfig(base_url="", api_key="k", model_id="m")
        mgr = LLMProviderManager(cfg)
        headers = {"X-Custom": "val"}
        result = mgr.update_settings(
            provider="anthropic",
            base_url="https://api.anthropic.com",
            model_id="claude-sonnet-4-20250514",
            extra_headers=headers,
        )
        self.assertEqual(result["extra_headers"], {"X-Custom": "val"})
        self.assertEqual(result["provider"], "anthropic")

    def test_update_settings_preserves_extra_headers(self):
        cfg = LLMConfig(base_url="", api_key="k", model_id="m", extra_headers={"X-Keep": "yes"})
        mgr = LLMProviderManager(cfg)
        result = mgr.update_settings(provider="anthropic")
        self.assertEqual(result["extra_headers"], {"X-Keep": "yes"})

    def test_get_runtime_config_includes_extra_headers(self):
        headers = {"X-Test": "1"}
        cfg = LLMConfig(base_url="", api_key="k", model_id="m", extra_headers=headers)
        mgr = LLMProviderManager(cfg)
        runtime = mgr.get_runtime_config()
        self.assertEqual(runtime.extra_headers, headers)

    def test_infer_anthropic_from_url(self):
        cfg = LLMConfig(base_url="https://api.anthropic.com", api_key="k", model_id="claude")
        mgr = LLMProviderManager(cfg)
        settings = mgr.get_settings()
        self.assertEqual(settings["provider"], "anthropic")

    def test_export_runtime_config_includes_extra_headers(self):
        cfg = LLMConfig(base_url="", api_key="k", model_id="m", extra_headers={"X": "1"})
        mgr = LLMProviderManager(cfg)
        exported = mgr.export_runtime_config()
        self.assertEqual(exported["extra_headers"], {"X": "1"})


if __name__ == '__main__':
    unittest.main()
