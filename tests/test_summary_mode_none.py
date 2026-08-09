"""summary_mode="none"（仅转录模式）后端行为测试。

覆盖：
- VALID_SUMMARY_MODES 两处副本（deps / llm_worker）均包含 "none"；
- TranscriberWorker：'none' 时转录完成后直接终态、不派发 LLM、payload 无副作用；
  其他模式照常派发；
- 多P（multipart）链路：分P标记 COMPLETED、merge 后跳过总览总结、summary_mode 透传；
- 任务创建接口接受 summary_mode="none" 并入库；
- re-summarize 对 'none' 任务可正常补总结。
"""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.downloader.video_downloader_worker import (
    VideoDownloaderWorker,
)
from src.main.python.sheng_wen.infra.api.routes import deps
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.llm.llm_worker import (
    VALID_SUMMARY_MODES as LLM_VALID_SUMMARY_MODES,
)
from src.main.python.sheng_wen.llm.llm_worker import LLMWorker
from src.main.python.sheng_wen.task_parts import (
    get_task_parts,
    init_task_parts,
    update_task_part,
)
from src.main.python.sheng_wen.transcriber import transcriber_worker as worker_module
from src.main.python.sheng_wen.transcriber.transcriber import TranscriptionResult
from src.main.python.sheng_wen.transcriber.transcriber_worker import TranscriberWorker


# ---- 公共辅助 --------------------------------------------------------------


def _result(text: str = "hello", duration: float = 8.0) -> TranscriptionResult:
    return TranscriptionResult(
        segments=[{"start": 1.0, "end": 2.0, "text": text}],
        transcription_time=0.5,
        real_time_factor=0.1,
        total_time=0.6,
        model_load_time=0.2,
        audio_duration=duration,
        language="zh",
        language_probability=0.9,
    )


class FakeTranscriber:
    def transcribe(self, path, progress_callback=None, cancel_check=None):
        if progress_callback:
            progress_callback(1.0)
        return _result()


def _save_task(task_id: str, summary_mode: str) -> None:
    db.save_task(
        task_id,
        {
            "id": task_id,
            "video_url": "file:///tmp/input.mp3",
            "status": TaskStatus.TRANSCRIBING,
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
        },
    )


def _make_transcriber_worker(monkeypatch, tmp_path, next_worker) -> TranscriberWorker:
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
    worker = TranscriberWorker("test", FakeTranscriber(), next_worker)
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


# ---- VALID_SUMMARY_MODES / 归一化 ------------------------------------------


def test_valid_summary_modes_include_none():
    assert "none" in deps.VALID_SUMMARY_MODES
    assert "none" in LLM_VALID_SUMMARY_MODES


def test_normalize_summary_mode_accepts_none():
    assert deps._normalize_summary_mode("none") == "none"
    assert deps._normalize_summary_mode("NONE") == "none"
    assert deps._normalize_summary_mode("none", fallback="auto") == "none"
    # re-summarize 无显式模式时，fallback 沿用任务已存模式（含 "none"）
    assert deps._normalize_summary_mode(None, fallback="none") == "none"
    # 非法值仍回退默认
    assert deps._normalize_summary_mode("bogus") == "auto"


def test_llm_worker_none_requested_mode_falls_back_to_auto_effective(monkeypatch):
    """'none' 若经 re-summarize 等入口到达 LLMWorker，应按 auto 判定兜底生成总结。"""
    worker = LLMWorker("test", llm_client=None)
    assert worker._resolve_requested_mode({"summary_mode": "none"}, None) == "none"
    metrics = {
        "audio_duration_sec": 1.0,
        "line_count": 1,
        "audio_triggered": False,
        "line_triggered": False,
    }
    monkeypatch.setattr(worker, "_collect_auto_mode_metrics", lambda **_kwargs: metrics)
    assert worker._resolve_effective_mode("none", None, "x") == "standard"


# ---- TranscriberWorker 派发行为 --------------------------------------------


