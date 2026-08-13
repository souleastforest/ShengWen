"""Test: delete_task 同步清理上传任务存储文件（H1）。

背景：上传任务文件位于存储目录（config.storage.resolved_base_dir）内且以 task_id
为前缀；删除任务时不清理会滞留最长 2h（retention_failed_sec），占用 10G cap 并
可能先于孤儿清扫触发其他 COMPLETED 任务媒体的误回收。
local-path 直读任务的文件是用户原始文件（存储目录外），删除时不得触碰。
"""

import glob
import os

import httpx
import pytest
from fastapi import FastAPI

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.config.settings import config
from src.main.python.sheng_wen.db import db
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.infra.api.routes.upload import router as upload_router

STORAGE_DIR = config.storage.resolved_base_dir


def _make_app():
    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    app.include_router(tasks_router)
    app.include_router(upload_router)
    return app


@pytest.fixture
def app():
    return _make_app()


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _glob_storage(pattern: str) -> list[str]:
    return glob.glob(os.path.join(STORAGE_DIR, pattern))


@pytest.mark.asyncio
async def test_delete_upload_task_removes_storage_files(app):
    """上传任务删除 → 存储目录内其私有文件同步清理。"""
    async with _client(app) as client:
        files = {"file": ("cleanme.mp4", b"fake video", "video/mp4")}
        resp = await client.post("/upload", files=files)
        assert resp.status_code == 201, resp.text
        task_id = resp.json()["id"]

        # 上传文件已落盘
        assert _glob_storage(f"{task_id}_*"), "上传临时文件未落盘"

        resp = await client.delete(f"/tasks/{task_id}")
        assert resp.status_code == 204

    # 文件与任务均已清理
    assert not _glob_storage(f"{task_id}.*"), "任务媒体文件残留"
    assert not _glob_storage(f"{task_id}_*"), "任务临时文件残留"
    assert db.get_task(task_id) is None


@pytest.mark.asyncio
async def test_delete_local_path_task_keeps_user_file(tmp_path, app):
    """local-path 任务删除 → 用户原始文件保留（存储目录外不清理）。"""
    media = tmp_path / "user_recording.wav"
    media.write_bytes(b"user original data")

    async with _client(app) as client:
        resp = await client.post(
            "/upload/local-path",
            json={"file_path": str(media), "summary_mode": "none"},
        )
        assert resp.status_code == 201, resp.text
        task_id = resp.json()["id"]

        resp = await client.delete(f"/tasks/{task_id}")
        assert resp.status_code == 204

    assert media.exists(), "local-path 用户文件被误删"
    assert media.read_bytes() == b"user original data"


@pytest.mark.asyncio
async def test_delete_upload_task_not_affect_other_tasks_files(app):
    """删除一个上传任务不误伤其他任务的文件（前缀隔离）。"""
    async with _client(app) as client:
        files = {"file": ("a.mp4", b"aaa", "video/mp4")}
        resp1 = await client.post("/upload", files=files)
        files = {"file": ("b.mp4", b"bbb", "video/mp4")}
        resp2 = await client.post("/upload", files=files)
        task_a, task_b = resp1.json()["id"], resp2.json()["id"]

        resp = await client.delete(f"/tasks/{task_a}")
        assert resp.status_code == 204

    assert not _glob_storage(f"{task_a}*"), "任务 A 文件残留"
    assert _glob_storage(f"{task_b}*"), "任务 B 文件被误删"

    # 清理任务 B 的测试残留文件
    for path in _glob_storage(f"{task_b}*"):
        os.remove(path)
