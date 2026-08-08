"""StorageReclaimService 回收逻辑单测：队列顺序/保护期/在跑保护/两档优先级/字段标记/共享BV/孤儿/边界。"""

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.main.python.sheng_wen.config.settings import StorageConfig
from src.main.python.sheng_wen.domain.storage.service import StorageReclaimService

NOW = datetime.now(timezone.utc)
MB = 1024 * 1024


class FakeDB:
    def __init__(self, tasks):
        self.tasks = {str(t["id"]): dict(t) for t in tasks}
        self.updates = []

    def list_tasks(self):
        return [dict(t) for t in self.tasks.values()]

    def get_task(self, task_id):
        return self.tasks.get(task_id)

    def update_task(self, task_id, updates):
        task = self.tasks.get(task_id)
        if task:
            task.update(updates)
            self.updates.append((task_id, dict(updates)))
        return task


class FakeNotifier:
    def __init__(self):
        self.calls = []

    async def __call__(self, task_id, updates):
        self.calls.append((task_id, dict(updates)))


def make_snapshots(active=None, waiting=None):
    return [
        {
            "name": "VideoDownloaderWorker",
            "active_task_id": active,
            "waiting_task_ids": waiting or [],
            "queue_size": len(waiting or []),
        }
    ]


def touch(path, size=1024, mtime_offset_sec=30 * 86400):
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    old = time.time() - mtime_offset_sec
    os.utime(path, (old, old))
    return path


def task(
    tid,
    status="COMPLETED",
    age_hours=10 * 24,
    created_age_hours=20 * 24,
    video_url="",
    transcript=None,
):
    return {
        "id": tid,
        "video_url": video_url or f"https://example.com/{tid}",
        "status": status,
        "created_at": NOW - timedelta(hours=created_age_hours),
        "latest_modified_at": NOW - timedelta(hours=age_hours),
        "transcript": transcript,
        "audio_downloaded": None,
        "audio_missing_reason": None,
    }


@pytest.fixture
def make_service(tmp_path):
    def _make(db_tasks, **cfg_overrides):
        notifier = FakeNotifier()
        svc = StorageReclaimService(
            base_dir=str(tmp_path),
            storage_config=StorageConfig(**cfg_overrides),
            db=FakeDB(db_tasks),
            snapshots_provider=lambda: make_snapshots(),
            notifier=notifier,
        )
        return svc, notifier

    return _make


# ---- 队列顺序：FIFO by created_at ----


@pytest.mark.asyncio
async def test_reclaim_fifo_by_created_at(tmp_path, make_service):
    """两个资格任务按 created_at 升序回收，先回收更早创建的任务。"""
    base = str(tmp_path)
    # 上传任务媒体以 {task_id}{ext} 命名
    old_task_file = touch(f"{base}/task_old.mp3", size=2048)
    new_task_file = touch(f"{base}/task_new.mp3", size=2048)

    svc, _ = make_service(
        [
            task("task_new", created_age_hours=10),
            task("task_old", created_age_hours=40),
        ],
        max_total_mb=2048 / MB,  # 限额仅够删一个文件
        retention_completed_sec=0,
        retention_failed_sec=0,
        cleanup_interval_sec=600,
        base_dir="temp",
    )
    result = await svc.run_once()

    assert not os.path.exists(old_task_file)
    assert os.path.exists(new_task_file)
    assert result.reclaimed_files == 1
    assert result.over_limit is True


# ---- 保护期：未超过保留期的任务不回收 ----


@pytest.mark.asyncio
async def test_retention_period_respected(tmp_path, make_service):
    """COMPLETED 未过 retention_completed_sec 不回收；FAILED 过 retention_failed_sec 回收。"""
    base = str(tmp_path)
    recent_file = touch(f"{base}/task_recent.mp3", size=2048)
    failed_file = touch(f"{base}/task_failed.mp3", size=2048)

    svc, _ = make_service(
        [
            task("task_recent", status="COMPLETED", age_hours=1),  # 1h < 24h
            task("task_failed", status="FAILED", age_hours=10),  # 10h > 2h
        ],
        max_total_mb=2048 / MB,
        retention_completed_sec=86400,
        retention_failed_sec=7200,
    )
    await svc.run_once()

    assert os.path.exists(recent_file)  # 保护期内，保留
    assert not os.path.exists(failed_file)  # 已过 FAILED 保留期，回收


