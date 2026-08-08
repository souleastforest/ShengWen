"""Tests: lifespan 启动前调用 db.recover_interrupted_tasks() 恢复中断任务。"""

import pytest

import src.main.python.sheng_wen.api as api_module


class _FakeDB:
    def __init__(self, recovered: int = 3):
        self.recovered = recovered
        self.calls = []

    def recover_interrupted_tasks(self) -> int:
        self.calls.append("recover_interrupted_tasks")
        return self.recovered


def _mock_reclaimer(monkeypatch):
    """隔离存储回收器：避免 lifespan 对真实 DB/temp 目录产生副作用。"""

    async def fake_start():
        pass

    async def fake_stop():
        pass

    monkeypatch.setattr(api_module, "start_storage_reclaimer", fake_start)
    monkeypatch.setattr(api_module, "stop_storage_reclaimer", fake_stop)


@pytest.mark.asyncio
async def test_lifespan_calls_recover_before_pipeline_start(monkeypatch):
    """恢复中断任务应在 pipeline.start() 之前执行。"""
    fake_db = _FakeDB(recovered=2)
    monkeypatch.setattr(api_module, "db", fake_db)
    _mock_reclaimer(monkeypatch)

    order = []

    async def fake_pipeline_start():
        order.append("pipeline.start")

    async def fake_pipeline_stop():
        order.append("pipeline.stop")

    monkeypatch.setattr(api_module.pipeline, "start", fake_pipeline_start)
    monkeypatch.setattr(api_module.pipeline, "stop", fake_pipeline_stop)

    async with api_module.app.router.lifespan_context(api_module.app):
        pass

    assert fake_db.calls == ["recover_interrupted_tasks"]
    assert order == ["pipeline.start", "pipeline.stop"]


@pytest.mark.asyncio
async def test_lifespan_survives_recover_exception(monkeypatch):
    """recover_interrupted_tasks 抛异常时不应阻断 pipeline 启动。"""

    class _RaisingDB:
        def recover_interrupted_tasks(self) -> int:
            raise RuntimeError("db down")

    monkeypatch.setattr(api_module, "db", _RaisingDB())
    _mock_reclaimer(monkeypatch)

    started = []

    async def fake_pipeline_start():
        started.append(True)

    monkeypatch.setattr(api_module.pipeline, "start", fake_pipeline_start)

    async with api_module.app.router.lifespan_context(api_module.app):
        pass

    assert started == [True]