@pytest.mark.asyncio
async def test_transcriber_none_mode_skips_llm_and_completes_task(
    monkeypatch, tmp_path
):
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    next_worker = AsyncMock()
    worker = _make_transcriber_worker(monkeypatch, tmp_path, next_worker)

    payload = _transcriber_payload(tmp_path, task_id, "none")
    returned = worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    assert returned is not None
    # 不派发 LLM
    next_worker.add_task.assert_not_called()
    # 任务直接终态，保留转录字段
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["progress"] == 100
    assert "hello" in task["transcript"]
    assert task["transcription_time"] == 0.5
    assert task["audio_duration"] == 8.0
    assert task["summary_mode"] == "none"
    # payload 无副作用
    assert payload == _transcriber_payload(tmp_path, task_id, "none")


@pytest.mark.asyncio
async def test_transcriber_auto_mode_still_dispatches_to_llm(monkeypatch, tmp_path):
    task_id = str(uuid.uuid4())
    _save_task(task_id, "auto")
    next_worker = AsyncMock()
    worker = _make_transcriber_worker(monkeypatch, tmp_path, next_worker)

    payload = _transcriber_payload(tmp_path, task_id, "auto")
    worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    next_worker.add_task.assert_awaited_once()
    sent_payload = next_worker.add_task.await_args.args[0]
    assert sent_payload["task_id"] == task_id
    assert sent_payload["summary_mode"] == "auto"
    assert sent_payload["intermediate_file_path"].endswith("out_summary.txt")
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.SUMMARIZING
    assert task["progress"] == 0.0
    assert payload == _transcriber_payload(tmp_path, task_id, "auto")


@pytest.mark.asyncio
async def test_transcriber_none_mode_multipart_part_marked_completed(
    monkeypatch,
    tmp_path,
):
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    init_task_parts(task_id, [{"index": 0, "cid": None, "title": "P1", "duration": 8}])
    next_worker = AsyncMock()
    worker = _make_transcriber_worker(monkeypatch, tmp_path, next_worker)

    payload = _transcriber_payload(
        tmp_path, task_id, "none", multipart_part={"index": 0, "title": "P1"}
    )
    worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    # 分P直接置为 COMPLETED（不再等待 LLM 分P总结）
    part = get_task_parts(task_id)[0]
    assert part["status"] == "COMPLETED"
    assert part["progress"] == 100
    assert "hello" in part["transcript"]
    next_worker.add_task.assert_not_called()
    assert db.get_task(task_id)["status"] == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_transcriber_auto_mode_multipart_part_waits_for_summary(
    monkeypatch,
    tmp_path,
):
    task_id = str(uuid.uuid4())
    _save_task(task_id, "auto")
    init_task_parts(task_id, [{"index": 0, "cid": None, "title": "P1", "duration": 8}])
    next_worker = AsyncMock()
    worker = _make_transcriber_worker(monkeypatch, tmp_path, next_worker)

    payload = _transcriber_payload(
        tmp_path, task_id, "auto", multipart_part={"index": 0, "title": "P1"}
    )
    worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    part = get_task_parts(task_id)[0]
    assert part["status"] == "SUMMARIZING"
    next_worker.add_task.assert_not_called()


# ---- 多P（multipart）merge 链路 ---------------------------------------------


def _make_multipart_task(task_id: str, with_summaries: bool = True) -> None:
    db.save_task(
        task_id,
        {
            "id": task_id,
            "video_url": "https://www.bilibili.com/video/BV1test",
            "status": TaskStatus.SUMMARIZING,
            "created_at": datetime.now(timezone.utc),
            "latest_modified_at": datetime.now(timezone.utc),
            "progress": 95.0,
            "title": "multi",
            "author_name": None,
            "author_url": None,
            "summary_mode": "none" if not with_summaries else "auto",
            "summary_chunk_total": None,
            "summary_chunk_done": None,
            "summary_meta": None,
        },
    )
    init_task_parts(
        task_id,
        [
            {"index": 0, "cid": None, "title": "P1", "duration": 10.0},
            {"index": 1, "cid": None, "title": "P2", "duration": 10.0},
        ],
    )
    for index in (0, 1):
        updates = {
            "status": "COMPLETED",
            "transcript": f"transcript-{index}",
            "audio_duration": 10.0,
        }
        if with_summaries:
            updates["summary"] = f"summary-{index}"
        update_task_part(task_id, index, updates)


