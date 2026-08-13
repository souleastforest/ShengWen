from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from src.main.python.sheng_wen.db import db

router = APIRouter()

# ---------------------------------------------------------------------------
# WS 协议约定（向后兼容：旧客户端收到未知 type 忽略；旧服务端不主动发 ping）。
#
# type 取值：
# - "task_update"   任务全量数据 + 可选 queues 快照（服务端 → 客户端）
# - "progress_update"  任务进度（服务端 → 客户端，早期兼容消息）
# - "ping"          服务端心跳探测（服务端 → 客户端）；客户端应回发 pong
# - "pong"          客户端心跳应答（客户端 → 服务端）；服务端仅把它当作
#                   “连接仍有消息往来”的活性信号，不做业务处理
#
# 半开连接检测（生产事故 93b857d0 修复）：
# - 服务端每 HEARTBEAT_INTERVAL 秒对每连接发送 ping；
# - receive 循环在 RECEIVE_TIMEOUT 内收不到任何消息（含不回 pong）即判定
#   半开，close 并移除连接；
# - 广播/心跳发送失败或超过 BROADCAST_SEND_TIMEOUT 同样移除该连接，
#   单连接阻塞不拖垮全体广播。
# ---------------------------------------------------------------------------

# 单连接发送超时（秒）：半开连接 TCP 缓冲满时 send_text 挂起，超时后移除
BROADCAST_SEND_TIMEOUT = 10.0
# 服务端心跳间隔（秒）
HEARTBEAT_INTERVAL = 30.0
# receive 超时（秒）：期间无任何消息（含前端不回 pong）判定半开
RECEIVE_TIMEOUT = 90.0


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        # 用 remove 而非 index 删除：并发（广播/心跳/endpoint）下连接可能
        # 已被移除，remove 幂等安全；读写均在主事件循环（asyncio 单线程）。
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def _close_connection(self, websocket: WebSocket) -> None:
        try:
            await websocket.close()
        except Exception:
            pass

    async def _safe_send(self, websocket: WebSocket, message: str) -> bool:
        """单连接发送：超时/失败即移除并关闭，返回是否发送成功。

        半开连接（TCP 缓冲满无 RST）会让 send_text 挂起数分钟——加超时后，
        单连接阻塞不再拖垮后续所有广播。
        """
        try:
            await asyncio.wait_for(
                websocket.send_text(message), timeout=BROADCAST_SEND_TIMEOUT
            )
            return True
        except Exception as e:
            logger.warning(
                f"[WebSocket] 发送失败（{type(e).__name__}），移除连接: "
                f"active={len(self.active_connections)}"
            )
            self.disconnect(websocket)
            await self._close_connection(websocket)
            return False

    async def broadcast(self, message: str):
        # 迭代副本：发送失败的连接会被移除，不干扰遍历
        for connection in list(self.active_connections):
            await self._safe_send(connection, message)

    async def heartbeat(self, websocket: WebSocket) -> None:
        """每 HEARTBEAT_INTERVAL 秒对单连接发送 ping；发送失败连接已被移除则退出。"""
        try:
            while websocket in self.active_connections:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if websocket not in self.active_connections:
                    return
                await self._safe_send(websocket, json.dumps({"type": "ping"}))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[WebSocket] 心跳循环退出: {type(e).__name__}: {e}")


manager = ConnectionManager()


async def notify_task_update(task_id: str, task_data: dict = None):
    if task_data is None:
        task_data = db.get_task(task_id)

    if task_data:
        if isinstance(task_data.get("created_at"), datetime):
            task_data["created_at"] = task_data["created_at"].isoformat()
        if isinstance(task_data.get("latest_modified_at"), datetime):
            task_data["latest_modified_at"] = task_data[
                "latest_modified_at"
            ].isoformat()

        # 附带 4 个 worker 的队列快照（向后兼容：旧客户端忽略多余字段）。
        queues = []
        try:
            from src.main.python.sheng_wen.api import get_queue_snapshots

            queues = await get_queue_snapshots()
        except Exception as e:
            logger.warning(f"[WebSocket] 获取队列快照失败，本次消息不含 queues: {e}")

        message = json.dumps(
            {"type": "task_update", "task": task_data, "queues": queues}
        )
        await manager.broadcast(message)
    else:
        logger.warning(f"[WebSocket] Task not found for broadcast: task_id={task_id}")


async def notify_progress_update(task_id: str, progress: float):
    message = json.dumps(
        {"type": "progress_update", "task_id": task_id, "progress": progress}
    )
    logger.debug(f"{message}")
    await manager.broadcast(message)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # 引用模块级 manager（测试通过 monkeypatch ws.manager 注入隔离实例）
    await manager.connect(websocket)
    heartbeat_task = asyncio.create_task(manager.heartbeat(websocket))
    try:
        while True:
            # 双向心跳：RECEIVE_TIMEOUT 内收不到任何消息（含客户端不回 pong）
            # 判定半开连接，close 并移除，结束本连接循环。
            try:
                await asyncio.wait_for(
                    websocket.receive_text(), timeout=RECEIVE_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning("[WebSocket] 接收超时（半开连接），关闭并移除连接")
                manager.disconnect(websocket)
                await manager._close_connection(websocket)
                return
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