# ---- 在跑保护：worker active/waiting 中的任务不回收 ----


@pytest.mark.asyncio
async def test_inflight_tasks_protected(tmp_path, make_service):
    """任务虽过保留期，但在 4 worker 快照的 active/waiting 集合中则不回收。"""
    base = str(tmp_path)
    active_file = touch(f"{base}/task_active.mp3", size=2048)
    waiting_file = touch(f"{base}/task_waiting.mp3", size=2048)
    idle_file = touch(f"{base}/task_idle.mp3", size=2048)

    svc, _ = make_service(
        [
            task("task_active"),
            task("task_waiting"),
            task("task_idle"),
        ],
        max_total_mb=2048 / MB,
        retention_completed_sec=0,
        retention_failed_sec=0,
    )
    svc._snapshots_provider = lambda: make_snapshots(
        active="task_active", waiting=["task_waiting"]
    )
    await svc.run_once()

    assert os.path.exists(active_file)
    assert os.path.exists(waiting_file)
    assert not os.path.exists(idle_file)


# ---- 两档优先级：先媒体后中间产物 ----


@pytest.mark.asyncio
async def test_media_reclaimed_before_intermediates(tmp_path, make_service):
    """限额只够删媒体时，中间产物保留；媒体先删。"""
    base = str(tmp_path)
    media = touch(f"{base}/task_1.mp3", size=2048)
    intermediate = touch(f"{base}/task_1_subtitle.txt", size=2048)

    svc, notifier = make_service(
        [task("task_1")],
        max_total_mb=2048 / MB,  # 删一个 2048B 文件后即达限额
        retention_completed_sec=0,
        retention_failed_sec=0,
    )
    result = await svc.run_once()

    assert not os.path.exists(media)
    assert os.path.exists(intermediate)
    assert result.reclaimed_files == 1
    assert notifier.calls == [
        ("task_1", {"audio_downloaded": False, "audio_missing_reason": "reclaimed"})
    ]


@pytest.mark.asyncio
async def test_intermediates_reclaimed_after_media(tmp_path, make_service):
    """第一档删完仍超限时，继续删第二档中间产物。"""
    base = str(tmp_path)
    media = touch(f"{base}/task_1.mp3", size=1024)
    intermediate = touch(f"{base}/task_1_subtitle.txt", size=1024)

    svc, _ = make_service(
        [task("task_1")],
        max_total_mb=512 / MB,  # 删媒体后 usage=1024 仍 > 512，继续删中间产物
        retention_completed_sec=0,
        retention_failed_sec=0,
    )
    result = await svc.run_once()

    assert not os.path.exists(media)
    assert not os.path.exists(intermediate)
    assert result.reclaimed_files == 2


# ---- 字段标记：每任务仅标记一次 ----


@pytest.mark.asyncio
async def test_mark_reclaimed_once_per_task(tmp_path, make_service):
    """任务有多个媒体文件时，仅标记一次。"""
    base = str(tmp_path)
    touch(f"{base}/task_1.mp3", size=1024, mtime_offset_sec=40 * 86400)
    touch(f"{base}/task_1.mp4", size=1024, mtime_offset_sec=30 * 86400)

    svc, notifier = make_service(
        [task("task_1")],
        max_total_mb=0.0001,
        retention_completed_sec=0,
        retention_failed_sec=0,
    )
    await svc.run_once()

    assert len(notifier.calls) == 1
    assert notifier.calls[0][0] == "task_1"
    assert notifier.calls[0][1]["audio_downloaded"] is False
    assert notifier.calls[0][1]["audio_missing_reason"] == "reclaimed"


# ---- 共享 BV：引用计数 ----


@pytest.mark.asyncio
async def test_shared_bv_file_kept_when_other_ref_not_reclaimed(tmp_path, make_service):
    """两个任务共享 BV 文件，仅一个可回收时文件不删。"""
    base = str(tmp_path)
    bv_file = touch(f"{base}/BV1234567890.mp4", size=2048)
    bv_url = "https://www.bilibili.com/video/BV1234567890/"

    svc, _ = make_service(
        [
            task("task_a", video_url=bv_url),
            task("task_b", video_url=bv_url, status="PENDING"),  # 未终态，不在回收集合
        ],
        max_total_mb=1024 / MB,  # usage=2048 > limit=1024，触发回收
        retention_completed_sec=0,
        retention_failed_sec=0,
    )
    await svc.run_once()

    assert os.path.exists(bv_file)  # task_b 仍引用，不删


