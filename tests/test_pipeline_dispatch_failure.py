"""Test: pipeline dispatch failure must mark the task FAILED with error_message.

TDD RED phase — currently a dispatch failure only publishes TASK_FAILED (with
no subscribers in production), leaving the task stuck in PENDING forever
without any error_message.

Root cause chain (生产事故 2026-08 起):
- get_transcriber("vibe_voice_asr") 懒导入 vibe_voice_asr_transcriber 失败
  （环境缺 torch，模块顶层 `import torch` 抛 ModuleNotFoundError）；
- transcriber.py get_class 用 `from None` 掩盖根因，抛 ValueError
  "未注册的转录器类型: ..."；
- pipeline._on_task_created 捕获后仅发布 TASK_FAILED——生产零订阅，事件丢失；
- 任务永久 PENDING 且无 error_message。

修复：pipeline 在 dispatch 失败分支直接复用任务标记失败逻辑
（update_and_notify，与 deps._fail_task_with_model_error 同一模式），
将任务标记为 FAILED 并写入 error_message。
"""

import pytest

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.events.topics import (
    TASK_CREATED,
    TASK_FAILED,
)
from src.main.python.sheng_wen.application.pipeline import Pipeline
from src.main.python.sheng_wen.db import TaskStatus, db


@pytest.fixture
def bus():
    return AsyncioEventBus()


@pytest.fixture
def pipeline(bus):
    return Pipeline(event_bus=bus)


@pytest.mark.asyncio
async def test_dispatch_failure_marks_task_failed(bus, pipeline):
    """dispatch 失败（工厂抛 ValueError，模拟 get_transcriber 懒导入失败）时，
    任务应被标记为 FAILED 且 error_message 包含原因，而不是静默卡 PENDING。"""
    task_id = "task-dispatch-fail"
    db.save_task(
        task_id,
        {
            "id": task_id,
            "video_url": "https://example.com/video.mp4",
            "status": TaskStatus.PENDING,
            "progress": 0.0,
        },
    )

    async def failing_factory():
        raise ValueError(
            "未注册的转录器类型: vibe_voice_asr（模块导入失败，可能缺失依赖）"
        )

    failed_events = []

    async def capture_failed(payload):
        failed_events.append(payload)

    bus.subscribe(TASK_FAILED, capture_failed)
    pipeline.register_worker_factory("get_downloader_worker", failing_factory)

    await pipeline.start()
    await bus.publish(
        TASK_CREATED,
        {
            "task_id": task_id,
            "video_url": "https://example.com/video.mp4",
        },
    )

    task = db.get_task(task_id)
    assert task is not None
    assert task["status"] == "FAILED"
    assert "未注册" in (task.get("error_message") or "")
    # TASK_FAILED 事件仍照常发布（供其他订阅者观测）
    assert failed_events == [
        {
            "task_id": task_id,
            "error": "未注册的转录器类型: vibe_voice_asr（模块导入失败，可能缺失依赖）",
        }
    ]


@pytest.mark.asyncio
async def test_dispatch_success_keeps_task_pending(bus, pipeline):
    """对照：dispatch 成功后任务保持 PENDING（由 worker 后续推进），
    不得被误标 FAILED。"""
    task_id = "task-dispatch-ok"
    db.save_task(
        task_id,
        {
            "id": task_id,
            "video_url": "https://example.com/video.mp4",
            "status": TaskStatus.PENDING,
            "progress": 0.0,
        },
    )

    received = []

    async def ok_worker():
        class _Worker:
            async def add_task(self, payload):
                received.append(payload)

        return _Worker()

    async def ok_factory():
        return await ok_worker()

    pipeline.register_worker_factory("get_downloader_worker", ok_factory)

    await pipeline.start()
    await bus.publish(
        TASK_CREATED,
        {
            "task_id": task_id,
            "video_url": "https://example.com/video.mp4",
        },
    )

    assert len(received) == 1
    task = db.get_task(task_id)
    assert task is not None
    assert task["status"] == "PENDING"
