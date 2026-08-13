"""Test: /upload 流式写盘 + 2GB 上限双保险 + source_name 落库（feat/local-video-upload）。

关键行为变更（对照旧实现）：
- 旧：`await file.read()` 整体读入内存 → 先写满盘再检查 500MB 上限
- 新：1MB 分块流式写盘（边写边累计）→ Content-Length 预检 + 流式累计双保险 413 → 上限配置化
      （config.storage.max_upload_mb，默认 2048）→ source_name 落库

测试上传会在项目根 temp/ 下创建 `{task_id}_temp{ext}` 文件（端点硬编码 cwd 相对路径），
teardown 中按 task_id 精确清理（本次测试创建的临时产物）。
"""

import os
import pytest
import httpx

from fastapi import FastAPI
from starlette.datastructures import UploadFile as StarletteUploadFile

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.config.settings import StorageConfig
from src.main.python.sheng_wen.db import db
from src.main.python.sheng_wen.infra.api.routes import upload as upload_module
from src.main.python.sheng_wen.infra.api.routes.upload import router as upload_router

TEMP_DIR = "temp"


def _make_app():
    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    app.include_router(upload_router)
    return app


def _uploaded_temp_paths(task_id: str) -> list[str]:
    """解析任务落盘的文件（含 413 清理前可能存在的路径）。"""
    return [
        os.path.join(TEMP_DIR, f"{task_id}_temp.mp4"),
        os.path.join(TEMP_DIR, f"{task_id}_temp.mp3"),
    ]


def _cleanup_task_files(task_ids: list[str]):
    for task_id in task_ids:
        for path in _uploaded_temp_paths(task_id):
            if os.path.exists(path):
                os.remove(path)


@pytest.fixture
def track_created():
    """记录测试创建的任务 id，teardown 清理其临时文件。"""
    created: list[str] = []
    yield created
    _cleanup_task_files(created)


@pytest.mark.asyncio
async def test_upload_streams_in_chunks(track_created, monkeypatch):
    """2.5MB 文件：file.read 分块多次调用（旧实现仅 1 次整体读），落盘内容完整。"""
    app = _make_app()
    payload = os.urandom(2 * 1024 * 1024 + 512 * 1024)  # 2.5MB

    # 注意：multipart 解析创建的是 starlette.datastructures.UploadFile 实例
    # （fastapi.UploadFile 是不同类对象，patch 它不会生效）
    original_read = StarletteUploadFile.read
    read_calls = {"n": 0}

    async def counting_read(self, size: int = -1):
        read_calls["n"] += 1
        return await original_read(self, size)

    monkeypatch.setattr(StarletteUploadFile, "read", counting_read)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("big.mp4", payload, "video/mp4")}
        resp = await client.post("/upload", files=files)

    assert resp.status_code == 201, resp.text
    assert read_calls["n"] >= 3, f"分块流式写盘未生效，read 仅调用 {read_calls['n']} 次"

    task_id = resp.json()["id"]
    track_created.append(task_id)
    stored = db.get_task(task_id)
    assert stored is not None
    media_path = stored["video_url"].removeprefix("file://")
    with open(media_path, "rb") as f:
        assert f.read() == payload, "落盘内容与上传内容不一致"


@pytest.mark.asyncio
async def test_upload_persists_source_name_and_publishes(track_created):
    """source_name 落库 + API 响应字段 + TASK_CREATED payload 透传。"""
    app = _make_app()
    published = []

    async def capture(payload):
        published.append(payload)

    app.state.event_bus.subscribe(TASK_CREATED, capture)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("我的视频.mp4", b"fake video content", "video/mp4")}
        resp = await client.post("/upload", files=files, data={"summary_mode": "none"})

    assert resp.status_code == 201, resp.text
    task_id = resp.json()["id"]
    track_created.append(task_id)

    assert resp.json()["source_name"] == "我的视频.mp4"
    stored = db.get_task(task_id)
    assert stored is not None
    assert stored["source_name"] == "我的视频.mp4"
    assert published[0]["source_name"] == "我的视频.mp4"