@pytest.mark.asyncio
async def test_shared_bv_file_deleted_when_all_refs_reclaimed(tmp_path, make_service):
    """两个任务共享 BV 文件，均在回收集合时删除。"""
    base = str(tmp_path)
    bv_file = touch(f"{base}/BV1234567890.mp4", size=2048)
    bv_url = "https://www.bilibili.com/video/BV1234567890/"

    svc, notifier = make_service(
        [
            task("task_a", video_url=bv_url, created_age_hours=40),
            task("task_b", video_url=bv_url, created_age_hours=30),
        ],
        max_total_mb=1024 / MB,  # usage=2048 > limit=1024，触发回收
        retention_completed_sec=0,
        retention_failed_sec=0,
    )
    await svc.run_once()

    assert not os.path.exists(bv_file)
    assert {tid for tid, _ in notifier.calls} == {"task_a", "task_b"}


# ---- 孤儿清扫 ----


@pytest.mark.asyncio
async def test_orphan_upload_temp_cleanup(tmp_path, make_service):
    """_temp.* 残留：无对应任务或终态任务且超期删除；非终态保留；近期保留。"""
    base = str(tmp_path)
    no_task = touch(f"{base}/ghost_temp.mp4", size=512)
    terminal_old = touch(f"{base}/task_term_temp.mp4", size=512)
    terminal_recent = touch(
        f"{base}/task_term_recent_temp.mp4", size=512, mtime_offset_sec=600
    )
    active = touch(f"{base}/task_active_temp.mp4", size=512)

    svc, _ = make_service(
        [
            task("task_term", status="FAILED", age_hours=10),
            task("task_term_recent", status="FAILED", age_hours=10),
            task("task_active", status="UPLOADING", age_hours=10),
        ],
        max_total_mb=1024,  # 远大于使用量，仅验证孤儿清扫
        retention_completed_sec=0,
        retention_failed_sec=7200,
    )
    result = await svc.run_once()

    assert not os.path.exists(no_task)  # 无对应任务 → 删
    assert not os.path.exists(terminal_old)  # 终态 + 超期 → 删
    assert os.path.exists(terminal_recent)  # 终态但未超期 → 保留
    assert os.path.exists(active)  # 任务非终态 → 保留
    assert result.orphan_files == 2


@pytest.mark.asyncio
async def test_orphan_part_cleanup(tmp_path, make_service):
    """.part/.ytdl 超 1h 删除，近期保留。"""
    base = str(tmp_path)
    old_part = touch(f"{base}/BV1234567890.part", size=512, mtime_offset_sec=7200)
    recent_part = touch(f"{base}/BV1234567891.part", size=512, mtime_offset_sec=600)
    old_ytdl = touch(f"{base}/BV1234567892.ytdl", size=512, mtime_offset_sec=7200)

    svc, _ = make_service(
        [],
        max_total_mb=1024,
        retention_completed_sec=0,
        retention_failed_sec=7200,
    )
    result = await svc.run_once()

    assert not os.path.exists(old_part)
    assert not os.path.exists(old_ytdl)
    assert os.path.exists(recent_part)
    assert result.orphan_files == 2


# ---- 边界：usage == limit 不回收；超限后删至 ≤ limit ----


@pytest.mark.asyncio
async def test_usage_at_limit_no_reclaim(tmp_path, make_service):
    """usage == limit 时不触发两档回收。"""
    base = str(tmp_path)
    media = touch(f"{base}/task_1.mp3", size=2048)
    intermediate = touch(f"{base}/task_1_summary.md", size=2048)

    svc, notifier = make_service(
        [task("task_1")],
        max_total_mb=4096 / MB,  # limit = 4096 == usage
        retention_completed_sec=0,
        retention_failed_sec=0,
    )
    result = await svc.run_once()

    assert os.path.exists(media)
    assert os.path.exists(intermediate)
    assert result.over_limit is False
    assert result.reclaimed_files == 0
    assert notifier.calls == []


