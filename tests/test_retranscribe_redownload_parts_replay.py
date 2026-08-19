"""TDD: re-transcribe / re-download 分P回放（附带项 6）。

根因（2026-08-19 缺陷附带）：多P任务重转录/重下载时派发 payload 不带
bilibili_parts/multipart_batch，下载器按单P语义处理 → 重转录/重下载退化为
只处理第一个分P。

修复契约：
⑤ re-transcribe：任务有 task_parts 记录 → 重置分P为 PENDING 并派发 payload
   恢复原 bilibili_parts（merge 全部分P）+ multipart_batch，完整重跑分P流水线；
   无 parts 记录 → 保持原单P payload（回归，不误伤）。
re-download：任务有 task_parts 记录 → 按分P逐个派发（multipart_part +
   bilibili_parts merge 单分P + re_download_only），不退化只下载第一P；
   无 parts 记录 → 保持原单P payload（回归）。

注：re-download 不派发 multipart_batch——父任务 _process_bilibili_multipart
会等待分P COMPLETED 后重跑合并/总结流水线，而 re-download 只恢复音频文件、
不重转录，父任务将被误判"所有选中的分P均处理失败"。
"""

from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from src.main.python.sheng_wen.infra.api.routes import deps
from src.main.python.sheng_wen.infra.api.routes import tasks as tasks_module
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.task_updater import update_and_notify

BILIBILI_URL = "https://www.bilibili.com/video/BV1eh411h7xH"
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
        "generate_topic": True,
    }


def fake_parts():
    return [
        {
            "task_id": "t1",
            "part_index": 0,
            "cid": 1001,
            "title": "P1",
            "duration": 60,
            "status": "COMPLETED",
            "progress": 100.0,
            "transcript": "P1 转录",
        },
        {
            "task_id": "t1",
            "part_index": 1,
            "cid": 1002,
            "title": "P2",
            "duration": 60,
            "status": "COMPLETED",
            "progress": 100.0,
            "transcript": "P2 转录",
        },
    ]


def fake_parts_info(count: int = 1) -> list[dict]:
    """探针 parts_info 桩：count=1 单P；count>1 多分P。"""
    return [
        {
            "index": i,
            "cid": 1001 + i,
            "title": f"P{i + 1}",
            "duration": 60,
        }
        for i in range(count)
    ]


@pytest.fixture
def env(monkeypatch):
    """构造独立 app + 假 db/worker/updater + task_parts 打桩。"""
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
    # task_parts 打桩：不触碰真实 DB
    monkeypatch.setattr(tasks_module, "get_task_parts", lambda tid: [])
    reset_calls = []

    def fake_reset(task_id, indices):
        reset_calls.append((task_id, sorted(indices)))
        return sorted(indices)

    monkeypatch.setattr(tasks_module, "reset_failed_parts", fake_reset)
    return {
        "app": app,
        "db": db,
        "worker": worker,
        "updater": updater,
        "reset_calls": reset_calls,
    }


@pytest.mark.asyncio
async def test_retranscribe_multipart_replays_parts(env, monkeypatch):
    """⑤ 多P任务 re-transcribe：重置分P + 派发 payload 恢复 bilibili_parts/multipart_batch。"""
    env["db"].tasks["t1"] = base_task("t1", video_url=BILIBILI_URL)
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)
    monkeypatch.setattr(tasks_module, "get_task_parts", lambda tid: fake_parts())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/tasks/t1/re-transcribe", json={"summary_mode": "none"}
        )

    assert resp.status_code == 200
    # 分P已重置为 PENDING（重转录需重跑全部部分，否则 COMPLETED 分P会被跳过）
    assert env["reset_calls"] == [("t1", [0, 1])]

    assert len(env["worker"].added) == 1
    payload = env["worker"].added[0]
    assert payload["task_id"] == "t1"
    assert payload["video_url"] == BILIBILI_URL
    assert payload["bilibili_parts"] == {"mode": "merge", "indices": [0, 1]}
    assert payload["multipart_batch"] is True
    assert payload["summary_mode"] == "none"