def _multipart_payload(task_id: str, summary_mode: str) -> dict:
    return {
        "task_id": task_id,
        "video_url": "https://www.bilibili.com/video/BV1test",
        "summary_mode": summary_mode,
        "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
        "multipart_batch": True,
    }


def test_multipart_merge_none_mode_skips_overview_summary(tmp_path):
    task_id = str(uuid.uuid4())
    _make_multipart_task(task_id, with_summaries=False)
    worker = VideoDownloaderWorker("test", summary_worker=AsyncMock())
    worker.output_dir = str(tmp_path)

    worker._process_bilibili_multipart(_multipart_payload(task_id, "none"))

    # 不派发总览 AI 总结，任务直接终态
    worker.summary_worker.process_task.assert_not_called()
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["progress"] == 100.0
    assert "transcript-0" in task["transcript"]
    assert "transcript-1" in task["transcript"]
    assert task["audio_duration"] == 20.0


def test_multipart_merge_auto_mode_dispatches_overview_with_summary_mode(tmp_path):
    task_id = str(uuid.uuid4())
    _make_multipart_task(task_id, with_summaries=True)
    worker = VideoDownloaderWorker("test", summary_worker=AsyncMock())
    worker.output_dir = str(tmp_path)

    worker._process_bilibili_multipart(_multipart_payload(task_id, "auto"))

    # 正常模式仍派发总览总结，且 summary_mode 从 payload 透传
    assert worker.summary_worker.process_task.await_count == 1
    sent = worker.summary_worker.process_task.await_args.args[0]
    assert sent["summary_mode"] == "auto"
    assert sent["multipart_overview"] is True
    assert sent["task_id"] == task_id


# ---- 任务接口 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_accepts_summary_mode_none():
    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    app.include_router(tasks_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/tasks/",
            json={
                "video_url": "https://example.com/video.mp4",
                "quality": "best",
                "summary_mode": "none",
            },
        )

    assert resp.status_code == 201
    task_id = resp.json()["id"]
    assert db.get_task(task_id)["summary_mode"] == "none"


@pytest.mark.asyncio
async def test_resummarize_works_on_none_mode_task():
    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    llm_worker = AsyncMock()
    app.state.get_llm_worker = AsyncMock(return_value=llm_worker)
    app.include_router(tasks_router)

    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    db.update_task(task_id, {"status": TaskStatus.COMPLETED, "transcript": "hello"})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/tasks/{task_id}/re-summarize")

    assert resp.status_code == 200
    llm_worker.add_task.assert_awaited_once()
    sent = llm_worker.add_task.await_args.args[0]
    assert sent["task_id"] == task_id
    # 无显式模式时 fallback 沿用任务已存模式（"none"），LLMWorker 会按 auto 判定兜底
    assert sent["summary_mode"] == "none"
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.SUMMARIZING
    # SUMMARIZING 广播/持久化的 summary_mode：resolved='none'（对仅转录任务补总结）
    # 改存 'auto'，消除"none + SUMMARIZING"瞬时误导（实际模式由
    # llm_worker._resolve_effective_mode 按 auto 兜底判定）；worker payload 仍为 'none'
    assert task["summary_mode"] == "auto"


@pytest.mark.asyncio
async def test_resummarize_none_mode_task_with_explicit_mode():
    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    llm_worker = AsyncMock()
    app.state.get_llm_worker = AsyncMock(return_value=llm_worker)
    app.include_router(tasks_router)

    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    db.update_task(task_id, {"status": TaskStatus.COMPLETED, "transcript": "hello"})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/tasks/{task_id}/re-summarize", json={"summary_mode": "standard"}
        )

    assert resp.status_code == 200
    sent = llm_worker.add_task.await_args.args[0]
    assert sent["summary_mode"] == "standard"
