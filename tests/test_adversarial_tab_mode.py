"""对抗性测试：模式 Tab 三选一（summary_mode none/standard/agent）后端路由行为。

从需求出发的攻击点：
- re_summarize_task：resolved 'none' 时持久化/广播 summary_mode='auto'（消除
  "none + SUMMARIZING" 瞬时误导），但 worker payload 仍传 'none' 走 auto 兜底；
  显式 standard/agent 不得被改写成 auto。
- create_task / upload / upload-local-path：summary_mode='none' 入库且 TASK_CREATED
  事件透传；非法值归一化回退默认（不 422）。
- re_transcribe_task：payload 'none' 时重置 summary_mode='none'、transcript=''、
  summary=''；无 payload 时 fallback 沿用任务已存模式。
- 边界：无转录原文时 re-summarize 必须 400。

每个用例失败 = 实现缺陷。
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.infra.api.routes.upload import router as upload_router
from src.main.python.sheng_wen.task_parts import init_task_parts


def _save_task(
    task_id: str,
    summary_mode: str = "none",
    transcript: str = "hello",
    status: str = TaskStatus.COMPLETED,
    video_url: str = "https://example.com/video.mp4",
) -> None:
    db.save_task(
        task_id,
        {
            "id": task_id,
            "video_url": video_url,
            "status": status,
            "created_at": datetime.now(timezone.utc),
            "latest_modified_at": datetime.now(timezone.utc),
            "progress": 0.0,
            "title": "test",
            "author_name": None,
            "author_url": None,
            "summary_mode": summary_mode,
            "transcript": transcript,
            "summary": "旧总结" if transcript else "",
            "summary_chunk_total": None,
            "summary_chunk_done": None,
            "summary_meta": None,
        },
    )


def _make_app(llm_worker=None, downloader_worker=None) -> FastAPI:
    app = FastAPI()
    from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus

    app.state.event_bus = AsyncioEventBus()
    if llm_worker is not None:
        app.state.get_llm_worker = AsyncMock(return_value=llm_worker)
    if downloader_worker is not None:
        app.state.get_downloader_worker = AsyncMock(return_value=downloader_worker)
    app.include_router(tasks_router)
    app.include_router(upload_router)
    return app


def _post(app: FastAPI, url: str, json: dict | None = None):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ---- re_summarize_task：'none' → 持久化/广播 'auto'，worker payload 仍 'none' ----------


@pytest.mark.asyncio
async def test_resummarize_none_persists_auto_worker_payload_stays_none():
    llm_worker = AsyncMock()
    app = _make_app(llm_worker=llm_worker)
    task_id = str(uuid.uuid4())
    _save_task(task_id, summary_mode="none")

    async with _post(app, "/tasks") as client:
        resp = await client.post(
            f"/tasks/{task_id}/re-summarize", json={"summary_mode": "none"}
        )

    assert resp.status_code == 200
    # 持久化（= 广播内容同源）：'none' 被改写为 'auto'
    task = db.get_task(task_id)
    assert task["summary_mode"] == "auto"
    assert task["status"] == TaskStatus.SUMMARIZING
    assert task["summary"] == ""
    # worker payload 仍传 'none'（LLMWorker 内部按 auto 兜底判定）
    sent = llm_worker.add_task.await_args.args[0]
    assert sent["summary_mode"] == "none"


@pytest.mark.asyncio
async def test_resummarize_no_payload_on_none_task_broadcasts_auto():
    llm_worker = AsyncMock()
    app = _make_app(llm_worker=llm_worker)
    task_id = str(uuid.uuid4())
    _save_task(task_id, summary_mode="none")

    async with _post(app, "/tasks") as client:
        resp = await client.post(f"/tasks/{task_id}/re-summarize")

    assert resp.status_code == 200
    # 无显式模式 → fallback 沿用任务已存 'none' → 广播/持久化改写 'auto'
    assert db.get_task(task_id)["summary_mode"] == "auto"
    sent = llm_worker.add_task.await_args.args[0]
    assert sent["summary_mode"] == "none"


@pytest.mark.asyncio
async def test_resummarize_explicit_standard_not_rewritten_to_auto():
    llm_worker = AsyncMock()
    app = _make_app(llm_worker=llm_worker)
    task_id = str(uuid.uuid4())
    _save_task(task_id, summary_mode="none")

    async with _post(app, "/tasks") as client:
        resp = await client.post(
            f"/tasks/{task_id}/re-summarize", json={"summary_mode": "standard"}
        )

    assert resp.status_code == 200
    assert db.get_task(task_id)["summary_mode"] == "standard"
    sent = llm_worker.add_task.await_args.args[0]
    assert sent["summary_mode"] == "standard"


@pytest.mark.asyncio
async def test_resummarize_none_multipart_worker_payload_keeps_none():
    llm_worker = AsyncMock()
    app = _make_app(llm_worker=llm_worker)
    task_id = str(uuid.uuid4())
    _save_task(task_id, summary_mode="none", video_url="https://www.bilibili.com/video/BV1test")
    init_task_parts(
        task_id,
        [
            {"index": 0, "cid": None, "title": "P1", "duration": 10.0},
            {"index": 1, "cid": None, "title": "P2", "duration": 10.0},
        ],
    )

    async with _post(app, "/tasks") as client:
        resp = await client.post(
            f"/tasks/{task_id}/re-summarize", json={"summary_mode": "none"}
        )

    assert resp.status_code == 200
    assert db.get_task(task_id)["summary_mode"] == "auto"
    sent = llm_worker.add_task.await_args.args[0]
    assert sent["summary_mode"] == "none"
    assert sent["multipart_resummarize"] is True


@pytest.mark.asyncio
async def test_resummarize_empty_transcript_returns_400():
    app = _make_app(llm_worker=AsyncMock())
    task_id = str(uuid.uuid4())
    _save_task(task_id, summary_mode="none", transcript="")

    async with _post(app, "/tasks") as client:
        resp = await client.post(
            f"/tasks/{task_id}/re-summarize", json={"summary_mode": "none"}
        )

    assert resp.status_code == 400
    assert "重新总结" in resp.json()["detail"]


# ---- create_task / upload：'none' 入库 + 事件透传 ---------------------------------------


@pytest.mark.asyncio
async def test_create_task_none_persists_and_publishes_none():
    app = _make_app()
    published = []

    async def capture(payload):
        published.append(payload)

    app.state.event_bus.subscribe(TASK_CREATED, capture)

    async with _post(app, "/tasks") as client:
        resp = await client.post(
            "/tasks/",
            json={
                "video_url": "https://example.com/v2.mp4",
                "quality": "best",
                "summary_mode": "none",
            },
        )

    assert resp.status_code == 201
    task_id = resp.json()["id"]
    assert db.get_task(task_id)["summary_mode"] == "none"
    assert published[0]["summary_mode"] == "none"


@pytest.mark.asyncio
async def test_upload_none_persists_and_publishes_none():
    app = _make_app()
    published = []

    async def capture(payload):
        published.append(payload)

    app.state.event_bus.subscribe(TASK_CREATED, capture)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("t.mp3", b"fake audio content", "audio/mpeg")}
        data = {"summary_mode": "none"}
        resp = await client.post("/upload", files=files, data=data)

    assert resp.status_code == 201
    task_id = resp.json()["id"]
    assert db.get_task(task_id)["summary_mode"] == "none"
    assert published[0]["summary_mode"] == "none"


@pytest.mark.asyncio
async def test_upload_local_path_none_persists_none(tmp_path):
    app = _make_app()
    media = tmp_path / "in.mp3"
    media.write_bytes(b"fake")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/upload/local-path",
            json={"file_path": str(media), "summary_mode": "none"},
        )

    assert resp.status_code == 201
    task_id = resp.json()["id"]
    assert db.get_task(task_id)["summary_mode"] == "none"


@pytest.mark.asyncio
async def test_upload_invalid_summary_mode_falls_back_to_auto_not_422():
    app = _make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("t.mp3", b"fake audio content", "audio/mpeg")}
        data = {"summary_mode": "bogus"}
        resp = await client.post("/upload", files=files, data=data)

    assert resp.status_code == 201
    assert db.get_task(resp.json()["id"])["summary_mode"] == "auto"


# ---- re_transcribe_task：'none' 重置语义 + fallback -------------------------------------


@pytest.mark.asyncio
async def test_re_transcribe_none_resets_and_keeps_none_mode():
    downloader_worker = AsyncMock()
    app = _make_app(downloader_worker=downloader_worker)
    task_id = str(uuid.uuid4())
    _save_task(task_id, summary_mode="agent", transcript="hello")

    async with _post(app, "/tasks") as client:
        resp = await client.post(
            f"/tasks/{task_id}/re-transcribe", json={"summary_mode": "none"}
        )

    assert resp.status_code == 200
    task = db.get_task(task_id)
    # none 模式：内容清空且模式落 none（转录完成即终态，不再生成总结）
    assert task["transcript"] == ""
    assert task["summary"] == ""
    assert task["summary_mode"] == "none"
    assert task["status"] == TaskStatus.DOWNLOADING
    sent = downloader_worker.add_task.await_args.args[0]
    assert sent["summary_mode"] == "none"


@pytest.mark.asyncio
async def test_re_transcribe_no_payload_falls_back_to_task_mode():
    downloader_worker = AsyncMock()
    app = _make_app(downloader_worker=downloader_worker)
    task_id = str(uuid.uuid4())
    _save_task(task_id, summary_mode="agent", transcript="hello")

    async with _post(app, "/tasks") as client:
        resp = await client.post(f"/tasks/{task_id}/re-transcribe")

    assert resp.status_code == 200
    assert db.get_task(task_id)["summary_mode"] == "agent"
    sent = downloader_worker.add_task.await_args.args[0]
    assert sent["summary_mode"] == "agent"
