"""TDD（红→绿）：transcript_segments 线格式契约统一（对抗评审 3 blockers 后端部分）。

根因：仅 GET /tasks/{id}?include_content=true 经 _segments_list 解析为数组；
其余交付 DB 原始 JSON 字符串的路径——response_model=Task 端点（PATCH /
re-summarize / retry-failed-parts / re-download / resolve-author）、WS
task_update 广播、GET /tasks/{id}/parts/{part_index} 详情——会让 Pydantic
Optional[list[SegmentOut]] 校验失败（HTTP 500）或前端 segments.map() 崩溃。

契约（与 GET include_content=true 语义一致）：
- 上述所有端点返回 body.transcript_segments 为数组（非字符串）；
- WS 广播 payload.task.transcript_segments 为数组；
- parts 详情 body.transcript_segments 为数组；
- 空数组 / 坏 JSON → None（与"无 segments"同语义）。
"""

import json

import httpx
import pytest
from fastapi import FastAPI

import src.main.python.sheng_wen.infra.api.routes.tasks as tasks_module
import src.main.python.sheng_wen.infra.api.routes.websocket as ws_module
from src.main.python.sheng_wen.db import db
from src.main.python.sheng_wen.infra.api.routes import deps
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.task_updater import update_and_notify
from src.main.python.sheng_wen.transcriber.type import segments_to_json

SEGMENTS_JSON = segments_to_json(
    [
        {"start": 0.0, "end": 3.0, "text": "你好"},
        {"start": 3.0, "end": 6.0, "text": "世界", "speaker_id": "A"},
    ]
)
EXPECTED_SEGMENTS = [
    {"start": 0.0, "end": 3.0, "text": "你好", "speaker_id": None},
    {"start": 3.0, "end": 6.0, "text": "世界", "speaker_id": "A"},
]
# WS 广播 / parts 详情不经 Pydantic response_model：speaker_id=None 被
# Segment.to_dict() 省略（与 GET 解析的原始 to_dict 输出一致）。
RAW_EXPECTED_SEGMENTS = [
    {"start": 0.0, "end": 3.0, "text": "你好"},
    {"start": 3.0, "end": 6.0, "text": "世界", "speaker_id": "A"},
]


class FakeDB:
    def __init__(self, tasks):
        self.tasks = {str(t["id"]): dict(t) for t in tasks}

    def get_task(self, task_id):
        task = self.tasks.get(task_id)
        return dict(task) if task else None

    def update_task(self, task_id, updates):
        task = self.tasks.get(task_id)
        if task:
            task.update(updates)
        return self.get_task(task_id)


class FakeWorker:
    def __init__(self):
        self.added = []

    async def add_task(self, payload):
        self.added.append(payload)


class FakeUpdater:
    """拦截 update_and_notify：记录调用并同步更新假 db（与真实语义一致）。"""

    def __init__(self, fdb):
        self.fdb = fdb
        self.calls = []

    async def __call__(self, task_id, updates):
        self.calls.append((task_id, dict(updates)))
        return self.fdb.update_task(task_id, updates)


