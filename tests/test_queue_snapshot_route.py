"""Tests for GET /tasks/queue response shape and api.get_queue_snapshots()."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import src.main.python.sheng_wen.api as api_module
from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router

FIXED_QUEUE_NAMES = [
    "VideoDownloaderWorker",
    "FileUploadWorker",
    "TranscriberWorker",
    "LLMWorker",
]


@pytest.fixture
def app_with_router():
    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    app.include_router(tasks_router)
    return app


@pytest.fixture
def no_workers(monkeypatch):
    """确保 4 个懒加载 worker 均为未实例化状态。"""
    for attr in (
        "downloader_worker",
        "file_upload_worker",
        "transcriber_worker",
        "llm_worker",
    ):
        monkeypatch.setattr(api_module, attr, None)


@pytest.mark.asyncio
async def test_queue_endpoint_response_shape(app_with_router, monkeypatch):
    """GET /tasks/queue 返回 {queues: [...], timestamp} 且 4 个队列名固定。"""

    async def fake_snapshots():
        return [
            {
                "name": name,
                "active_task_id": None,
                "waiting_task_ids": [],
                "queue_size": 0,
            }
            for name in FIXED_QUEUE_NAMES
        ]

    monkeypatch.setattr(api_module, "get_queue_snapshots", fake_snapshots)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_router), base_url="http://testserver"
    ) as client:
        response = await client.get("/tasks/queue")

    assert response.status_code == 200
    body = response.json()
    assert len(body["queues"]) == 4
    assert [q["name"] for q in body["queues"]] == FIXED_QUEUE_NAMES
    for q in body["queues"]:
        assert set(q.keys()) == {
            "name",
            "active_task_id",
            "waiting_task_ids",
            "queue_size",
        }
        assert q["waiting_task_ids"] == []
        assert q["queue_size"] == 0
    assert "timestamp" in body
    assert body["timestamp"].endswith("Z")


@pytest.mark.asyncio
async def test_queue_route_not_confused_with_task_id(app_with_router, monkeypatch):
    """/tasks/queue 不能被 /{task_id} 路由吞掉（应命中 /queue 而非返回 404）。"""

    async def fake_snapshots():
        return []

    monkeypatch.setattr(api_module, "get_queue_snapshots", fake_snapshots)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_router), base_url="http://testserver"
    ) as client:
        response = await client.get("/tasks/queue")

    assert response.status_code == 200
    assert response.json()["queues"] == []


@pytest.mark.asyncio
async def test_get_queue_snapshots_all_empty_without_workers(no_workers):
    """未实例化 worker 时返回 4 个全空快照。"""
    snapshots = await api_module.get_queue_snapshots()

    assert len(snapshots) == 4
    for name, snap in zip(FIXED_QUEUE_NAMES, snapshots):
        assert snap["name"] == name
        assert snap["active_task_id"] is None
        assert snap["waiting_task_ids"] == []
        assert snap["queue_size"] == 0


class _FakeWorker:
    def __init__(self, name, active=None, waiting=None):
        self.name = name
        self._active = active
        self._waiting = waiting

    def snapshot(self):
        return {
            "name": self.name,
            "active_task_id": self._active,
            "waiting_task_ids": self._waiting,
            "queue_size": len(self._waiting),
        }


class _RaisingWorker:
    name = "TranscriberWorker"

    def snapshot(self):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_get_queue_snapshots_uses_worker_snapshot(monkeypatch):
    """已实例化 worker 返回其真实快照，队列名以固定名为准。"""
    monkeypatch.setattr(api_module, "downloader_worker", None)
    monkeypatch.setattr(api_module, "file_upload_worker", None)
    monkeypatch.setattr(
        api_module,
        "transcriber_worker",
        _FakeWorker("TranscriberWorker", active="t9", waiting=["t1", "t2"]),
    )
    monkeypatch.setattr(api_module, "llm_worker", None)

    snapshots = await api_module.get_queue_snapshots()

    transcriber_snap = next(s for s in snapshots if s["name"] == "TranscriberWorker")
    assert transcriber_snap["active_task_id"] == "t9"
    assert transcriber_snap["waiting_task_ids"] == ["t1", "t2"]
    assert transcriber_snap["queue_size"] == 2


@pytest.mark.asyncio
async def test_get_queue_snapshots_falls_back_to_empty_on_error(monkeypatch):
    """worker.snapshot() 抛异常时该队列返回全空快照，不影响其他队列。"""
    monkeypatch.setattr(api_module, "downloader_worker", None)
    monkeypatch.setattr(api_module, "file_upload_worker", None)
    monkeypatch.setattr(api_module, "transcriber_worker", _RaisingWorker())
    monkeypatch.setattr(api_module, "llm_worker", None)

    snapshots = await api_module.get_queue_snapshots()

    transcriber_snap = next(s for s in snapshots if s["name"] == "TranscriberWorker")
    assert transcriber_snap["active_task_id"] is None
    assert transcriber_snap["waiting_task_ids"] == []
    assert transcriber_snap["queue_size"] == 0
