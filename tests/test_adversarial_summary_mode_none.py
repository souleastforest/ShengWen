"""对抗性测试：summary_mode="none"（仅转录原文模式）——从需求出发验证，不信任实现自测。

覆盖需求 B 的对抗性验证点：
1. 'none' 任务提交→完成时 COMPLETED/progress=100/transcript 完整/summary 为空；
   非 'none' 模式行为完全不变（diff 回归：auto/standard/agent 全部照常派发 LLM）。
2. multipart 分P任务 'none'：每个 part COMPLETED、无 overview 总结、任务终态正确；
   多P字幕直取同样跳过。
3. re-summarize 对 'none' 任务补总结成功（status 回 COMPLETED、summary 出现）。
4. re-transcribe / re-download 与 'none' 组合不崩溃（不 500、不派发 LLM）。
"""

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.downloader.video_downloader_worker import (
    VideoDownloaderWorker,
)
from src.main.python.sheng_wen.llm.llm_worker import LLMWorker
from src.main.python.sheng_wen.task_parts import get_task_parts, init_task_parts
from src.main.python.sheng_wen.transcriber import transcriber_worker as worker_module
from src.main.python.sheng_wen.transcriber.transcriber import TranscriptionResult
from src.main.python.sheng_wen.transcriber.transcriber_worker import TranscriberWorker


# ---- 公共辅助 --------------------------------------------------------------


def _result(text: str = "hello") -> TranscriptionResult:
    return TranscriptionResult(
        segments=[{"start": 1.0, "end": 2.0, "text": text}],
        transcription_time=0.5,
        real_time_factor=0.1,
        total_time=0.6,
        model_load_time=0.2,
        audio_duration=8.0,
        language="zh",
        language_probability=0.9,
    )


class FakeTranscriber:
    def transcribe(self, path, progress_callback=None, cancel_check=None):
        if progress_callback:
            progress_callback(1.0)
        return _result()


class FakeLLMClient:
    """模拟 LLM 客户端：回调返回一段总结文本。"""

    def __init__(self, text="这是对抗性测试生成的总结。"):
        self.text = text

    async def response(self, messages, resp_callback, stream=True, timeout=60):
        resp_callback(self.text)


def _save_task(
    task_id: str, summary_mode: str, status=TaskStatus.TRANSCRIBING, **extra
):
    task = {
        "id": task_id,
        "video_url": "file:///tmp/input.mp3",
        "status": status,
        "created_at": datetime.now(timezone.utc),
        "latest_modified_at": datetime.now(timezone.utc),
        "progress": 0.0,
        "title": "test",
        "author_name": None,
        "author_url": None,
        "summary_mode": summary_mode,
        "summary_chunk_total": None,
        "summary_chunk_done": None,
        "summary_meta": None,
    }
    task.update(extra)
    db.save_task(task_id, task)


def _make_transcriber_worker(monkeypatch, tmp_path) -> TranscriberWorker:
    monkeypatch.setattr(
        worker_module,
        "config",
        SimpleNamespace(
            whisper=SimpleNamespace(
                asr_chunk_threshold_sec=10.0,
                asr_chunk_duration_sec=5.0,
                asr_chunk_oom_fallback=False,
            )
        ),
    )
    monkeypatch.setattr(worker_module, "get_audio_duration", lambda _path: 8.0)
    worker = TranscriberWorker("test", FakeTranscriber(), AsyncMock())
    worker._loop = asyncio.get_running_loop()
    return worker


def _transcriber_payload(
    tmp_path, task_id: str, summary_mode: str, multipart_part=None
):
    audio_file = tmp_path / "input.mp3"
    audio_file.write_bytes(b"fake-audio")
    payload = {
        "task_id": task_id,
        "audio_file": str(audio_file),
        "output_file": str(tmp_path / "out_summary.md"),
        "summary_mode": summary_mode,
    }
    if multipart_part is not None:
        payload["multipart_part"] = multipart_part
    return payload


# ---- 1. 模式行为不变性（diff 回归）+ 'none' 终态 ------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "standard", "agent"])
async def test_transcriber_all_normal_modes_still_dispatch_llm(
    monkeypatch, tmp_path, mode
):
    """diff 回归：auto/standard/agent 全部照常派发 LLM，'none' 除外。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, mode)
    worker = _make_transcriber_worker(monkeypatch, tmp_path)

    payload = _transcriber_payload(tmp_path, task_id, mode)
    worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    assert worker._next_worker.add_task.await_count == 1
    sent = worker._next_worker.add_task.await_args.args[0]
    assert sent["task_id"] == task_id
    assert sent["summary_mode"] == mode
    # 任务进入总结阶段而非直接终态
    assert db.get_task(task_id)["status"] == TaskStatus.SUMMARIZING


@pytest.mark.asyncio
async def test_transcriber_uppercase_NONE_also_skips_llm(monkeypatch, tmp_path):
    """大写 'NONE' 应同样走仅转录分支。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    worker = _make_transcriber_worker(monkeypatch, tmp_path)

    payload = _transcriber_payload(tmp_path, task_id, "NONE")
    worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    worker._next_worker.add_task.assert_not_called()
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["progress"] == 100


