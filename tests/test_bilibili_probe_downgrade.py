"""Tests: POST /tasks/ B 站分P探针网络类失败降级为单P提交（不再 422）。

背景：2026-08-18 事故——瞬时 DNS/网络故障时探针抛异常被无差别转 422
（"无法确认 B 站分P信息"），状态码与文案均误导。修复：网络类失败
（httpx 传输层异常 / DNS 解析失败等）降级为整视频单P语义继续创建任务，
确定性错误（无效 BV / 视频不存在 / 业务性拒绝）与多P检测保持 422。
"""

import httpx
import pytest

from bilibili_api.exceptions import ResponseCodeException
from fastapi import FastAPI

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.db import db
from src.main.python.sheng_wen.infra.api.routes import tasks as tasks_module
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router

BILIBILI_URL = "https://www.bilibili.com/video/BV1xx411c7mD"


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


def _post(client, url=BILIBILI_URL):
    payload = {"video_url": url, "quality": "best", "summary_mode": "auto"}
    return client.post("/tasks/", json=payload)


@pytest.mark.asyncio
async def test_probe_connect_error_downgrades_to_single_part_201(
    app_with_bus, monkeypatch
):
    """网络类失败（httpx.ConnectError，DNS 失败的真实异常类型）→ 201 创建成功。"""
    app, bus = app_with_bus

    async def fake_probe(url):
        raise httpx.ConnectError(
            "Cannot connect to host api.bilibili.com:443 "
            "[Temporary failure in name resolution]"
        )

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    published = []

    async def capture(payload):
        published.append(payload)

    bus.subscribe(TASK_CREATED, capture)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client)

    assert resp.status_code == 201
    data = resp.json()
    assert data["video_url"] == BILIBILI_URL
    assert db.get_task(data["id"]) is not None
    # 单P语义：降级后事件 payload 不带 bilibili_parts / multipart_batch
    assert len(published) == 1
    payload = published[0]
    assert payload["task_id"] == data["id"]
    assert "bilibili_parts" not in payload
    assert "multipart_batch" not in payload


@pytest.mark.asyncio
async def test_probe_network_error_by_message_keyword_201(app_with_bus, monkeypatch):
    """消息关键词兜底：非 httpx 类型但含 DNS 失败关键词 → 同样降级 201。"""
    app, bus = app_with_bus

    async def fake_probe(url):
        raise RuntimeError("Temporary failure in name resolution")

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client)

    assert resp.status_code == 201
    assert resp.json()["video_url"] == BILIBILI_URL


@pytest.mark.asyncio
async def test_probe_deterministic_error_still_422(app_with_bus, monkeypatch):
    """确定性错误（无效 BV，ResponseCodeException -404）→ 仍 422。"""
    app, bus = app_with_bus

    async def fake_probe(url):
        raise ResponseCodeException(-404, "啥都木有", {})

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client)

    assert resp.status_code == 422
    assert "无法确认 B 站分P信息" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_probe_multi_part_detection_still_422(app_with_bus, monkeypatch):
    """检测到多P（len(parts_info) > 1）→ 仍 422，要求先选择分P。"""
    app, bus = app_with_bus

    async def fake_probe(url):
        return (
            "测试标题",
            [
                {"index": 0, "cid": 1001, "title": "P1", "duration": 60},
                {"index": 1, "cid": 1002, "title": "P2", "duration": 60},
            ],
        )

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client)

    assert resp.status_code == 422
    assert "检测到多分P视频" in resp.json()["detail"]
