import asyncio

from src.main.python.sheng_wen.llm.anthropic_client import AnthropicClient
from src.main.python.sheng_wen.llm.llm import LLMConfig, LLMMessage, LLMResponseError
from src.main.python.sheng_wen.llm.llm_worker import LLMWorker


class _FakeMessages:
    def __init__(self):
        self.called = False

    async def create(self, **_kwargs):
        self.called = True
        raise AssertionError("empty prompt must not reach the provider")

    def stream(self, **_kwargs):
        self.called = True
        raise AssertionError("empty prompt must not reach the provider")


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_anthropic_client_rejects_empty_user_before_network_call():
    client = AnthropicClient(
        LLMConfig(
            base_url="https://example.invalid",
            api_key="test",
            model_id="glm-5.2",
            provider="anthropic",
        )
    )
    fake = _FakeClient()
    client._client = fake

    received = []
    asyncio.run(
        client.response(
            [
                LLMMessage(role="system", content="system"),
                LLMMessage(role="user", content="  "),
            ],
            received.append,
            stream=False,
        )
    )

    assert len(received) == 1
    assert isinstance(received[0], LLMResponseError)
    assert "内容为空" in str(received[0])
    assert not fake.messages.called


def test_llm_worker_marks_empty_multipart_part_failed_without_parent_failure(
    tmp_path, monkeypatch
):
    transcript_path = tmp_path / "empty-part.txt"
    output_path = tmp_path / "summary-part.md"
    transcript_path.write_text("", encoding="utf-8")
    updates = []

    def record_update(task_id, part_index, values):
        updates.append((task_id, part_index, values))

    import src.main.python.sheng_wen.task_parts as task_parts_module

    monkeypatch.setattr(task_parts_module, "update_task_part", record_update)

    fake = type("FakeLLM", (), {"called": False})()
    worker = LLMWorker("test", fake)
    monkeypatch.setattr(worker, "is_task_cancelled", lambda _task_id: False)
    asyncio.run(
        worker.process_task(
            {
                "task_id": "task-1",
                "multipart_part": {"index": 2},
                "intermediate_file_path": str(transcript_path),
                "output_file": str(output_path),
            }
        )
    )

    assert len(updates) == 1
    assert updates[0][0:2] == ("task-1", 2)
    assert updates[0][2]["status"] == "FAILED"
    assert "转录文本为空" in updates[0][2]["error_message"]
    assert not output_path.exists()


def test_llm_worker_skips_empty_transcript(tmp_path):
    transcript_path = tmp_path / "empty.txt"
    output_path = tmp_path / "summary.md"
    transcript_path.write_text("  \n", encoding="utf-8")

    class FakeLLM:
        called = False

        async def response(self, **_kwargs):
            self.called = True

    fake = FakeLLM()
    worker = LLMWorker("test", fake)

    asyncio.run(
        worker.process_task(
            {
                "intermediate_file_path": str(transcript_path),
                "output_file": str(output_path),
            }
        )
    )

    assert fake.called is False
    assert not output_path.exists()
