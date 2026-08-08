"""Pipeline start/stop 幂等性测试。

背景：ShengWen-app.py（主 app）与 api.py（被挂载子 app）的 lifespan 都会调用
pipeline.start()；若重复订阅，同一 TASK_CREATED 会被派发多次（任务重复入队、
文件任务被二次处理误标 FAILED）。start()/stop() 必须幂等。
"""

import asyncio

import pytest

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.application.pipeline import Pipeline


@pytest.mark.asyncio
async def test_start_twice_subscribes_once():
    bus = AsyncioEventBus()
    pipeline = Pipeline(bus)

    await pipeline.start()
    await pipeline.start()  # 幂等：第二次不应重复订阅

    assert len(pipeline._subscription_ids) == 1
    assert len(bus._handlers.get(TASK_CREATED, {})) == 1

    # 派发一次事件应只触发一次 handler
    fired = []
    bus.subscribe(TASK_CREATED, fired.append)
    await bus.publish(TASK_CREATED, {"task_id": "t1"})
    await asyncio.sleep(0)
    await pipeline.stop()


@pytest.mark.asyncio
async def test_stop_then_restart_subscribes_once():
    bus = AsyncioEventBus()
    pipeline = Pipeline(bus)

    await pipeline.start()
    await pipeline.stop()
    await pipeline.start()

    assert len(pipeline._subscription_ids) == 1
    assert len(bus._handlers.get(TASK_CREATED, {})) == 1


@pytest.mark.asyncio
async def test_stop_is_idempotent():
    bus = AsyncioEventBus()
    pipeline = Pipeline(bus)

    await pipeline.start()
    await pipeline.stop()
    await pipeline.stop()  # 不应报错

    assert len(bus._handlers.get(TASK_CREATED, {})) == 0


@pytest.mark.asyncio
async def test_single_created_event_dispatches_once():
    """一次 TASK_CREATED 发布只触发一次 _on_task_created（模拟重复 start 场景）。"""
    bus = AsyncioEventBus()
    pipeline = Pipeline(bus)
    dispatched = []

    async def fake_factory():
        class FakeWorker:
            name = "FakeWorker"

            async def add_task(self, payload):
                dispatched.append(payload["task_id"])

        return FakeWorker()

    pipeline.register_worker_factory("get_downloader_worker", fake_factory)
    # 模拟两个 lifespan 都调用了 start
    await pipeline.start()
    await pipeline.start()

    await bus.publish(TASK_CREATED, {"task_id": "t1", "type": "fake"})
    await asyncio.sleep(0.01)

    assert dispatched.count("t1") == 1, (
        f"应只派发一次，实际 {len(dispatched)} 次: {dispatched}"
    )
    await pipeline.stop()