@pytest.mark.asyncio
async def test_upload_oversize_stream_rejects_413(track_created, monkeypatch):
    """流式累计超限 → 413 + 临时文件清理 + 任务未入库（绕过 Content-Length 预检）。"""
    app = _make_app()

    # 上限压到 ~2KB；容忍保持默认（32MB）→ 预检不触发，由流式累计分支拦截
    monkeypatch.setattr(
        upload_module,
        "config",
        type("FakeConfig", (), {"storage": StorageConfig(max_upload_mb=0.002)})(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("huge.mp4", os.urandom(64 * 1024), "video/mp4")}  # 64KB > 2KB
        resp = await client.post("/upload", files=files)

    assert resp.status_code == 413, resp.text
    assert "最大支持" in resp.json()["detail"]

    # 无任务入库 + 本次请求无新增临时文件（快照对比，避免匹配历史残留）
    assert db.list_tasks() == []
    before = set(os.listdir(TEMP_DIR)) if os.path.isdir(TEMP_DIR) else set()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("huge.mp4", os.urandom(64 * 1024), "video/mp4")}
        await client.post("/upload", files=files)
    after = set(os.listdir(TEMP_DIR)) if os.path.isdir(TEMP_DIR) else set()
    assert not (after - before), f"超限清理未生效，新增残留: {after - before}"


@pytest.mark.asyncio
async def test_upload_oversize_content_length_preflight_413(track_created, monkeypatch):
    """Content-Length 预检（无容忍）→ 直接 413，不开始写盘。"""
    app = _make_app()

    monkeypatch.setattr(
        upload_module,
        "config",
        type("FakeConfig", (), {"storage": StorageConfig(max_upload_mb=0.002)})(),
    )
    monkeypatch.setattr(upload_module, "_UPLOAD_PREFLIGHT_TOLERANCE_BYTES", 0)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("huge.mp4", os.urandom(64 * 1024), "video/mp4")}
        resp = await client.post("/upload", files=files)

    assert resp.status_code == 413, resp.text
    assert db.list_tasks() == []


def test_file_upload_worker_max_size_follows_config():
    """FileUploadWorker 上限跟随 storage.max_upload_mb（默认 2GB，可注入覆盖）。

    回归：worker 曾硬编码 500MB，端点升 2GB 后 500MB~2GB 上传会"先成功建任务后 FAILED"
    （code-reviewer B1）。
    """
    from src.main.python.sheng_wen.downloader.file_upload_worker import (
        FileUploadWorker,
    )

    worker = FileUploadWorker("upload_test")
    assert worker.MAX_FILE_SIZE == 2048 * 1024 * 1024

    worker_injected = FileUploadWorker("upload_test2", max_file_mb=0.001)
    assert worker_injected.MAX_FILE_SIZE == int(0.001 * 1024 * 1024)


@pytest.mark.asyncio
async def test_upload_unsupported_ext_rejects_400():
    """回归：扩展名白名单外文件 400。"""
    app = _make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("evil.exe", b"MZ", "application/octet-stream")}
        resp = await client.post("/upload", files=files)

    assert resp.status_code == 400
    assert "不支持的文件格式" in resp.json()["detail"]
    assert db.list_tasks() == []


@pytest.mark.asyncio
async def test_upload_local_path_persists_source_name(tmp_path):
    """/upload/local-path：source_name 取文件名。"""
    app = _make_app()
    media = tmp_path / "本地录音.wav"
    media.write_bytes(b"fake")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/upload/local-path",
            json={"file_path": str(media), "summary_mode": "none"},
        )

    assert resp.status_code == 201, resp.text
    task_id = resp.json()["id"]
    assert resp.json()["source_name"] == "本地录音.wav"
    stored = db.get_task(task_id)
    assert stored is not None
    assert stored["source_name"] == "本地录音.wav"
