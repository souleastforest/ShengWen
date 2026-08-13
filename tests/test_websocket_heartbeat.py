"""Tests: WS 广播加固（失败连接移除/发送超时）与双向心跳（ping/pong、接收超时判半开）。

生产事故根因（任务 93b857d0）：广播循环串行 await send_text 无超时 + except pass 吞错 +
失败连接不移除 → 半开连接（TCP 缓冲满无 RST）挂起 send_text 数分钟，堵死后续所有广播。
本测试锁定：
- broadcast 对失败/超时连接：移除并 close，且不阻塞其余连接；
- 服务端心跳：每 HEARTBEAT_INTERVAL 秒发送 {"type": "ping"}；失败同样移除；
- websocket_endpoint 接收超时（RECEIVE_TIMEOUT 内无任何消息，含前端不回 pong）判定半开，
  关闭并移除连接。
"""

import asyncio
import json

import pytest

from src.main.python.sheng_wen.infra.api.routes import websocket as ws


class FakeWebSocket:
    """最小 WebSocket 替身：记录 accept/send/close，可配置 send/receive 行为。"""

    def __init__(self):
        self.accepted = False
        self.sent: list[str] = []
        self.closed = False
        self.close_code = None
        # send_text 异常或挂起行为
        self.send_error: Exception | None = None
        self.send_delay = 0.0
        # receive_text 异常或挂起行为
        self.receive_error: Exception | None = None
        self.receive_delay = 0.0

    async def accept(self):
        self.accepted = True

    async def send_text(self, message: str):
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(message)

    async def receive_text(self):
        if self.receive_delay:
            await asyncio.sleep(self.receive_delay)
        if self.receive_error is not None:
            raise self.receive_error
        return json.dumps({"type": "pong"})

    async def close(self, code=None, reason=None):
        self.closed = True
        self.close_code = code


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    """把广播/心跳/接收超时压到毫秒级，测试不等待真实 10s/30s/90s。"""
    monkeypatch.setattr(ws, "BROADCAST_SEND_TIMEOUT", 0.05)
    monkeypatch.setattr(ws, "HEARTBEAT_INTERVAL", 0.05)
    monkeypatch.setattr(ws, "RECEIVE_TIMEOUT", 0.05)


def _fresh_manager(
    monkeypatch, connections: list[FakeWebSocket] | None = None
) -> ws.ConnectionManager:
    """注入隔离的 ConnectionManager，避免污染模块级 manager。

    connections 为预置连接列表（endpoint 测试不预置——connect() 会 append）。
    """
    manager = ws.ConnectionManager()
    if connections is not None:
        manager.active_connections = list(connections)
    monkeypatch.setattr(ws, "manager", manager)
    return manager


# ---- broadcast：失败/超时连接移除 --------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_removes_connection_whose_send_raises():
    manager = ws.ConnectionManager()
    dead = FakeWebSocket()
    alive = FakeWebSocket()
    dead.send_error = RuntimeError("connection reset")
    manager.active_connections = [dead, alive]

    await manager.broadcast("hello")

    assert dead not in manager.active_connections
    assert dead.closed is True
    assert alive in manager.active_connections
    assert alive.sent == ["hello"]


@pytest.mark.asyncio
async def test_broadcast_removes_connection_whose_send_hangs():
    """send_text 挂起（半开连接 TCP 缓冲满）超时后移除，不阻塞后续连接。"""
    manager = ws.ConnectionManager()
    hung = FakeWebSocket()
    hung.send_delay = 10.0  # 远超 BROADCAST_SEND_TIMEOUT=0.05
    alive = FakeWebSocket()
    manager.active_connections = [hung, alive]

    started = asyncio.get_running_loop().time()
    await manager.broadcast("hello")
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 1.0, f"广播被挂起连接拖垮: elapsed={elapsed:.2f}s"
    assert hung not in manager.active_connections
    assert hung.closed is True
    assert alive.sent == ["hello"]


@pytest.mark.asyncio
async def test_broadcast_stale_copy_safe_send_is_idempotent():
    """广播已快照连接列表后，连接被并发移除：对陈旧副本 _safe_send 不抛异常、
    不重复操作，连接列表状态正确（remove 幂等安全）。"""
    manager = ws.ConnectionManager()
    conn = FakeWebSocket()
    conn.send_error = RuntimeError("gone")
    manager.active_connections = [conn]

    snapshot = list(manager.active_connections)  # broadcast 的迭代副本
    manager.disconnect(conn)  # 广播开始前连接已被并发移除

    # 对陈旧副本直接发送（等价于广播循环对已移除连接调用）
    ok = await manager._safe_send(snapshot[0], "hello")

    assert ok is False
    assert manager.active_connections == []
    assert conn.closed is True


# ---- 心跳：ping 发送与失败移除 ------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_sends_ping_periodically():
    manager = ws.ConnectionManager()
    conn = FakeWebSocket()
    manager.active_connections = [conn]

    task = asyncio.create_task(manager.heartbeat(conn))
    await asyncio.sleep(0.12)  # 约 2 个 HEARTBEAT_INTERVAL 周期
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    pings = [json.loads(m) for m in conn.sent]
    assert len(pings) >= 2
    assert all(m["type"] == "ping" for m in pings)


@pytest.mark.asyncio
async def test_heartbeat_removes_connection_on_ping_send_failure():
    manager = ws.ConnectionManager()
    conn = FakeWebSocket()
    conn.send_error = RuntimeError("broken pipe")
    manager.active_connections = [conn]

    task = asyncio.create_task(manager.heartbeat(conn))
    await asyncio.sleep(0.12)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert conn not in manager.active_connections
    assert conn.closed is True


# ---- websocket_endpoint：接收超时判定半开 --------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_receive_timeout_closes_and_removes_half_open_connection(
    monkeypatch,
):
    """RECEIVE_TIMEOUT 内无消息（前端不回 pong）→ 判定半开：close + 移除 + 结束循环。"""
    conn = FakeWebSocket()
    conn.receive_delay = 10.0  # 无任何消息，挂起
    manager = _fresh_manager(monkeypatch)

    started = asyncio.get_running_loop().time()
    await ws.websocket_endpoint(conn)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 1.0, f"半开连接未及时关闭: elapsed={elapsed:.2f}s"
    assert conn.accepted is True
    assert conn.closed is True
    assert conn not in manager.active_connections


@pytest.mark.asyncio
async def test_endpoint_disconnects_on_websocket_disconnect(monkeypatch):
    """正常 WebSocketDisconnect 仍走 disconnect 移除（回归保护）。"""
    from starlette.websockets import WebSocketDisconnect

    conn = FakeWebSocket()
    conn.receive_error = WebSocketDisconnect()
    manager = _fresh_manager(monkeypatch)

    await ws.websocket_endpoint(conn)

    assert conn not in manager.active_connections
