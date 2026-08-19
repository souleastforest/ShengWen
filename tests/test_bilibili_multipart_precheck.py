"""TDD: 下载前多P预检测（P0-1 / P0-2 / P0-4）。

根因（2026-08-19 缺陷，三代理侦查已闭环）：
- b23.tv 分享短链自带 p=1 → yt-dlp 判单视频只下 P1 → 下载器
  video_paths==1 校验通过 → 静默 COMPLETED 只总结第一讲；
- 叠加环境：DNS 故障 → 前端 /bilibili/video-info 降级 → 静默 submitTask()
  盲建单P任务；后端探针降级（tasks.py）→ 盲建单P任务；
- 第二个静默通道：字幕直取路径硬编码 part_index=0——无 parts 任务 + DNS 正常
  + 字幕可用时静默取第一P字幕，不经过任何多P校验。

修复契约：
① 无 parts 的多P B 站 URL：下载前 dry-run（extract_info download=False）
   检出 entries>1 → 任务 FAILED，错误文案含"检测到 N 个分P"与 b23.tv 指引，
   且不触发实际下载（extract_info download=True 不得被调用）；
② 单P URL / 带 bilibili_parts 配置 / multipart_part 子任务 / re_download_only
   不被误伤（预检测跳过）；
③ dry-run 网络异常（DNS/连接失败）→ warning 放行，走下载路径，由下载后
   video_paths!=1 校验兜底；
④ 字幕直取路径（summary_worker 存在）：无 parts 多P URL 在直取前拒绝，
   不静默取 part_index=0；
P0-4 下载后校验文案补充 b23.tv 短链 p=N 提示（dry-run 放行但实际下载
   仍检出多P的兜底场景）。

失败原因须与线上一致：静默只取第一P / 静默取第一P字幕。
"""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.downloader.video_downloader_worker import (
    VideoDownloaderWorker,
)

import src.main.python.sheng_wen.downloader.video_downloader_worker as dl_module

BILIBILI_URL = "https://www.bilibili.com/video/BV1eh411h7xH"
SHORT_LINK = "https://b23.tv/CD6M1qC"


def _save_task(task_id: str, url: str = BILIBILI_URL) -> None:
    db.save_task(
        task_id,
        {
            "id": task_id,
            "video_url": url,
            "status": TaskStatus.PENDING,
            "created_at": datetime.now(timezone.utc),
            "latest_modified_at": datetime.now(timezone.utc),
            "progress": 0.0,
            "title": "",
        },
    )


def _make_fake_ytdl(recorder: dict):
    """Fake yt_dlp.YoutubeDL：记录 dry-run 与真实下载的 extract_info 调用。

    recorder 字段：
    - dry_info: dry-run（download=False）返回的 info_dict
    - dry_raise: dry-run 时抛出的异常（模拟网络故障）
    - download_info: 真实下载（download=True）返回的 info_dict
    - path: prepare_filename 返回的已存在文件路径（下载成功判定）
    """

    class FakeYDL:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            if download:
                recorder["download_calls"].append(url)
                return recorder.get("download_info") or {"id": "p1"}
            recorder["dry_calls"].append(url)
            if recorder.get("dry_raise"):
                raise recorder["dry_raise"]
            return recorder.get("dry_info") or {}

        def prepare_filename(self, entry):
            return recorder.get("path") or "/nonexistent/out.mp4"

    return FakeYDL


def _make_downloader(tmp_path, summary_worker=None):
    downloader = VideoDownloaderWorker("test", summary_worker=summary_worker)
    downloader.output_dir = str(tmp_path)
    downloader._loop = asyncio.get_running_loop()
    return downloader


def _new_recorder(**overrides) -> dict:
    recorder = {
        "dry_info": {},
        "download_info": {},
        "path": "/nonexistent/out.mp4",
        "dry_calls": [],
        "download_calls": [],
    }
    recorder.update(overrides)
    return recorder


