"""Tests: POST /bilibili/video-info 探针网络类失败降级为单P语义（不再 500）。

背景：与 POST /tasks/ 探针同一根因（2026-08-18 瞬时 DNS/网络故障）。
网络类失败 → 200 is_multi_part=false（title 尽力获取失败则为空）；
确定性错误（无效 BV / 视频不存在 / 业务性拒绝）→ 保持 500（前端可区分"不可恢复"）。
"""

import httpx
import pytest

from bilibili_api import video
from bilibili_api.exceptions import ResponseCodeException
from fastapi import FastAPI

from src.main.python.sheng_wen.infra.api.routes.bilibili import (
    router as bilibili_router,
)

BILIBILI_URL = "https://www.bilibili.com/video/BV1xx411c7mD"


@pytest.fixture
def app_with_router():
    app = FastAPI()
    app.include_router(bilibili_router)
    return app


@pytest.mark.asyncio
async def test_video_info_connect_error_returns_single_part_200(
    app_with_router, monkeypatch
):
    """网络类失败（httpx.ConnectError）→ 200 is_multi_part=false，title 为空。"""
    app = app_with_router

    async def fake_get_info(self):
        raise httpx.ConnectError(
            "Cannot connect to host api.bilibili.com:443 "
            "[Temporary failure in name resolution]"
        )

    monkeypatch.setattr(video.Video, "get_info", fake_get_info)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/bilibili/video-info", json={"url": BILIBILI_URL})

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_multi_part"] is False
    assert data["title"] == ""
    assert data["bvid"] == "BV1xx411c7mD"
    assert data["duration"] == 0
    assert data["parts"] is None


@pytest.mark.asyncio
async def test_video_info_network_error_by_message_keyword_200(
    app_with_router, monkeypatch
):
    """消息关键词兜底：非 httpx 类型但含 DNS 失败关键词 → 同样 200 单P语义。"""
    app = app_with_router

    async def fake_get_info(self):
        raise RuntimeError("Temporary failure in name resolution")

    monkeypatch.setattr(video.Video, "get_info", fake_get_info)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/bilibili/video-info", json={"url": BILIBILI_URL})

    assert resp.status_code == 200
    assert resp.json()["is_multi_part"] is False


@pytest.mark.asyncio
async def test_video_info_deterministic_error_still_500(app_with_router, monkeypatch):
    """确定性错误（无效 BV，ResponseCodeException -404）→ 保持 500。"""
    app = app_with_router

    async def fake_get_info(self):
        raise ResponseCodeException(-404, "啥都木有", {})

    monkeypatch.setattr(video.Video, "get_info", fake_get_info)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/bilibili/video-info", json={"url": BILIBILI_URL})

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_video_info_multi_part_normal_200(app_with_router, monkeypatch):
    """正常多P视频 → 200 is_multi_part=true + parts（回归，防误伤）。"""
    app = app_with_router

    async def fake_get_info(self):
        return {
            "title": "测试标题",
            "duration": 120,
            "pages": [
                {"page": 1, "cid": 1001, "part": "P1", "duration": 60},
                {"page": 2, "cid": 1002, "part": "P2", "duration": 60},
            ],
        }

    monkeypatch.setattr(video.Video, "get_info", fake_get_info)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/bilibili/video-info", json={"url": BILIBILI_URL})

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_multi_part"] is True
    assert data["title"] == "测试标题"
    assert len(data["parts"]) == 2
    assert data["parts"][0]["index"] == 0
