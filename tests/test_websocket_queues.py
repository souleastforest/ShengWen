"""Tests: WS task_update 消息扩展包含 queues 字段（向后兼容）。"""

import json
from datetime import datetime, timezone

import pytest

import src.main.python.sheng_wen.api as api_module
from src.main.python.sheng_wen.infra.api.routes import websocket as ws

SAMPLE_TASK = {
    "id": "t1",
    "video_url": "https://example.com/a.mp4",
    "status": "PENDING",
    "created_at": datetime.now(timezone.utc),
    "latest_modified_at": datetime.now(timezone.utc),
    "progress": 0.0,
    "title": None,
    "author_name": None,
    "author_url": None,
    "summary_mode": "auto",
}


@pytest.fixture
def captured_broadcast(monkeypatch):
    captured = []

    async def fake_broadcast(message: str):
        captured.append(message)

    monkeypatch.setattr(ws.manager, "broadcast", fake_broadcast)
    return captured


@pytest.mark.asyncio
async def test_notify_task_update_includes_queues(captured_broadcast, monkeypatch):
    """notify_task_update 广播消息应包含 queues 字段。"""

    async def fake_snapshots():
        return [
            {
                "name": "LLMWorker",
                "active_task_id": "t1",
                "waiting_task_ids": [],
                "queue_size": 0,
            }
        ]

    monkeypatch.setattr(api_module, "get_queue_snapshots", fake_snapshots)

    await ws.notify_task_update("t1", task_data=dict(SAMPLE_TASK))

    assert len(captured_broadcast) == 1
    message = json.loads(captured_broadcast[0])
    assert message["type"] == "task_update"
    assert message["task"]["id"] == "t1"
    assert message["queues"] == [
        {
            "name": "LLMWorker",
            "active_task_id": "t1",
            "waiting_task_ids": [],
            "queue_size": 0,
        }
    ]


@pytest.mark.asyncio
async def test_notify_task_update_queues_fallback_empty_on_error(
    captured_broadcast, monkeypatch
):
    """get_queue_snapshots 异常时 queues 回退为空列表，消息仍正常广播。"""

    async def raising_snapshots():
        raise RuntimeError("boom")

    monkeypatch.setattr(api_module, "get_queue_snapshots", raising_snapshots)

    await ws.notify_task_update("t1", task_data=dict(SAMPLE_TASK))

    assert len(captured_broadcast) == 1
    message = json.loads(captured_broadcast[0])
    assert message["type"] == "task_update"
    assert message["task"]["id"] == "t1"
    assert message["queues"] == []