@pytest.mark.asyncio
async def test_retranscribe_without_parts_keeps_single_payload(env, monkeypatch):
    """⑤ 回归：无 task_parts 记录 + 单P URL → 保持原单P payload，不带 bilibili_parts。"""
    env["db"].tasks["t1"] = base_task("t1", video_url=BILIBILI_URL)
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)

    # P1-C 探针桩：URL 确认为单P（不得误拒正常单P任务）
    async def fake_probe(url):
        return ("单P标题", fake_parts_info(1))

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/tasks/t1/re-transcribe", json={"summary_mode": "none"}
        )

    assert resp.status_code == 200
    assert env["reset_calls"] == []
    assert len(env["worker"].added) == 1
    payload = env["worker"].added[0]
    assert "bilibili_parts" not in payload
    assert "multipart_batch" not in payload


@pytest.mark.asyncio
async def test_retranscribe_separate_child_multipart_url_rejected(env, monkeypatch):
    """P1-C：separate 拆分子任务（B 站 URL + 无 task_parts + 多分P）→ 409 拒绝，
    文案可行动，不派发任务、不改动任务状态。"""
    env["db"].tasks["t1"] = base_task("t1", video_url=BILIBILI_URL)
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)

    async def fake_probe(url):
        return ("多P标题", fake_parts_info(2))

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/tasks/t1/re-transcribe", json={"summary_mode": "none"}
        )

    assert resp.status_code == 409
    assert "拆分子任务" in resp.json()["detail"]
    assert "父任务" in resp.json()["detail"]
    assert env["worker"].added == []
    assert env["reset_calls"] == []
    # 未改动任务状态（拒绝发生在重置之前）
    assert env["db"].get_task("t1")["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_redownload_multipart_replays_per_part(env, monkeypatch):
    """附带：多P任务 re-download → 按分P逐个派发（不退化只下载第一P）。"""
    env["db"].tasks["t1"] = base_task("t1", video_url=BILIBILI_URL)
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)
    monkeypatch.setattr(tasks_module, "get_task_parts", lambda tid: fake_parts())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-download")

    assert resp.status_code == 200
    assert len(env["worker"].added) == 2
    for index, payload in enumerate(env["worker"].added):
        assert payload["task_id"] == "t1"
        assert payload["video_url"] == BILIBILI_URL
        assert payload["re_download_only"] is True
        assert payload["restore_status"] == "COMPLETED"
        assert payload["multipart_part"]["index"] == index
        assert payload["multipart_part"]["restore_status"] == "COMPLETED"
        assert payload["bilibili_parts"] == {"mode": "merge", "indices": [index]}
        # re-download 不派发 multipart_batch（父合并流水线会误判分P全部失败）
        assert "multipart_batch" not in payload


@pytest.mark.asyncio
async def test_redownload_without_parts_keeps_single_payload(env, monkeypatch):
    """附带：回归——无 task_parts 记录 + 单P URL → 保持原单P re_download payload。"""
    env["db"].tasks["t1"] = base_task("t1", video_url=BILIBILI_URL)
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)

    # P1-C 探针桩：URL 确认为单P（不得误拒正常单P任务）
    async def fake_probe(url):
        return ("单P标题", fake_parts_info(1))

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-download")

    assert resp.status_code == 200
    assert len(env["worker"].added) == 1
    payload = env["worker"].added[0]
    assert "bilibili_parts" not in payload
    assert "multipart_part" not in payload
    assert payload["re_download_only"] is True


@pytest.mark.asyncio
async def test_redownload_separate_child_multipart_url_rejected(env, monkeypatch):
    """P1-C：separate 拆分子任务（B 站 URL + 无 task_parts + 多分P）→ 409 拒绝，
    文案可行动，不派发任务、不改动任务状态。"""
    env["db"].tasks["t1"] = base_task("t1", video_url=BILIBILI_URL)
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)

    async def fake_probe(url):
        return ("多P标题", fake_parts_info(2))

    monkeypatch.setattr(tasks_module, "_get_bilibili_video_title_and_parts", fake_probe)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-download")

    assert resp.status_code == 409
    assert "拆分子任务" in resp.json()["detail"]
    assert "父任务" in resp.json()["detail"]
    assert env["worker"].added == []
    assert env["db"].get_task("t1")["status"] == "COMPLETED"
