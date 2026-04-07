import pytest
from unittest.mock import AsyncMock

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.pipeline import Pipeline


@pytest.fixture
def bus():
    return AsyncioEventBus()


@pytest.fixture
def pipeline(bus):
    return Pipeline(event_bus=bus)


@pytest.mark.asyncio
async def test_pipeline_subscribes_on_start(pipeline, bus):
    await pipeline.start()
    # Verify subscription by publishing an event
    await bus.publish(
        "task.created", {"task_id": "t1", "video_url": "https://example.com/v.mp4"}
    )
    # Should not raise


@pytest.mark.asyncio
async def test_pipeline_dispatches_downloader_worker(pipeline, bus):
    mock_worker = AsyncMock()
    mock_factory = AsyncMock(return_value=mock_worker)
    pipeline.register_worker_factory("get_downloader_worker", mock_factory)

    await pipeline.start()
    await bus.publish(
        "task.created",
        {
            "task_id": "t1",
            "video_url": "https://example.com/v.mp4",
        },
    )

    mock_factory.assert_called_once()
    mock_worker.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_dispatches_upload_worker_for_file(pipeline, bus):
    mock_worker = AsyncMock()
    mock_factory = AsyncMock(return_value=mock_worker)
    pipeline.register_worker_factory("get_file_upload_worker", mock_factory)

    await pipeline.start()
    await bus.publish(
        "task.created",
        {
            "task_id": "t2",
            "video_url": "file:///tmp/audio.mp3",
            "file_path": "/tmp/audio.mp3",
        },
    )

    mock_factory.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_publishes_failed_on_error(pipeline, bus):
    failing_factory = AsyncMock(side_effect=RuntimeError("worker init failed"))
    pipeline.register_worker_factory("get_downloader_worker", failing_factory)

    # Also subscribe to TASK_FAILED to verify (must be async for EventBus)
    failed_events = []

    async def capture_failed(payload):
        failed_events.append(payload)

    bus.subscribe("task.failed", capture_failed)

    await pipeline.start()
    await bus.publish(
        "task.created",
        {
            "task_id": "t3",
            "video_url": "https://example.com/v.mp4",
        },
    )

    assert len(failed_events) == 1
    assert failed_events[0]["task_id"] == "t3"


@pytest.mark.asyncio
async def test_pipeline_stop_unsubscribes(pipeline, bus):
    await pipeline.start()
    await pipeline.stop()
    # After stop, publishing should not trigger any handler
    # (no error expected since no subscribers)
    await bus.publish("task.created", {"task_id": "t4", "video_url": "x"})