@pytest.mark.asyncio
async def test_transcriber_missing_summary_mode_still_dispatches(monkeypatch, tmp_path):
    """缺失 summary_mode 不得误判为 'none'（防默认跳过总结）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "auto")
    worker = _make_transcriber_worker(monkeypatch, tmp_path)

    payload = _transcriber_payload(tmp_path, task_id, "")
    worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    assert worker._next_worker.add_task.await_count == 1
    assert db.get_task(task_id)["status"] == TaskStatus.SUMMARIZING


@pytest.mark.asyncio
async def test_transcriber_none_completes_with_empty_summary(monkeypatch, tmp_path):
    """'none' 终态：COMPLETED、progress=100、transcript 完整、summary 为空。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    worker = _make_transcriber_worker(monkeypatch, tmp_path)

    payload = _transcriber_payload(tmp_path, task_id, "none")
    worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["progress"] == 100
    assert "hello" in task["transcript"]
    assert not task.get("summary")
    assert task["summary_mode"] == "none"


# ---- 2. multipart / 字幕直取 -------------------------------------------------


def _run_in_worker_thread(fn):
    """在无事件循环的 worker 线程中执行同步函数（生产环境 process_task 的运行方式）。"""
    result = {}

    def target():
        try:
            result["value"] = fn()
        except BaseException as e:  # noqa: BLE001
            result["error"] = e

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


@pytest.mark.asyncio
async def test_multipart_batch_none_end_to_end(monkeypatch, tmp_path):
    """多P分P 'none' 全链路：每个 part COMPLETED、无 overview 总结、任务终态 COMPLETED。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    init_task_parts(
        task_id,
        [
            {"index": 0, "cid": None, "title": "P1", "duration": 8},
            {"index": 1, "cid": None, "title": "P2", "duration": 8},
        ],
    )

    transcriber = _make_transcriber_worker(monkeypatch, tmp_path)

    def fake_extract_audio(video_path, audio_path, task_id=None):
        with open(audio_path, "wb") as f:
            f.write(b"fake-audio")
        return True

    monkeypatch.setattr(transcriber, "_extract_audio", fake_extract_audio)

    downloader = VideoDownloaderWorker("test", summary_worker=AsyncMock())
    downloader.next_worker = transcriber
    downloader.output_dir = str(tmp_path)
    downloader._loop = asyncio.get_running_loop()

    # 非 bilibili URL：跳过字幕直取，走下载+转录
    payload = {
        "task_id": task_id,
        "video_url": "https://example.com/video.mp4",
        "summary_mode": "none",
        "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
    }

    # 拦截 yt-dlp 下载：返回已存在的占位文件
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"fake-video")

    class FakeYDL:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            return {"entries": [{"id": "p0"}]}

        def prepare_filename(self, entry):
            return str(video_file)

    import src.main.python.sheng_wen.downloader.video_downloader_worker as dl_module

    monkeypatch.setattr(dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))

    downloader._process_bilibili_multipart(payload)
    await downloader._await_pending_updates(timeout=3.0)
    await transcriber._await_pending_updates(timeout=3.0)

    # 每个分P COMPLETED
    parts = get_task_parts(task_id)
    assert len(parts) == 2
    for part in parts:
        assert part["status"] == "COMPLETED"
        assert part["progress"] == 100
    # 无 overview 总结派发
    downloader.summary_worker.process_task.assert_not_called()
    # 任务终态
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["progress"] == 100
    assert "hello" in task["transcript"]
    assert not task.get("summary")


@pytest.mark.asyncio
async def test_subtitle_direct_fetch_none_skips_llm(monkeypatch, tmp_path):
    """单P字幕直取 'none'：任务 COMPLETED、不派发总结。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    downloader = VideoDownloaderWorker("test", summary_worker=AsyncMock())
    downloader.output_dir = str(tmp_path)
    downloader._loop = asyncio.get_running_loop()

    monkeypatch.setattr(
        downloader,
        "_try_extract_bilibili_subtitle",
        lambda video_url, sessdata: {
            "transcript": "字幕转录内容",
            "language": "zh",
        },
    )

    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": "https://www.bilibili.com/video/BV1test",
            "summary_mode": "none",
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    downloader.summary_worker.process_task.assert_not_called()
    downloader.summary_worker.add_task.assert_not_called()
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["progress"] == 100
    assert "字幕转录内容" in task["transcript"]
    assert not task.get("summary")


