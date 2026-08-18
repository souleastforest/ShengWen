"""Tests: POST /tasks/ B 站分P探针网络类失败降级为单P提交（不再 422）。

背景：2026-08-18 事故——瞬时 DNS/网络故障时探针抛异常被无差别转 422
（"无法确认 B 站分P信息"），状态码与文案均误导。修复：网络类失败
（aiohttp/httpx 传输层异常 / DNS 解析失败 / CDN 5xx 等）降级为整视频
单P语义继续创建任务，确定性错误（无效 BV / 视频不存在 / 业务性拒绝
如 412 风控）与多P检测保持 422。

真实异常面：bilibili_api 客户端注册顺序 httpx → aiohttp → curl_cffi，
curl_cffi 未装且 aiohttp 可用时 selected_client="aiohttp"——生产主路径
异常类型为 aiohttp.ClientConnectionError 子类（ClientConnectorError /
ServerDisconnectedError 等）。httpx.ConnectError 用例保留为"兜底类型"
（未装 aiohttp 时的备选客户端，当前生产不出现）。
"""

import aiohttp
import httpx
import pytest

from bilibili_api.exceptions import NetworkException, ResponseCodeException
from fastapi import FastAPI

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.db import db
from src.main.python.sheng_wen.infra.api.routes import tasks as tasks_module
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router

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
async def test_probe_aiohttp_connector_error_downgrades_201(app_with_bus, monkeypatch):
    """生产主路径：aiohttp.ClientConnectorError（DNS 失败真实异常）→ 201 创建成功。"""
    app, bus = app_with_bus

    async def fake_probe(url):
        raise _make_connector_error()

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
async def test_probe_aiohttp_server_disconnected_downgrades_201(
    app_with_bus, monkeypatch
):
    """aiohttp.ServerDisconnectedError（连接中断）→ 降级 201。"""
    app, bus = app_with_bus

    async def fake_probe(url):
        raise aiohttp.ServerDisconnectedError()

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client)

    assert resp.status_code == 201
    assert resp.json()["video_url"] == BILIBILI_URL


@pytest.mark.asyncio
async def test_probe_network_exception_5xx_downgrades_201(app_with_bus, monkeypatch):
    """bilibili_api.NetworkException status>=500（CDN 5xx 瞬时故障）→ 降级 201。"""
    app, bus = app_with_bus

    async def fake_probe(url):
        raise NetworkException(502, "Bad Gateway")

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client)

    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_probe_network_exception_412_still_422(app_with_bus, monkeypatch):
    """NetworkException status<500（412 风控/404 业务性拒绝）→ 保持 422。"""
    app, bus = app_with_bus

    async def fake_probe(url):
        raise NetworkException(412, "Precondition Failed")

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_probe_httpx_connect_error_downgrades_201(app_with_bus, monkeypatch):
    """兜底类型（未装 aiohttp 时备选客户端）：httpx.ConnectError → 降级 201。

    注：生产 selected_client="aiohttp"，httpx 类型不出现，此处为双保险。
    """
    app, bus = app_with_bus

    async def fake_probe(url):
        raise httpx.ConnectError(
            "Cannot connect to host api.bilibili.com:443 "
            "[Temporary failure in name resolution]"
        )

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client)

    assert resp.status_code == 201
    assert resp.json()["video_url"] == BILIBILI_URL


@pytest.mark.asyncio
async def test_probe_network_error_by_message_keyword_201(app_with_bus, monkeypatch):
    """消息关键词兜底：非标准 HTTP 客户端类型但含 DNS 失败关键词 → 同样降级 201。"""
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
async def test_probe_bilibili_business_error_keyword_not_misclassified(
    app_with_bus, monkeypatch
):
    """ApiException 业务异常在关键词兜底前显式排除：含网络关键词的业务消息不降级。"""
    app, bus = app_with_bus

    async def fake_probe(url):
        raise ResponseCodeException(
            -404, "network is unreachable（业务文案含网络词）", {}
        )

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_probe_deterministic_error_still_422(app_with_bus, monkeypatch):
    """确定性错误（无效 BV，ResponseCodeException -404）→ 仍 422，文案引导检查链接。"""
    app, bus = app_with_bus

    async def fake_probe(url):
        raise ResponseCodeException(-404, "啥都木有", {})

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client)

    assert resp.status_code == 422
    assert "请检查链接是否正确后重试" in resp.json()["detail"]


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
