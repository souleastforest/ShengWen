"""TDD: GET /tasks/{id}/subtitles 端点 + include_content 携带/剥离 transcript_segments。

契约（plan step 5-6）：
- GET /tasks/{id}/subtitles?format=srt|vtt：format 用 Literal 校验（非法 422）；
  无 transcript 且无 segments → 404；优先级 segments > parse_hhmmss_transcript 回退；
  PlainTextResponse 返回；
- include_content=true：transcript_segments 解析为列表；false 与列表端点剥离；
- re_transcribe reset_data 带 "transcript_segments": None。
"""

import httpx
import pytest
from fastapi import FastAPI

from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.transcriber.type import segments_to_json

SEGMENTS_JSON = segments_to_json(
    [
        {"start": 0.0, "end": 3.0, "text": "你好"},
        {"start": 3.0, "end": 6.0, "text": "世界", "speaker_id": "A"},
    ]
)


def _sample_task(**overrides) -> dict:
    task = {
        "id": "test-task-1",
        "video_url": "https://example.com/video.mp4",
        "status": "COMPLETED",
        "created_at": "2026-04-07T00:00:00Z",
        "latest_modified_at": "2026-04-07T00:00:00Z",
        "progress": 1.0,
        "title": "Test Task",
        "transcript": "000000第一段\n000010第二段",
        "summary": "总结",
        "error_message": None,
        "audio_duration": 100.0,
        "transcription_time": 10.0,
        "topic": None,
        "author_name": None,
        "author_url": None,
        "summary_mode": "none",
        "summary_chunk_total": None,
        "summary_chunk_done": None,
        "summary_meta": None,
        "transcript_segments": SEGMENTS_JSON,
    }
    task.update(overrides)
    return task


@pytest.fixture
def app(monkeypatch):
    """独立 app + 打桩：db 返回样例任务、_with_part_stats 恒等（避免触碰真实 task_parts 表）。"""
    import src.main.python.sheng_wen.infra.api.routes.tasks as tasks_module

    from src.main.python.sheng_wen.db import db

    monkeypatch.setattr(db, "get_task", lambda task_id: _sample_task())
    monkeypatch.setattr(tasks_module, "_with_part_stats", lambda task: task)
    app = FastAPI()
    app.include_router(tasks_router)
    return app


# ---- include_content 携带/剥离 ---------------------------------------------