@pytest.mark.asyncio
async def test_multipart_subtitle_merge_none_skips_llm(monkeypatch, tmp_path):
    """多P字幕直取合并 'none'：跳过总结、任务终态正确。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    downloader = VideoDownloaderWorker("test", summary_worker=AsyncMock())
    downloader.output_dir = str(tmp_path)
    downloader._loop = asyncio.get_running_loop()

    async def fake_multi_subtitles(video_url, sessdata, part_indices):
        return [
            {
                "part_index": 0,
                "part_title": "P1",
                "transcript": "P1字幕",
                "duration": 10,
            },
            {
                "part_index": 1,
                "part_title": "P2",
                "transcript": "P2字幕",
                "duration": 10,
            },
        ]

    monkeypatch.setattr(
        downloader, "_extract_bilibili_multi_part_subtitles", fake_multi_subtitles
    )

    class FakeVideo:
        async def get_info(self):
            return {
                "title": "multi",
                "pages": [
                    {"page": 1, "duration": 10},
                    {"page": 2, "duration": 10},
                ],
            }

    import bilibili_api.video as bili_video

    monkeypatch.setattr(bili_video.Video, "get_info", FakeVideo.get_info)

    # 多P字幕直取分支内部使用 asyncio.run(...)，必须在线程（无运行中事件循环）中执行
    _run_in_worker_thread(
        lambda: downloader.process_task(
            {
                "task_id": task_id,
                "video_url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "summary_mode": "none",
                "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
            }
        ),
    )
    await downloader._await_pending_updates(timeout=3.0)

    downloader.summary_worker.process_task.assert_not_called()
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["progress"] == 100
    assert "P1字幕" in task["transcript"]
    assert "P2字幕" in task["transcript"]
    assert not task.get("summary")


# ---- 3. re-summarize 补总结 --------------------------------------------------


@pytest.mark.asyncio
async def test_resummarize_none_task_full_flow_generates_summary(monkeypatch, tmp_path):
    """re-summarize 'none' 任务全流程：LLM 执行后 status 回 COMPLETED、summary 出现。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none", status=TaskStatus.COMPLETED, transcript="转录原文内容")
    db.update_task(task_id, {"progress": 100.0})

    worker = LLMWorker("test", llm_client=FakeLLMClient("对抗性总结文本"))
    worker.system_prompt = "你是一个总结助手。"
    worker._loop = asyncio.get_running_loop()

    temp_file = tmp_path / "re.txt"
    temp_file.write_text("转录原文内容", encoding="utf-8")

    # 模拟 re-summarize 的派发（summary_mode 沿用任务已存模式 'none'）
    await worker.process_task(
        {
            "task_id": task_id,
            "intermediate_file_path": str(temp_file),
            "output_file": str(tmp_path / "re_summary.md"),
            "summary_mode": "none",
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert "对抗性总结文本" in task["summary"]
    assert task["summary_mode"] in {"standard", "agent"}


# ---- 4. re-transcribe / re-download 与 'none' 组合 ---------------------------


@pytest.mark.asyncio
async def test_re_transcribe_none_task_keeps_none_mode(monkeypatch, tmp_path):
    """re-transcribe 'none' 任务（无显式模式）→ 沿用 'none'，完成时不派发 LLM。"""
    from src.main.python.sheng_wen.infra.api.routes import deps

    task_id = str(uuid.uuid4())
    _save_task(task_id, "none", status=TaskStatus.COMPLETED, transcript="旧转录")
    db.update_task(task_id, {"progress": 100.0})

    local_media = tmp_path / "input.mp3"
    local_media.write_bytes(b"fake")
    monkeypatch.setattr(
        deps, "_resolve_local_media_file", lambda tid, task: str(local_media)
    )

    transcriber = _make_transcriber_worker(monkeypatch, tmp_path)
    add_task_mock = AsyncMock()
    monkeypatch.setattr(transcriber, "add_task", add_task_mock)

    import httpx
    from fastapi import FastAPI

    from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
    from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router

    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    app.state.get_transcriber_worker = AsyncMock(return_value=transcriber)
    app.include_router(tasks_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/tasks/{task_id}/re-transcribe")

    assert resp.status_code == 200
    # 路由派发的 payload 必须沿用 'none'（否则 transcriber 会派发 LLM）
    sent = add_task_mock.await_args.args[0]
    assert sent["summary_mode"] == "none"

    # transcriber 按 'none' 处理：直接终态、不派发 LLM
    payload = _transcriber_payload(tmp_path, task_id, "none")
    transcriber.process_task(payload)
    await transcriber._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["progress"] == 100
    assert "hello" in task["transcript"]
    assert task["summary_mode"] == "none"
    assert not task.get("summary")
    transcriber._next_worker.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_re_download_none_task_keeps_none_mode(monkeypatch, tmp_path):
    """re-download 'none' 任务 → 下载 payload 沿用 summary_mode='none'，不崩溃。"""
    from src.main.python.sheng_wen.infra.api.routes import deps

    task_id = str(uuid.uuid4())
    _save_task(
        task_id,
        "none",
        status=TaskStatus.COMPLETED,
        transcript="转录内容",
        video_url="https://example.com/video.mp4",
    )
    db.update_task(task_id, {"progress": 100.0})
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)

    import httpx
    from fastapi import FastAPI

    from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
    from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router

    downloader = VideoDownloaderWorker("test", summary_worker=AsyncMock())
    add_task_mock = AsyncMock()
    monkeypatch.setattr(downloader, "add_task", add_task_mock)
    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    app.state.get_downloader_worker = AsyncMock(return_value=downloader)
    app.include_router(tasks_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/tasks/{task_id}/re-download")

    assert resp.status_code == 200
    sent = add_task_mock.await_args.args[0]
    assert sent["summary_mode"] == "none"
    assert sent["re_download_only"] is True
