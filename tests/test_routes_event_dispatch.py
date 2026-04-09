"""Test: route handlers use event bus publish instead of direct worker.add_task().

TDD RED phase — these tests should FAIL until routes are refactored
to publish TASK_CREATED events instead of calling worker.add_task() directly.
"""
import pytest
import httpx
from unittest.mock import AsyncMock

from fastapi import FastAPI

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.infra.api.routes.upload import router as upload_router


@pytest.fixture
def app_with_bus():
    """Create a FastAPI app with upload router and an event bus on state."""
    app = FastAPI()
    bus = AsyncioEventBus()
    app.state.event_bus = bus
    app.include_router(upload_router)
    return app, bus


@pytest.mark.asyncio
async def test_upload_publishes_task_created_event(app_with_bus):
    """Upload route should publish TASK_CREATED event instead of calling worker.add_task()."""
    app, bus = app_with_bus

    published = []
    async def capture(payload):
        published.append(payload)

    bus.subscribe(TASK_CREATED, capture)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Upload a small test file
        files = {"file": ("test.mp3", b"fake audio content", "audio/mpeg")}
        resp = await client.post("/upload", files=files)

    assert resp.status_code == 201
    assert len(published) == 1
    payload = published[0]
    assert payload.get("task_id") is not None
    assert payload.get("file_path") is not None or payload.get("video_url", "").startswith("file://")


@pytest.mark.asyncio
async def test_upload_does_not_call_worker_directly(app_with_bus):
    """Upload route should NOT call worker.add_task() directly."""
    app, bus = app_with_bus

    # Mock the worker factory so we can assert it's NOT called
    mock_factory = AsyncMock()
    app.state.get_file_upload_worker = mock_factory

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("test.mp3", b"fake audio content", "audio/mpeg")}
        resp = await client.post("/upload", files=files)

    assert resp.status_code == 201
    # The worker factory should NOT have been called directly from the route
    # (Pipeline handles it via event bus)
    mock_factory.assert_not_called()
