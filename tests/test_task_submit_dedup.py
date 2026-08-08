"""Tests: POST /tasks/ 幂等去重（5 秒内同一 video_url 直接返回已创建任务）。"""

import pytest
import httpx

from fastapi import FastAPI

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.db import db
from src.main.python.sheng_wen.infra.api.routes import tasks as tasks_module
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router


@pytest.fixture
def app_with_bus():
    app = FastAPI()
    bus = AsyncioEventBus()
    app.state.event_bus = bus
    app.include_router(tasks_router)
    return app, bus


@pytest.fixture(autouse=True)
def clear_dedup():
    tasks_module._task_submit_dedup.clear()
    yield
    tasks_module._task_submit_dedup.clear()


def _post(client, url, parts=None):
    payload = {"video_url": url, "quality": "best", "summary_mode": "auto"}
    if parts is not None:
        payload["bilibili_parts"] = parts
    return client.post("/tasks/", json=payload)


@pytest.mark.asyncio
async def test_duplicate_submit_within_ttl_returns_same_task(app_with_bus):
    """5 秒内同一 URL 重复提交返回已创建任务（同一 task_id，201）。"""
    app, bus = app_with_bus

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await _post(client, "https://example.com/a.mp4")
        r2 = await _post(client, "https://example.com/a.mp4")

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["video_url"] == "https://example.com/a.mp4"


@pytest.mark.asyncio
async def test_different_urls_create_different_tasks(app_with_bus):
    """不同 URL 不受去重影响。"""
    app, bus = app_with_bus

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await _post(client, "https://example.com/a.mp4")
        r2 = await _post(client, "https://example.com/b.mp4")

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


@pytest.mark.asyncio
async def test_dedup_expires_after_ttl(app_with_bus, monkeypatch):
    """TTL 过期后同一 URL 重新创建新任务。"""
    app, bus = app_with_bus
    now = [1000.0]
    monkeypatch.setattr(tasks_module, "_dedup_now", lambda: now[0])

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await _post(client, "https://example.com/a.mp4")
        now[0] += 10.0  # 超过 5 秒窗口
        r2 = await _post(client, "https://example.com/a.mp4")

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


@pytest.mark.asyncio
async def test_dedup_recreates_after_task_deleted(app_with_bus):
    """窗口内任务被删除后，同一 URL 重新创建。"""
    app, bus = app_with_bus

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await _post(client, "https://example.com/a.mp4")
        db.delete_task(r1.json()["id"])
        r2 = await _post(client, "https://example.com/a.mp4")

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


@pytest.mark.asyncio
async def test_separate_mode_skips_dedup(app_with_bus, monkeypatch):
    """多分P separate 模式跳过去重：每次提交都创建新任务。"""
    app, bus = app_with_bus

    async def fake_fetch_parts(url):
        return (
            "测试标题",
            [{"index": 0, "cid": 1001, "title": "P1", "duration": 60}],
        )

    monkeypatch.setattr(
        tasks_module, "_get_bilibili_video_title_and_parts", fake_fetch_parts
    )

    parts = {"mode": "separate", "indices": [0]}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await _post(
            client, "https://www.bilibili.com/video/BV1xx0000000", parts=parts
        )
        r2 = await _post(
            client, "https://www.bilibili.com/video/BV1xx0000000", parts=parts
        )

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


@pytest.mark.asyncio
async def test_merge_mode_still_dedups(app_with_bus, monkeypatch):
    """多分P merge 模式创建单个任务，仍走 5 秒去重。"""
    app, bus = app_with_bus

    async def fake_fetch_parts(url):
        return (
            "测试标题",
            [{"index": 0, "cid": 1001, "title": "P1", "duration": 60}],
        )

    monkeypatch.setattr(
        tasks_module, "_get_bilibili_video_title_and_parts", fake_fetch_parts
    )

    parts = {"mode": "merge", "indices": [0]}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await _post(
            client, "https://www.bilibili.com/video/BV1xx0000000", parts=parts
        )
        r2 = await _post(
            client, "https://www.bilibili.com/video/BV1xx0000000", parts=parts
        )

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
