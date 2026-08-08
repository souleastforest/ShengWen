"""StorageReclaimService 基础行为单测（run_once/infer/backfill 的轻量覆盖）。"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from src.main.python.sheng_wen.config.settings import StorageConfig
from src.main.python.sheng_wen.domain.storage.repository import TempFileRepository
from src.main.python.sheng_wen.domain.storage.service import StorageReclaimService

NOW = datetime.now(timezone.utc)


class FakeDB:
    def __init__(self, tasks):
        self.tasks = {str(t["id"]): dict(t) for t in tasks}

    def list_tasks(self):
        return [dict(t) for t in self.tasks.values()]

    def update_task(self, task_id, updates):
        task = self.tasks.get(task_id)
        if task:
            task.update(updates)
        return task


class FakeNotifier:
    def __init__(self):
        self.calls = []

    async def __call__(self, task_id, updates):
        self.calls.append((task_id, dict(updates)))


def task(tid, status="COMPLETED", video_url=None, transcript=None):
    return {
        "id": tid,
        "video_url": video_url or f"https://example.com/{tid}",
        "status": status,
        "created_at": NOW - timedelta(days=5),
        "latest_modified_at": NOW - timedelta(days=4),
        "transcript": transcript,
        "audio_downloaded": None,
        "audio_missing_reason": None,
    }


@pytest.mark.asyncio
async def test_run_once_under_limit_no_reclaim(tmp_path):
    """使用量未超限时不触发两档回收（孤儿清扫不受影响）。"""
    base = str(tmp_path)
    media = os.path.join(base, "task_1.mp3")
    with open(media, "wb") as f:
        f.write(b"x" * 1024)

    notifier = FakeNotifier()
    svc = StorageReclaimService(
        base_dir=base,
        storage_config=StorageConfig(
            max_total_mb=10240, retention_completed_sec=0, retention_failed_sec=0
        ),
        db=FakeDB([task("task_1")]),
        snapshots_provider=lambda: [],
        notifier=notifier,
    )
    result = await svc.run_once()

    assert result.over_limit is False
    assert result.reclaimed_files == 0
    assert os.path.exists(media)
    assert notifier.calls == []


@pytest.mark.asyncio
async def test_run_once_over_limit_reclaims_and_marks(tmp_path):
    """超限时按任务文件组回收媒体并标记 reclaimed。"""
    base = str(tmp_path)
    media = os.path.join(base, "task_1.mp3")
    with open(media, "wb") as f:
        f.write(b"x" * 2048)

    notifier = FakeNotifier()
    svc = StorageReclaimService(
        base_dir=base,
        storage_config=StorageConfig(
            max_total_mb=1024 / (1024 * 1024),
            retention_completed_sec=0,
            retention_failed_sec=0,
        ),
        db=FakeDB([task("task_1")]),
        snapshots_provider=lambda: [],
        notifier=notifier,
    )
    result = await svc.run_once()

    assert result.over_limit is True
    assert result.reclaimed_files == 1
    assert result.marked_tasks == 1
    assert not os.path.exists(media)
    assert notifier.calls == [
        ("task_1", {"audio_downloaded": False, "audio_missing_reason": "reclaimed"})
    ]


@pytest.mark.asyncio
async def test_infer_audio_status_branches(tmp_path):
    """infer_audio_status 三分支：有媒体 / subtitle_only / reclaimed。"""
    base = str(tmp_path)
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "task_a.mp3"), "wb") as f:
        f.write(b"x")
    with open(os.path.join(base, "task_b_subtitle.txt"), "w", encoding="utf-8") as f:
        f.write("hi")

    notifier = FakeNotifier()
    svc = StorageReclaimService(
        base_dir=base,
        storage_config=StorageConfig(),
        db=FakeDB([]),
        notifier=notifier,
    )
    assert await svc.infer_audio_status(task("task_a")) == (True, None)
    assert await svc.infer_audio_status(task("task_b")) == (False, "subtitle_only")
    assert await svc.infer_audio_status(task("task_c", transcript="x")) == (
        False,
        "reclaimed",
    )
    assert await svc.infer_audio_status(task("task_d")) == (None, None)


@pytest.mark.asyncio
async def test_backfill_legacy_skips_non_terminal(tmp_path):
    """backfill 仅回填终态任务。"""
    base = str(tmp_path)
    with open(os.path.join(base, "task_1.mp3"), "wb") as f:
        f.write(b"x")

    db = FakeDB(
        [
            task("task_1", status="COMPLETED"),
            task("task_2", status="PENDING"),
        ]
    )
    svc = StorageReclaimService(
        base_dir=base,
        storage_config=StorageConfig(),
        db=db,
    )
    updated = await svc.backfill_legacy()
    assert updated == 1
    assert db.tasks["task_1"]["audio_downloaded"] is True
    assert db.tasks["task_2"].get("audio_downloaded") is None


@pytest.mark.asyncio
async def test_bilibili_multipart_media_files_resolved(tmp_path):
    """B站 multipart 任务（video_url 含 BV）的分P文件必须被解析为媒体文件（回归: multipart 匹配误放 else 分支）。"""
    base = str(tmp_path)
    task_id = "7a65baa8-2bb1-43d0-b6b1-7108af8f3db6"
    # 任务 ID 命名分P + BV 命名分P 各一个
    with open(os.path.join(base, f"{task_id}_p1.mp4"), "wb") as f:
        f.write(b"a" * 10)
    with open(os.path.join(base, f"{task_id}_p2.mp3"), "wb") as f:
        f.write(b"a" * 10)
    with open(os.path.join(base, "BV1hK4y1S7hz_p3.mp4"), "wb") as f:
        f.write(b"a" * 10)

    repo = TempFileRepository()
    files = await repo.scan(base)
    file_index = {f.path for f in files}

    media = await repo.resolve_task_media_files(
        task_id,
        "https://www.bilibili.com/video/BV1hK4y1S7hz",
        file_index,
        base,
    )
    names = sorted(os.path.basename(p) for p in media)
    assert f"{task_id}_p1.mp4" in names, names
    assert f"{task_id}_p2.mp3" in names, names
    assert "BV1hK4y1S7hz_p3.mp4" in names, names


@pytest.mark.asyncio
async def test_upload_task_media_files_resolved(tmp_path):
    """上传任务（无 BV）的媒体文件解析不受影响。"""
    base = str(tmp_path)
    task_id = "task_1"
    with open(os.path.join(base, f"{task_id}.mp3"), "wb") as f:
        f.write(b"a")
    repo = TempFileRepository()
    files = await repo.scan(base)
    media = await repo.resolve_task_media_files(task_id, "file://local/x.mp3", {f.path for f in files}, base)
    assert any(os.path.basename(p) == "task_1.mp3" for p in media)


@pytest.mark.asyncio
async def test_orphan_media_files_without_tasks(tmp_path):
    """无任务引用的媒体文件（任务已删除的 UUID 文件、无任务引用的 BV 文件、BV 分P孤儿）应被列出。"""
    base = str(tmp_path)
    orphan_uuid = "bce81e4f-bfb3-48a3-8db5-b1fa31e63b8d"
    with open(os.path.join(base, f"{orphan_uuid}.mp3"), "wb") as f:
        f.write(b"a" * 100)
    with open(os.path.join(base, "BV1OrphanFile.mp4"), "wb") as f:
        f.write(b"a" * 100)
    with open(os.path.join(base, "BV1OrphanFile_p1.mp4"), "wb") as f:
        f.write(b"a" * 100)
    # 有任务引用的 BV 文件不应是孤儿
    with open(os.path.join(base, "BV1InUse.mp4"), "wb") as f:
        f.write(b"a" * 100)
    # 有任务的分P文件不应是孤儿
    with open(os.path.join(base, "task_1_p1.mp4"), "wb") as f:
        f.write(b"a" * 100)

    repo = TempFileRepository()
    tasks = [
        {"id": "task_1", "video_url": "https://www.bilibili.com/video/BV1InUse", "status": "COMPLETED"},
        {"id": "task_2", "video_url": "https://www.bilibili.com/video/BV1OrphanFile", "status": "PENDING"},
    ]
    orphans = await repo.list_orphans(
        base, tasks, terminal_task_ids={"task_1"}, retention_failed_sec=0
    )
    names = sorted(os.path.basename(o.path) for o in orphans)
    # BV1OrphanFile 被 task_2 引用（PENDING 也算引用，防误删）→ 不回收（含分P）
    assert f"{orphan_uuid}.mp3" in names, names
    assert "BV1OrphanFile.mp4" not in names, names
    assert "BV1OrphanFile_p1.mp4" not in names, names
    assert "BV1InUse.mp4" not in names
    assert "task_1_p1.mp4" not in names
