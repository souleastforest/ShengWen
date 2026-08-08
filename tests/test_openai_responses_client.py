"""OpenAiResponsesClient 单测（mock openai SDK）。"""

import pytest

from src.main.python.sheng_wen.llm.llm import LLMConfig, LLMMessage, LLMResponseError
from src.main.python.sheng_wen.llm.openai_responses_client import OpenAiResponsesClient


def _config(**overrides) -> LLMConfig:
    base = dict(
        provider="openai_responses",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        model_id="deepseek-v4-flash",
        temperature=1.0,
    )
    base.update(overrides)
    return LLMConfig(**base)


def _make_messages() -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="你是总结助手"),
        LLMMessage(role="user", content="总结这段内容"),
    ]


class FakeResponses:
    def __init__(self, chunks=None, text=""):
        self._chunks = chunks
        self._text = text
        self.created_calls = 0
        self.streamed = False

    async def create(self, **kwargs):
        self.created_calls += 1
        self.last_kwargs = kwargs

        class FakeResponse:
            output_text = self._text

        return FakeResponse()

    def stream(self, **kwargs):
        self.streamed = True
        self.last_kwargs = kwargs

        class FakeStream:
            def __init__(self):
                self._events = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._events:
                    raise StopAsyncIteration
                return self._events.pop(0)

        s = FakeStream()
        s._events = self._chunks
        return s


class FakeClient:
    def __init__(self, responses: FakeResponses):
        self.responses = responses


@pytest.mark.asyncio
async def test_non_stream_sends_messages_and_calls_back(monkeypatch):
    fake = FakeResponses(text="总结内容输出")
    client = FakeClient(fake)

    def _fake_create_client(config):
        return client

    monkeypatch.setattr(OpenAiResponsesClient, "_create_client", staticmethod(_fake_create_client))

    llm = OpenAiResponsesClient(_config())
    received = []
    await llm.response(_make_messages(), received.append, stream=False)

    assert received == ["总结内容输出"]
    assert fake.created_calls == 1
    assert fake.last_kwargs["model"] == "deepseek-v4-flash"
    assert fake.last_kwargs["temperature"] == 1.0
    input_items = fake.last_kwargs["input"]
    assert input_items[0] == {"role": "system", "content": "你是总结助手"}
    assert input_items[1] == {"role": "user", "content": "总结这段内容"}


@pytest.mark.asyncio
async def test_stream_calls_back_per_delta(monkeypatch):
    class Ev:
        type = "response.output_text.delta"
        delta = "块"

    fake = FakeResponses(chunks=[Ev(), Ev(), Ev()])
    client = FakeClient(fake)

    def _fake_create_client(config):
        return client

    monkeypatch.setattr(OpenAiResponsesClient, "_create_client", staticmethod(_fake_create_client))

    llm = OpenAiResponsesClient(_config())
    received = []
    await llm.response(_make_messages(), received.append, stream=True)

    assert received == ["块", "块", "块"]
    assert fake.streamed is True


@pytest.mark.asyncio
async def test_empty_user_message_rejected(monkeypatch):
    """无有效 user 消息时不发起请求，直接回调错误。"""
    client = FakeClient(FakeResponses(text="x"))

    def _fake_create_client(config):
        return client

    monkeypatch.setattr(OpenAiResponsesClient, "_create_client", staticmethod(_fake_create_client))

    llm = OpenAiResponsesClient(_config())
    received = []
    await llm.response([LLMMessage(role="user", content="   ")], received.append)

    assert isinstance(received[0], LLMResponseError)
    assert client.responses.created_calls == 0


@pytest.mark.asyncio
async def test_api_error_mapped_to_llm_response_error(monkeypatch):
    class BoomClient:
        class Responses:
            def create(self, **kwargs):
                raise Exception("401 invalid token")

        responses = Responses()

    client = BoomClient()

    def _fake_create_client(config):
        return client

    monkeypatch.setattr(OpenAiResponsesClient, "_create_client", staticmethod(_fake_create_client))

    llm = OpenAiResponsesClient(_config())
    received = []
    await llm.response(_make_messages(), received.append, stream=False)

    assert isinstance(received[0], LLMResponseError)
    assert "OpenAI Responses 请求失败" in str(received[0])
