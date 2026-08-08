import asyncio

import pytest

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.events.topics import (
    TASK_COMPLETED,
    TASK_CREATED,
    TASK_FAILED,
)
from src.main.python.sheng_wen.application.pipeline import Pipeline


class CompletingWorker:
    def __init__(self, event_bus: AsyncioEventBus) -> None:
        self._bus = event_bus
        self.received_payloads: list[dict] = []

    async def add_task(self, payload: dict) -> None:
        self.received_payloads.append(payload)
        await self._bus.publish(
            TASK_COMPLETED,
            {
                "task_id": payload["task_id"],
                "status": "completed",
            },
        )


@pytest.fixture
def bus():
    return AsyncioEventBus()


@pytest.fixture
def pipeline(bus):
    return Pipeline(event_bus=bus)


@pytest.mark.asyncio
async def test_pipeline_dispatches_created_task_and_completion_event(bus, pipeline):
    worker = CompletingWorker(bus)
    completed_events = []
    completion_seen = asyncio.Event()

    async def capture_completed(payload):
        completed_events.append(payload)
        completion_seen.set()

    async def worker_factory():
        return worker

    bus.subscribe(TASK_COMPLETED, capture_completed)
    pipeline.register_worker_factory("get_downloader_worker", worker_factory)

    await pipeline.start()
    await bus.publish(
        TASK_CREATED,
        {
            "task_id": "task-1",
            "video_url": "https://example.com/video.mp4",
        },
    )
    await asyncio.wait_for(completion_seen.wait(), timeout=1)

    assert worker.received_payloads == [
        {
            "task_id": "task-1",
            "video_url": "https://example.com/video.mp4",
        }
    ]
    assert completed_events == [{"task_id": "task-1", "status": "completed"}]


@pytest.mark.asyncio
async def test_pipeline_ignores_created_task_when_factory_not_registered(bus, pipeline):
    completed_events = []
    failed_events = []

    async def capture_completed(payload):
        completed_events.append(payload)

    async def capture_failed(payload):
        failed_events.append(payload)

    bus.subscribe(TASK_COMPLETED, capture_completed)
    bus.subscribe(TASK_FAILED, capture_failed)

    await pipeline.start()
    await bus.publish(
        TASK_CREATED,
        {
            "task_id": "task-2",
            "video_url": "file:///tmp/input.mp3",
            "file_path": "/tmp/input.mp3",
        },
    )

    assert completed_events == []
    assert failed_events == []
    assert bus.consume_dead_letters() == []
