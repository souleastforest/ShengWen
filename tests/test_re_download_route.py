"""POST /tasks/{task_id}/re-download 校验矩阵与派发 payload 单测。"""

from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from src.main.python.sheng_wen.infra.api.routes import deps
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.task_updater import update_and_notify

NOW = datetime.now(timezone.utc)


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


class FakeDownloaderWorker:
    def __init__(self):
        self.added = []

    async def add_task(self, payload):
        self.added.append(payload)


class FakeUpdater:
    """拦截 update_and_notify：记录调用并同步更新假 db（与真实语义一致）。"""

    def __init__(self, db):
        self.db = db
        self.calls = []

    async def __call__(self, task_id, updates):
        self.calls.append((task_id, dict(updates)))
        return self.db.update_task(task_id, updates)


def base_task(tid, status="COMPLETED", transcript="hello", video_url=None):
    return {
        "id": tid,
        "video_url": video_url or f"https://example.com/{tid}",
        "status": status,
        "created_at": NOW,
        "latest_modified_at": NOW,
        "progress": 100.0,
        "title": f"task {tid}",
        "transcript": transcript,
        "summary": "summary",
        "error_message": None,
        "audio_downloaded": False,
        "audio_missing_reason": "reclaimed",
        "summary_mode": "auto",
    }


@pytest.fixture
def env(monkeypatch):
    """构造独立 app + 假 db/worker/updater。"""
    db = FakeDB([])
    worker = FakeDownloaderWorker()
    updater = FakeUpdater(db)

    app = FastAPI()

    async def fake_worker_factory():
        return worker

    app.state.get_downloader_worker = fake_worker_factory
    app.include_router(tasks_router)

    import src.main.python.sheng_wen.infra.api.routes.tasks as tasks_module

    monkeypatch.setattr(tasks_module, "db", db)
    # 路由函数体内 `from ..task_updater import update_and_notify` 在调用期解析，
    # 替换 task_updater 模块的引用即可拦截。
    monkeypatch.setattr(update_and_notify.__module__ + ".update_and_notify", updater)
    return {
        "app": app,
        "db": db,
        "worker": worker,
        "updater": updater,
    }


@pytest.mark.asyncio
async def test_re_download_404_when_task_missing(env):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/ghost/re-download")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_re_download_400_for_local_file_task(env, monkeypatch):
    env["db"].tasks["t1"] = base_task(
        "t1", video_url="file:///tmp/x.mp4", transcript=None
    )
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-download")
    assert resp.status_code == 400
    assert env["worker"].added == []


@pytest.mark.asyncio
async def test_re_download_409_without_transcript(env, monkeypatch):
    env["db"].tasks["t1"] = base_task("t1", transcript=None)
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-download")
    assert resp.status_code == 409
    assert env["worker"].added == []


@pytest.mark.asyncio
async def test_re_download_409_when_local_media_exists(env, monkeypatch):
    env["db"].tasks["t1"] = base_task("t1")
    monkeypatch.setattr(
        deps, "_resolve_local_media_file", lambda tid, task: "/tmp/real.mp3"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-download")
    assert resp.status_code == 409
    assert env["worker"].added == []


@pytest.mark.asyncio
async def test_re_download_ok_dispatches_re_download_only(env, monkeypatch):
    """合法请求：状态置 DOWNLOADING + audio 标记清空，派发 re_download_only payload。"""
    env["db"].tasks["t1"] = base_task("t1", status="FAILED")
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-download")
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "DOWNLOADING"
    assert body["audio_downloaded"] is False
    assert body["audio_missing_reason"] is None

    assert len(env["worker"].added) == 1
    payload = env["worker"].added[0]
    assert payload["task_id"] == "t1"
    assert payload["video_url"] == "https://example.com/t1"
    assert payload["quality"] == "audio_only"
    assert payload["re_download_only"] is True
    assert payload["restore_status"] == "FAILED"
    assert payload["summary_mode"] == "auto"
