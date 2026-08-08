"""infer_audio_status 三分支推断 + backfill_legacy 不广播单测。"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from src.main.python.sheng_wen.config.settings import StorageConfig
from src.main.python.sheng_wen.domain.storage.service import StorageReclaimService

NOW = datetime.now(timezone.utc)


class FakeDB:
    def __init__(self, tasks):
        self.tasks = {str(t["id"]): dict(t) for t in tasks}
        self.updates = []
        self.calls = []

    def list_tasks(self):
        return [dict(t) for t in self.tasks.values()]

    def update_task(self, task_id, updates):
        self.calls.append(("update_task", task_id, dict(updates)))
        task = self.tasks.get(task_id)
        if task:
            task.update(updates)
            self.updates.append((task_id, dict(updates)))
        return task


class RaisingNotifier:
    """backfill 不应触发广播；一旦被调用立即失败。"""

    async def __call__(self, task_id, updates):
        raise AssertionError(f"backfill 不应广播: {task_id} {updates}")


def task(tid, status="COMPLETED", transcript=None, audio_downloaded=None):
    return {
        "id": tid,
        "video_url": f"https://example.com/{tid}",
        "status": status,
        "created_at": NOW - timedelta(days=3),
        "latest_modified_at": NOW - timedelta(days=2),
        "transcript": transcript,
        "audio_downloaded": audio_downloaded,
        "audio_missing_reason": None,
    }


def make_service(tmp_path, db):
    return StorageReclaimService(
        base_dir=str(tmp_path),
        storage_config=StorageConfig(
            max_total_mb=10240,
            retention_completed_sec=86400,
            retention_failed_sec=7200,
        ),
        db=db,
        notifier=RaisingNotifier(),
    )


def touch(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---- infer 三分支 ----


@pytest.mark.asyncio
async def test_infer_with_media_returns_true(tmp_path):
    """有媒体文件 → (True, None)。"""
    base = str(tmp_path)
    touch(f"{base}/task_1.mp3")
    svc = make_service(tmp_path, FakeDB([]))
    inferred, reason = await svc.infer_audio_status(task("task_1"))
    assert inferred is True
    assert reason is None


@pytest.mark.asyncio
async def test_infer_with_bilibili_media_returns_true(tmp_path):
    """B 站任务有 BV 媒体文件 → (True, None)。"""
    base = str(tmp_path)
    touch(f"{base}/BV1234567890.mp4")
    t = task("task_1")
    t["video_url"] = "https://www.bilibili.com/video/BV1234567890/"
    svc = make_service(tmp_path, FakeDB([]))
    inferred, reason = await svc.infer_audio_status(t)
    assert inferred is True
    assert reason is None


@pytest.mark.asyncio
async def test_infer_subtitle_only(tmp_path):
    """无媒体但有 subtitle.txt → (False, subtitle_only)。"""
    base = str(tmp_path)
    touch(f"{base}/task_1_subtitle.txt")
    svc = make_service(tmp_path, FakeDB([]))
    inferred, reason = await svc.infer_audio_status(task("task_1", transcript="hello"))
    assert inferred is False
    assert reason == "subtitle_only"


@pytest.mark.asyncio
async def test_infer_reclaimed(tmp_path):
    """无媒体、无 subtitle.txt 但有 transcript → (False, reclaimed)。"""
    svc = make_service(tmp_path, FakeDB([]))
    inferred, reason = await svc.infer_audio_status(task("task_1", transcript="hello"))
    assert inferred is False
    assert reason == "reclaimed"


@pytest.mark.asyncio
async def test_infer_unknown(tmp_path):
    """无媒体、无字幕文件、无 transcript → (None, None)。"""
    svc = make_service(tmp_path, FakeDB([]))
    inferred, reason = await svc.infer_audio_status(task("task_1"))
    assert inferred is None
    assert reason is None


# ---- backfill：不广播、仅 NULL + 终态 ----


@pytest.mark.asyncio
async def test_backfill_updates_only_null_terminal_tasks(tmp_path):
    """仅 audio_downloaded IS NULL 的终态任务被回填；不广播。"""
    base = str(tmp_path)
    touch(f"{base}/task_media.mp3")
    touch(f"{base}/task_sub_subtitle.txt")

    fake_db = FakeDB(
        [
            task("task_media", status="COMPLETED", transcript="x"),  # 有媒体 → True
            task(
                "task_sub", status="PARTIAL", transcript="x"
            ),  # 无媒体有字幕 → subtitle_only
            task("task_reclaimed", status="FAILED", transcript="x"),  # → reclaimed
            task(
                "task_already",
                status="COMPLETED",
                transcript="x",
                audio_downloaded=True,
            ),  # 已标记 → 跳过
            task("task_pending", status="PENDING", transcript="x"),  # 非终态 → 跳过
            task("task_unknown", status="COMPLETED"),  # 无法推断 → 保持 NULL
        ]
    )
    svc = make_service(tmp_path, fake_db)
    updated = await svc.backfill_legacy()

    assert updated == 3
    assert fake_db.tasks["task_media"]["audio_downloaded"] is True
    assert fake_db.tasks["task_media"]["audio_missing_reason"] is None
    assert fake_db.tasks["task_sub"]["audio_downloaded"] is False
    assert fake_db.tasks["task_sub"]["audio_missing_reason"] == "subtitle_only"
    assert fake_db.tasks["task_reclaimed"]["audio_downloaded"] is False
    assert fake_db.tasks["task_reclaimed"]["audio_missing_reason"] == "reclaimed"
    assert fake_db.tasks["task_already"]["audio_downloaded"] is True  # 未被覆盖
    assert fake_db.tasks["task_pending"]["audio_downloaded"] is None
    assert fake_db.tasks["task_unknown"]["audio_downloaded"] is None
    # 仅通过 db.update_task 直接写入，未走任何广播
    assert all(call[0] == "update_task" for call in fake_db.calls)
