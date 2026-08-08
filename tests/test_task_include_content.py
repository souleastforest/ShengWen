"""Test: GET /tasks/{task_id} include_content 参数行为。

include_content=true（默认）返回完整 transcript/summary/summary_meta；
include_content=false 剥离 transcript/summary_meta 并把 summary 截断为概览。
"""

import httpx
import pytest
from fastapi import FastAPI

from src.main.python.sheng_wen.db import db
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router


def _sample_task(**overrides) -> dict:
    task = {
        "id": "test-task-1",
        "video_url": "https://example.com/video.mp4",
        "status": "COMPLETED",
        "created_at": "2026-04-07T00:00:00Z",
        "latest_modified_at": "2026-04-07T00:00:00Z",
        "progress": 1.0,
        "title": "Test Task",
        "transcript": "完整转录原文内容",
        "summary": "完整总结内容",
        "error_message": None,
        "audio_duration": 100.0,
        "transcription_time": 10.0,
        "topic": None,
        "author_name": None,
        "author_url": None,
        "summary_mode": "agent",
        "summary_chunk_total": 1,
        "summary_chunk_done": 1,
        "summary_meta": '{"agent_chunks": 2}',
    }
    task.update(overrides)
    return task


@pytest.fixture
def app_with_task(monkeypatch):
    monkeypatch.setattr(db, "get_task", lambda task_id: _sample_task())
    app = FastAPI()
    app.include_router(tasks_router)
    return app


@pytest.mark.asyncio
async def test_get_task_default_returns_full_content(app_with_task):
    """默认 include_content=true：transcript/summary/summary_meta 完整返回。"""
    transport = httpx.ASGITransport(app=app_with_task)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["transcript"] == "完整转录原文内容"
    assert data["summary"] == "完整总结内容"
    assert data["summary_meta"] == '{"agent_chunks": 2}'


@pytest.mark.asyncio
async def test_get_task_include_content_false_strips_transcript(app_with_task):
    """include_content=false：剥离 transcript 与 summary_meta。"""
    transport = httpx.ASGITransport(app=app_with_task)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1?include_content=false")

    assert resp.status_code == 200
    data = resp.json()
    assert data["transcript"] is None
    assert data["summary_meta"] is None
    assert data["summary"] == "完整总结内容"


@pytest.mark.asyncio
async def test_get_task_include_content_false_truncates_long_summary(monkeypatch):
    """include_content=false：超过 12000 字符的 summary 被截断。"""
    monkeypatch.setattr(
        db, "get_task", lambda task_id: _sample_task(summary="s" * 20000)
    )
    app = FastAPI()
    app.include_router(tasks_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1?include_content=false")

    assert resp.status_code == 200
    assert len(resp.json()["summary"]) == 12000


@pytest.mark.asyncio
async def test_get_task_include_content_false_cuts_before_multipart_marker(monkeypatch):
    """include_content=false：分P总结从 '# 分P总结' 标记处截断。"""
    monkeypatch.setattr(
        db,
        "get_task",
        lambda task_id: _sample_task(
            summary="# 总览开头\n# 分P总结\n## P1: 第一节\npart one\n## P2: 第二节\npart two"
        ),
    )
    app = FastAPI()
    app.include_router(tasks_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1?include_content=false")

    assert resp.status_code == 200
    assert resp.json()["summary"] == "# 总览开头"