@pytest.mark.asyncio
async def test_reclaim_stops_when_under_limit(tmp_path, make_service):
    """回收后 usage ≤ limit 即停止，不超额删除。"""
    base = str(tmp_path)
    media = touch(f"{base}/task_1.mp3", size=2048, mtime_offset_sec=40 * 86400)
    intermediate = touch(
        f"{base}/task_1_summary.md", size=2048, mtime_offset_sec=30 * 86400
    )

    svc, _ = make_service(
        [task("task_1")],
        max_total_mb=2048 / MB,  # 删一个 2048B 文件后 usage == limit
        retention_completed_sec=0,
        retention_failed_sec=0,
    )
    result = await svc.run_once()

    assert not os.path.exists(media)
    assert os.path.exists(intermediate)
    assert result.reclaimed_files == 1


# ---- 媒体解析：上传与 multipart ----


@pytest.mark.asyncio
async def test_upload_and_multipart_media_resolution(tmp_path, make_service):
    """上传任务媒体（{task_id}{ext}/.mp3/_temp{ext}）与 multipart 媒体（_p{N}.*）均被识别。"""
    base = str(tmp_path)
    touch(f"{base}/task_1.mp4", size=1024)
    touch(f"{base}/task_1.mp3", size=1024)
    touch(f"{base}/task_1_temp.mp4", size=1024)
    touch(f"{base}/task_1_p1.mp4", size=1024)
    touch(f"{base}/task_1_p2.mp3", size=1024)
    touch(f"{base}/task_1_p1_summary.md", size=1024)  # 中间产物，不是媒体

    svc, notifier = make_service(
        [task("task_1")],
        max_total_mb=0.0001,
        retention_completed_sec=0,
        retention_failed_sec=0,
    )
    result = await svc.run_once()

    # _temp.mp4 走孤儿清扫，其余 4 个媒体 + 1 个中间产物走两档回收
    assert result.orphan_files == 1
    assert result.reclaimed_files == 5
    assert not os.path.exists(f"{base}/task_1.mp4")
    assert not os.path.exists(f"{base}/task_1_p2.mp3")
    assert not os.path.exists(f"{base}/task_1_p1_summary.md")
    assert notifier.calls == [
        ("task_1", {"audio_downloaded": False, "audio_missing_reason": "reclaimed"})
    ]


# ---- subtitle_only：无媒体可删不标记 ----


@pytest.mark.asyncio
async def test_subtitle_only_task_not_marked(tmp_path, make_service):
    """字幕直取任务无媒体文件，回收时不标记 reclaimed。"""
    base = str(tmp_path)
    touch(f"{base}/task_1_subtitle.txt", size=2048)

    svc, notifier = make_service(
        [task("task_1")],
        max_total_mb=2048 / MB,
        retention_completed_sec=0,
        retention_failed_sec=0,
    )
    await svc.run_once()

    assert notifier.calls == []


# ---- 周期循环：首轮立即执行 + 可停止 ----


@pytest.mark.asyncio
async def test_reclaimer_loop_starts_and_stops(monkeypatch):
    """StorageReclaimerLoop start() 首轮立即 run_once，stop() 取消循环。"""
    import src.main.python.sheng_wen.domain.storage.reclaimer_loop as loop_module
    from src.main.python.sheng_wen.domain.storage.reclaimer_loop import (
        StorageReclaimerLoop,
    )

    runs = []

    class _FakeService:
        def __init__(self, repository=None):
            self.repository = repository

        async def run_once(self):
            runs.append(1)
            from src.main.python.sheng_wen.domain.storage.type import ReclaimResult

            return ReclaimResult()

    monkeypatch.setattr(loop_module, "StorageReclaimService", _FakeService)
    # 缩短轮询间隔便于测试
    monkeypatch.setattr(loop_module.config.storage, "cleanup_interval_sec", 60)

    loop = StorageReclaimerLoop()
    loop.start()
    assert loop.running
    # 等待首轮执行
    for _ in range(50):
        if runs:
            break
        await asyncio.sleep(0.02)
    assert runs == [1]

    await loop.stop()
    assert not loop.running
    assert not loop._task
