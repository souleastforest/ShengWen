"""Tests: POST /bilibili/video-info 探针网络类失败降级为单P语义（不再 500）。

背景：与 POST /tasks/ 探针同一根因（2026-08-18 瞬时 DNS/网络故障）。
网络类失败 → 200 is_multi_part=false（title 尽力获取失败则为空）；
确定性错误（无效 BV / 视频不存在 / 业务性拒绝如 412 风控）→ 保持 500
（前端可区分"不可恢复"）。

真实异常面：bilibili_api selected_client="aiohttp"（curl_cffi 未装），
生产主路径异常类型为 aiohttp.ClientConnectionError 子类；httpx 用例为
兜底类型（未装 aiohttp 时的备选客户端）。
"""

import aiohttp
import httpx
import pytest

from bilibili_api import video
from bilibili_api.exceptions import NetworkException, ResponseCodeException
from fastapi import FastAPI

from src.main.python.sheng_wen.infra.api.routes.bilibili import (
    router as bilibili_router,
)

BILIBILI_URL = "https://www.bilibili.com/video/BV1xx411c7mD"


def _make_connector_error() -> aiohttp.ClientConnectorError:
    """构造真实 aiohttp.ClientConnectorError（DNS/连接失败的生产异常类型）。"""
    connection_key = aiohttp.client_reqrep.ConnectionKey(
        host="api.bilibili.com",
        port=443,
        is_ssl=True,
        ssl=None,
        proxy=None,
        proxy_auth=None,
        proxy_headers_hash=None,
    )
    return aiohttp.ClientConnectorError(
        connection_key, OSError(-3, "Temporary failure in name resolution")
    )


@pytest.fixture
def app_with_router():
    app = FastAPI()
    app.include_router(bilibili_router)
    return app


@pytest.mark.asyncio
async def test_video_info_aiohttp_connector_error_returns_single_part_200(
    app_with_router, monkeypatch
):
    """生产主路径：aiohttp.ClientConnectorError（DNS 失败真实异常）→ 200 单P语义。"""
    app = app_with_router

    async def fake_get_info(self):
        raise _make_connector_error()

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
async def test_video_info_aiohttp_server_disconnected_returns_single_part_200(
    app_with_router, monkeypatch
):
    """aiohttp.ServerDisconnectedError（连接中断）→ 200 单P语义。"""
    app = app_with_router

    async def fake_get_info(self):
        raise aiohttp.ServerDisconnectedError()

    monkeypatch.setattr(video.Video, "get_info", fake_get_info)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/bilibili/video-info", json={"url": BILIBILI_URL})

    assert resp.status_code == 200
    assert resp.json()["is_multi_part"] is False


@pytest.mark.asyncio
async def test_video_info_network_exception_5xx_returns_single_part_200(
    app_with_router, monkeypatch
):
    """NetworkException status>=500（CDN 5xx 瞬时故障）→ 200 单P语义。"""
    app = app_with_router

    async def fake_get_info(self):
        raise NetworkException(502, "Bad Gateway")

    monkeypatch.setattr(video.Video, "get_info", fake_get_info)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/bilibili/video-info", json={"url": BILIBILI_URL})

    assert resp.status_code == 200
    assert resp.json()["is_multi_part"] is False


@pytest.mark.asyncio
async def test_video_info_network_exception_412_still_500(app_with_router, monkeypatch):
    """NetworkException status<500（412 风控/404 业务性拒绝）→ 保持 500。"""
    app = app_with_router

    async def fake_get_info(self):
        raise NetworkException(412, "Precondition Failed")

    monkeypatch.setattr(video.Video, "get_info", fake_get_info)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/bilibili/video-info", json={"url": BILIBILI_URL})

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_video_info_httpx_connect_error_returns_single_part_200(
    app_with_router, monkeypatch
):
    """兜底类型（未装 aiohttp 时备选客户端）：httpx.ConnectError → 200 单P语义。

    注：生产 selected_client="aiohttp"，httpx 类型不出现，此处为双保险。
    """
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
    assert resp.json()["is_multi_part"] is False


@pytest.mark.asyncio
async def test_video_info_network_error_by_message_keyword_200(
    app_with_router, monkeypatch
):
    """消息关键词兜底：非标准 HTTP 客户端类型但含 DNS 失败关键词 → 同样 200 单P语义。"""
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
