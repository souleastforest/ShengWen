from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from src.main.python.sheng_wen.db import db


router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass


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

        message = json.dumps({"type": "task_update", "task": task_data})
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
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