@pytest.mark.asyncio
async def test_multipart_without_parts_rejected_before_download(monkeypatch, tmp_path):
    """① 无 parts 多P URL（11 entries，对应王德峰 11 讲）→ FAILED + 指引；不触发下载。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    recorder = _new_recorder(
        dry_info={"entries": [{"id": f"p{i}"} for i in range(11)]},
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = _make_downloader(tmp_path, summary_worker=None)
    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.FAILED
    assert "检测到 11 个分P" in task["error_message"]
    assert "b23.tv" in task["error_message"]
    # 仅一次 dry-run，未触发实际下载（不浪费全量下载 ~800MB）
    assert recorder["dry_calls"] == [BILIBILI_URL]
    assert recorder["download_calls"] == []


@pytest.mark.asyncio
async def test_subtitle_path_multipart_rejected_without_part0_fetch(
    monkeypatch, tmp_path
):
    """④ 字幕直取路径：无 parts 多P URL 在直取前拒绝，不静默取 part_index=0。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    recorder = _new_recorder(
        dry_info={"entries": [{"id": "p0"}, {"id": "p1"}]},
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = _make_downloader(tmp_path, summary_worker=AsyncMock())

    def boom(video_url, sessdata):
        raise AssertionError("多P视频不得静默取第一个分P字幕")

    monkeypatch.setattr(downloader, "_try_extract_bilibili_subtitle", boom)

    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.FAILED
    assert "检测到 2 个分P" in task["error_message"]
    # 字幕直取未被调用，下载也未触发
    assert recorder["download_calls"] == []


@pytest.mark.asyncio
async def test_single_part_proceeds_to_download(monkeypatch, tmp_path):
    """② 单P URL（无 entries）→ 预检测放行，正常下载。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"fake-video")
    recorder = _new_recorder(
        dry_info={"id": "p1"},
        download_info={"id": "p1"},
        path=str(video_file),
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = _make_downloader(tmp_path, summary_worker=None)
    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.TRANSCRIBING
    assert recorder["dry_calls"] == [BILIBILI_URL]
    assert recorder["download_calls"] == [BILIBILI_URL]


@pytest.mark.asyncio
async def test_with_parts_config_skips_precheck(monkeypatch, tmp_path):
    """② 带 bilibili_parts 配置（用户已选择分P）→ 预检测跳过，正常下载。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"fake-video")
    recorder = _new_recorder(
        dry_info={"entries": [{"id": "p0"}, {"id": "p1"}]},
        download_info={"id": "p1"},
        path=str(video_file),
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = _make_downloader(tmp_path, summary_worker=None)
    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
            "bilibili_parts": {"mode": "merge", "indices": [0]},
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.TRANSCRIBING
    # 预检测跳过：无 dry-run 调用
    assert recorder["dry_calls"] == []
    assert recorder["download_calls"] == [BILIBILI_URL]


@pytest.mark.asyncio
async def test_precheck_skips_child_and_redownload_tasks(monkeypatch, tmp_path):
    """② multipart_part 子任务 / bilibili_batch_child / re_download_only / 非 B 站 → 跳过。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"fake-video")
    recorder = _new_recorder(
        dry_info={"entries": [{"id": "p0"}, {"id": "p1"}]},
        download_info={"id": "p1"},
        path=str(video_file),
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = _make_downloader(tmp_path, summary_worker=None)

    # multipart_part 子任务
    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
            "multipart_part": {"index": 0, "title": "P1"},
        }
    )
    await downloader._await_pending_updates(timeout=3.0)
    assert recorder["dry_calls"] == []

    # bilibili_batch_child
    recorder["dry_calls"] = []
    recorder["download_calls"] = []
    task_id2 = str(uuid.uuid4())
    _save_task(task_id2)
    downloader.process_task(
        {
            "task_id": task_id2,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
            "bilibili_batch_child": True,
            "bilibili_parts": {"mode": "merge", "indices": [0]},
        }
    )
    await downloader._await_pending_updates(timeout=3.0)
    assert recorder["dry_calls"] == []

    # re_download_only（维护流程，不阻断）
    recorder["dry_calls"] = []
    recorder["download_calls"] = []
    task_id3 = str(uuid.uuid4())
    _save_task(task_id3)
    downloader.process_task(
        {
            "task_id": task_id3,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
            "re_download_only": True,
            "restore_status": "COMPLETED",
        }
    )
    await downloader._await_pending_updates(timeout=3.0)
    assert recorder["dry_calls"] == []
    assert recorder["download_calls"] == [BILIBILI_URL]


@pytest.mark.asyncio
async def test_dry_run_network_error_proceeds_to_download(monkeypatch, tmp_path):
    """③ dry-run 网络异常（DNS 失败）→ warning 放行，走下载路径由后置校验兜底。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"fake-video")
    recorder = _new_recorder(
        dry_raise=RuntimeError("Temporary failure in name resolution"),
        dry_info={"entries": [{"id": "p0"}, {"id": "p1"}]},
        download_info={"id": "p1"},
        path=str(video_file),
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = _make_downloader(tmp_path, summary_worker=None)
    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.TRANSCRIBING
    assert len(recorder["dry_calls"]) == 1
    assert recorder["download_calls"] == [BILIBILI_URL]


@pytest.mark.asyncio
async def test_post_download_multipart_message_has_b23_tv_hint(monkeypatch, tmp_path):
    """P0-4 兜底：dry-run 放行但实际下载仍检出多P → 文案含 b23.tv p=N 指引。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    p1 = tmp_path / "p1.mp4"
    p2 = tmp_path / "p2.mp4"
    p1.write_bytes(b"fake-1")
    p2.write_bytes(b"fake-2")
    recorder = _new_recorder(
        dry_raise=RuntimeError("connection timed out"),
        dry_info={"entries": [{"id": "p0"}, {"id": "p1"}]},
        download_info={"entries": [{"id": "p0"}, {"id": "p1"}]},
        path=str(p1),
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = _make_downloader(tmp_path, summary_worker=None)
    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.FAILED
    assert "2 个分P" in task["error_message"]
    assert "b23.tv" in task["error_message"]


@pytest.mark.asyncio
async def test_subtitle_path_network_error_falls_back_to_download(
    monkeypatch, tmp_path
):
    """③ 字幕路径 dry-run 网络异常 → 放行；字幕直取失败 → 回退下载路径正常完成。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"fake-video")
    recorder = _new_recorder(
        dry_raise=RuntimeError("Temporary failure in name resolution"),
        dry_info={"id": "p1"},
        download_info={"id": "p1"},
        path=str(video_file),
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = _make_downloader(tmp_path, summary_worker=AsyncMock())

    def no_subtitle(video_url, sessdata):
        return None

    monkeypatch.setattr(downloader, "_try_extract_bilibili_subtitle", no_subtitle)

    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.TRANSCRIBING
    assert recorder["download_calls"] == [BILIBILI_URL]
