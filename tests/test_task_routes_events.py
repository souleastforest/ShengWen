"""Test: task creation routes use event bus publish instead of direct worker.add_task().

TDD RED phase — these tests should FAIL until tasks.py create_task route
publishes TASK_CREATED events instead of calling worker.add_task() directly.
"""
import pytest
import httpx
from unittest.mock import AsyncMock

from fastapi import FastAPI

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router


@pytest.fixture
def app_with_bus():
    app = FastAPI()
    bus = AsyncioEventBus()
    app.state.event_bus = bus
    app.include_router(tasks_router)
    return app, bus


@pytest.mark.asyncio
async def test_create_task_publishes_task_created(app_with_bus):
    """POST /tasks/ should publish TASK_CREATED event."""
    app, bus = app_with_bus

    published = []

    async def capture(payload):
        published.append(payload)

    bus.subscribe(TASK_CREATED, capture)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/tasks/",
            json={
                "video_url": "https://www.bilibili.com/video/BV1test",
                "quality": "best",
                "summary_mode": "auto",
            },
        )

    assert resp.status_code == 201
    assert len(published) == 1
    payload = published[0]
    assert "task_id" in payload
    assert payload["video_url"] == "https://www.bilibili.com/video/BV1test"


@pytest.mark.asyncio
async def test_create_task_does_not_call_worker_directly(app_with_bus):
    """POST /tasks/ should NOT call worker.add_task() directly."""
    app, bus = app_with_bus

    mock_factory = AsyncMock()
    app.state.get_downloader_worker = mock_factory

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/tasks/",
            json={
                "video_url": "https://example.com/video.mp4",
                "quality": "best",
            },
        )

    assert resp.status_code == 201
    mock_factory.assert_not_called()