def base_task(tid="t1", **overrides) -> dict:
    task = {
        "id": tid,
        "video_url": f"https://example.com/{tid}",
        "status": "COMPLETED",
        "created_at": "2026-04-07T00:00:00Z",
        "latest_modified_at": "2026-04-07T00:00:00Z",
        "progress": 1.0,
        "title": f"task {tid}",
        "transcript": "000000第一段\n000010第二段",
        "summary": "summary",
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
def env(monkeypatch):
    """独立 app + 假 db/worker/updater；task_parts 相关调用全部打桩（禁止触真实库）。"""
    fdb = FakeDB([])
    downloader_worker = FakeWorker()
    llm_worker = FakeWorker()
    updater = FakeUpdater(fdb)

    app = FastAPI()

    async def downloader_factory():
        return downloader_worker

    async def llm_factory():
        return llm_worker

    app.state.get_downloader_worker = downloader_factory
    app.state.get_llm_worker = llm_factory
    app.include_router(tasks_router)

    monkeypatch.setattr(tasks_module, "db", fdb)
    monkeypatch.setattr(tasks_module, "_with_part_stats", lambda task: task)
    monkeypatch.setattr(tasks_module, "get_task_parts", lambda tid, *a, **k: [])
    monkeypatch.setattr(update_and_notify.__module__ + ".update_and_notify", updater)
    return {
        "app": app,
        "db": fdb,
        "downloader_worker": downloader_worker,
        "llm_worker": llm_worker,
        "updater": updater,
    }


async def _post(env, url, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        return await client.request("POST", url, **kwargs)


async def _patch(env, url, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        return await client.request("PATCH", url, **kwargs)


# ---- BLOCKER-1：response_model=Task 端点统一解析 ----------------------------------


@pytest.mark.asyncio
async def test_patch_task_returns_segments_list(env):
    """红：PATCH 更新 topic（任务已有落库 segments）→ 200 且 transcript_segments 为数组（现状 500）。"""
    env["db"].tasks["t1"] = base_task("t1")
    resp = await _patch(env, "/tasks/t1", json={"topic": "新主题"})
    assert resp.status_code == 200
    assert resp.json()["transcript_segments"] == EXPECTED_SEGMENTS


@pytest.mark.asyncio
async def test_retry_failed_parts_returns_segments_list(env, monkeypatch):
    """红：retry-failed-parts → 200 且 transcript_segments 为数组（现状 500）。"""
    env["db"].tasks["t1"] = base_task("t1")
    monkeypatch.setattr(
        tasks_module,
        "get_task_parts",
        lambda tid: [{"part_index": 0, "status": "FAILED"}],
    )
    monkeypatch.setattr(tasks_module, "reset_failed_parts", lambda tid, idx: list(idx))
    resp = await _post(env, "/tasks/t1/retry-failed-parts")
    assert resp.status_code == 200
    assert resp.json()["transcript_segments"] == EXPECTED_SEGMENTS


@pytest.mark.asyncio
async def test_re_summarize_returns_segments_list(env, monkeypatch):
    """红：re-summarize（multipart 分支）→ 200 且 transcript_segments 为数组（现状 500）。"""
    env["db"].tasks["t1"] = base_task("t1")
    # 走 multipart 分支（避免 temp 文件 IO），get_task_parts 非空
    monkeypatch.setattr(tasks_module, "get_task_parts", lambda tid: [{"part_index": 0}])
    resp = await _post(env, "/tasks/t1/re-summarize", json={"summary_mode": "standard"})
    assert resp.status_code == 200
    assert resp.json()["transcript_segments"] == EXPECTED_SEGMENTS


@pytest.mark.asyncio
async def test_re_download_returns_segments_list(env, monkeypatch):
    """红：re-download → 200 且 transcript_segments 为数组（现状 500）。"""
    env["db"].tasks["t1"] = base_task("t1")
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)
    resp = await _post(env, "/tasks/t1/re-download")
    assert resp.status_code == 200
    assert resp.json()["transcript_segments"] == EXPECTED_SEGMENTS


@pytest.mark.asyncio
async def test_resolve_author_returns_segments_list(env):
    """红：resolve-author（非 B 站 URL 直接返回任务）→ 200 且 transcript_segments 为数组（现状 500）。"""
    env["db"].tasks["t1"] = base_task("t1")
    resp = await _post(env, "/tasks/t1/resolve-author")
    assert resp.status_code == 200
    assert resp.json()["transcript_segments"] == EXPECTED_SEGMENTS


@pytest.mark.asyncio
async def test_patch_empty_or_bad_segments_maps_to_none(env):
    """空数组 / 坏 JSON segments 语义与 GET 一致：→ None（非字符串，不 500）。"""
    for raw in ("[]", "{bad json"):
        env["db"].tasks["t1"] = base_task("t1", transcript_segments=raw)
        resp = await _patch(env, "/tasks/t1", json={"topic": "x"})
        assert resp.status_code == 200
        assert resp.json()["transcript_segments"] is None


# ---- BLOCKER-2a：WS 广播 payload -------------------------------------------------


@pytest.mark.asyncio
async def test_ws_broadcast_parses_segments(monkeypatch):
    """红：WS task_update 广播 payload 的 transcript_segments 为数组（现状原始 JSON 字符串）。"""
    captured = []

    class FakeManager:
        async def broadcast(self, message):
            captured.append(json.loads(message))

    monkeypatch.setattr(ws_module, "manager", FakeManager())
    monkeypatch.setattr(db, "get_task", lambda tid: base_task("t1"))
    from src.main.python.sheng_wen.infra.api.routes.websocket import notify_task_update

    await notify_task_update("t1")

    assert len(captured) == 1
    assert captured[0]["type"] == "task_update"
    assert captured[0]["task"]["transcript_segments"] == RAW_EXPECTED_SEGMENTS


@pytest.mark.asyncio
async def test_ws_broadcast_bad_json_segments_none(monkeypatch):
    """坏 JSON segments 广播 → None（与 GET 语义一致）。"""
    captured = []

    class FakeManager:
        async def broadcast(self, message):
            captured.append(json.loads(message))

    monkeypatch.setattr(ws_module, "manager", FakeManager())
    monkeypatch.setattr(
        db, "get_task", lambda tid: base_task("t1", transcript_segments="{bad json")
    )
    from src.main.python.sheng_wen.infra.api.routes.websocket import notify_task_update

    await notify_task_update("t1")

    assert captured[0]["task"]["transcript_segments"] is None


# ---- BLOCKER-2b：parts 详情端点 ---------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_part_returns_segments_list(env, monkeypatch):
    """红：GET /tasks/{id}/parts/{part_index} 详情 → transcript_segments 为数组（现状字符串）。"""
    env["db"].tasks["t1"] = base_task("t1")
    part = {
        "task_id": "t1",
        "part_index": 0,
        "cid": 1001,
        "title": "P1",
        "duration": 60,
        "status": "COMPLETED",
        "progress": 100,
        "error_message": None,
        "transcript": "000000第一段",
        "transcript_segments": SEGMENTS_JSON,
        "summary": None,
        "updated_at": "2026-04-07T00:00:00Z",
    }
    monkeypatch.setattr(tasks_module, "get_task_part", lambda tid, idx: dict(part))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.get("/tasks/t1/parts/0")
    assert resp.status_code == 200
    assert resp.json()["transcript_segments"] == RAW_EXPECTED_SEGMENTS