@pytest.mark.asyncio
async def test_get_task_include_content_true_returns_segments_list(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1")

    assert resp.status_code == 200
    segments = resp.json()["transcript_segments"]
    assert segments == [
        {"start": 0.0, "end": 3.0, "text": "你好", "speaker_id": None},
        {"start": 3.0, "end": 6.0, "text": "世界", "speaker_id": "A"},
    ]


@pytest.mark.asyncio
async def test_get_task_include_content_false_strips_segments(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1?include_content=false")

    assert resp.status_code == 200
    assert resp.json()["transcript_segments"] is None


@pytest.mark.asyncio
async def test_get_task_no_segments_returns_none(app, monkeypatch):
    from src.main.python.sheng_wen.db import db

    monkeypatch.setattr(
        db, "get_task", lambda task_id: _sample_task(transcript_segments=None)
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1")

    assert resp.status_code == 200
    assert resp.json()["transcript_segments"] is None


@pytest.mark.asyncio
async def test_list_tasks_strips_segments(app, monkeypatch):
    from src.main.python.sheng_wen.db import db

    monkeypatch.setattr(db, "list_tasks", lambda: [_sample_task()])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/")

    assert resp.status_code == 200
    assert resp.json()[0]["transcript_segments"] is None
    assert resp.json()[0]["transcript"] is None


# ---- GET /tasks/{id}/subtitles ---------------------------------------------


@pytest.mark.asyncio
async def test_subtitles_srt_200(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1/subtitles?format=srt")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "00:00:00,000 --> 00:00:03,000" in body
    assert "00:00:03,000 --> 00:00:06,000" in body
    assert "你好" in body
    assert "[Speaker A] 世界" in body
    assert "WEBVTT" not in body


@pytest.mark.asyncio
async def test_subtitles_vtt_200(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1/subtitles?format=vtt")

    assert resp.status_code == 200
    body = resp.text
    assert body.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:03.000" in body


@pytest.mark.asyncio
async def test_subtitles_default_format_is_srt(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1/subtitles")

    assert resp.status_code == 200
    assert "00:00:00,000 --> 00:00:03,000" in resp.text


@pytest.mark.asyncio
async def test_subtitles_invalid_format_422(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1/subtitles?format=ass")

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_subtitles_no_content_404(app, monkeypatch):
    from src.main.python.sheng_wen.db import db

    monkeypatch.setattr(
        db,
        "get_task",
        lambda task_id: _sample_task(transcript=None, transcript_segments=None),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1/subtitles")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_subtitles_task_not_found_404(app, monkeypatch):
    from src.main.python.sheng_wen.db import db

    monkeypatch.setattr(db, "get_task", lambda task_id: None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/missing/subtitles")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_subtitles_legacy_fallback_parse_hhmmss(app, monkeypatch):
    """存量任务无 segments → HHMMSS 行解析回退（end 用下行 start、末行 +3）。"""
    from src.main.python.sheng_wen.db import db

    monkeypatch.setattr(
        db,
        "get_task",
        lambda task_id: _sample_task(
            transcript_segments=None, transcript="000000第一段\n000010第二段"
        ),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1/subtitles?format=srt")

    assert resp.status_code == 200
    body = resp.text
    assert "00:00:00,000 --> 00:00:10,000" in body
    assert "00:00:10,000 --> 00:00:13,000" in body
    assert "第一段" in body
    assert "第二段" in body


@pytest.mark.asyncio
async def test_subtitles_segments_prefer_over_transcript(app, monkeypatch):
    """优先级：有 segments 时不走 HHMMSS 回退（segments 时间轴为准）。"""
    from src.main.python.sheng_wen.db import db

    monkeypatch.setattr(
        db,
        "get_task",
        lambda task_id: _sample_task(
            transcript_segments=segments_to_json(
                [{"start": 100.0, "end": 105.0, "text": "来自segments"}]
            ),
            transcript="000000来自transcript",
        ),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/tasks/test-task-1/subtitles?format=srt")

    assert resp.status_code == 200
    body = resp.text
    assert "00:01:40,000 --> 00:01:45,000" in body
    assert "来自segments" in body
    assert "来自transcript" not in body


# ---- re_transcribe 重置 -----------------------------------------------------


@pytest.mark.asyncio
async def test_re_transcribe_resets_segments(monkeypatch):
    """re_transcribe reset_data 带 transcript_segments: None。"""
    import src.main.python.sheng_wen.infra.api.routes.tasks as tasks_module
    from src.main.python.sheng_wen.infra.api.routes import deps
    from src.main.python.sheng_wen.db import db

    app = FastAPI()
    app.include_router(tasks_router)

    task = _sample_task()
    monkeypatch.setattr(db, "get_task", lambda task_id: task)
    monkeypatch.setattr(tasks_module, "_with_part_stats", lambda t: t)
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)
    monkeypatch.setattr(tasks_module, "_is_bilibili_multipart_url", lambda url: False)
    monkeypatch.setattr(tasks_module, "get_task_parts", lambda tid: [])
    monkeypatch.setattr(tasks_module, "reset_failed_parts", lambda tid, idx: [])

    reset_calls: list[tuple[str, dict]] = []

    async def fake_updater(task_id, updates):
        reset_calls.append((task_id, updates))
        task.update(updates)
        return task

    # 路由函数体内 `from ..task_updater import update_and_notify` 在调用期解析，
    # 替换 task_updater 模块的引用即可拦截（同 test_retranscribe_* 模式）。
    from src.main.python.sheng_wen.task_updater import update_and_notify

    monkeypatch.setattr(
        update_and_notify.__module__ + ".update_and_notify", fake_updater
    )

    async def fake_worker_factory():
        return SimpleAsyncWorker()

    class SimpleAsyncWorker:
        async def add_task(self, payload):
            pass

    app.state.get_downloader_worker = fake_worker_factory

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/tasks/test-task-1/re-transcribe")

    assert resp.status_code == 200
    reset_updates = dict(reset_calls[0][1])
    assert "transcript_segments" in reset_updates
    assert reset_updates["transcript_segments"] is None
