"""Tests for Worker.snapshot() — queue snapshot for GET /tasks/queue & WS broadcast."""

import asyncio

import pytest

from src.main.python.sheng_wen.worker import Worker


class DummyWorker(Worker):
    def process_task(self, payload):
        pass


def test_snapshot_empty_queue():
    worker = DummyWorker(name="DummyWorker")
    snap = worker.snapshot()

    assert snap["name"] == "DummyWorker"
    assert snap["active_task_id"] is None
    assert snap["waiting_task_ids"] == []
    assert snap["queue_size"] == 0


@pytest.mark.asyncio
async def test_snapshot_waiting_ids_fifo_order():
    worker = DummyWorker(name="DummyWorker")
    await worker.add_task({"task_id": "t1", "video_url": "https://example.com/1.mp4"})
    await worker.add_task({"task_id": "t2", "video_url": "https://example.com/2.mp4"})
    await worker.add_task({"task_id": "t3", "video_url": "https://example.com/3.mp4"})

    snap = worker.snapshot()

    # FIFO：先入队者排在前，queue_size 与列表长度一致
    assert snap["waiting_task_ids"] == ["t1", "t2", "t3"]
    assert snap["queue_size"] == 3
    assert snap["active_task_id"] is None


@pytest.mark.asyncio
async def test_snapshot_skips_payloads_without_task_id():
    worker = DummyWorker(name="DummyWorker")
    await worker.add_task({"task_id": "t1"})
    await worker.add_task({"no_task_id": True})

    snap = worker.snapshot()

    assert snap["waiting_task_ids"] == ["t1"]
    assert snap["queue_size"] == 1


@pytest.mark.asyncio
async def test_snapshot_active_task_id_via_process_task_args():
    """active_task_id 从进行中的 asyncio.Task 协程参数提取。"""
    worker = DummyWorker(name="DummyWorker")

    async def processing(payload):
        await asyncio.sleep(0.2)

    process_task = asyncio.create_task(processing({"task_id": "t-active"}))
    worker._active_process_task = process_task

    try:
        snap = worker.snapshot()
        assert snap["active_task_id"] == "t-active"
        assert snap["waiting_task_ids"] == []
    finally:
        process_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await process_task


@pytest.mark.asyncio
async def test_snapshot_active_task_id_via_tracked_field():
    """active_task_id 优先使用显式跟踪的 _active_task_id。"""
    worker = DummyWorker(name="DummyWorker")
    worker._active_task_id = "t-tracked"

    async def processing(payload):
        await asyncio.sleep(0.2)

    process_task = asyncio.create_task(processing({"task_id": "t-arg"}))
    worker._active_process_task = process_task

    try:
        snap = worker.snapshot()
        assert snap["active_task_id"] == "t-tracked"
    finally:
        process_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await process_task


def test_snapshot_safe_default_on_error():
    worker = DummyWorker(name="DummyWorker")
    worker._task_queue._queue = None  # 人为破坏队列结构

    snap = worker.snapshot()

    assert snap["name"] == "DummyWorker"
    assert snap["active_task_id"] is None
    assert snap["waiting_task_ids"] == []
    assert snap["queue_size"] == 0
