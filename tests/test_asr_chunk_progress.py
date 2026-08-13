"""Tests: ASR 分片进度字段 asr_chunk_total / asr_chunk_done（加性 schema 变更）。

生产诉求（任务 93b857d0）：用户期望"转录分片 10/10"进度展示——ASR 分片计数
（10 个 6min 分片）从不落库，前端无数据源。本测试锁定：
- TaskModel 列定义 + to_dict 契约往返（save/get/update 均保留字段）；
- _ensure_schema 对存量库 ALTER TABLE ADD COLUMN（列不存在才加）；
- 分片写入路径：run_chunks 开始写 total、每片完成写 done（与 progress 合并上报）；
- 清空路径：转录完成转 SUMMARIZING 时两字段置 None（与 summary_chunk 对称）。
"""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.db import Base, TaskDB, TaskModel, TaskStatus, db
from src.main.python.sheng_wen.downloader.video_downloader_worker import (
    VideoDownloaderWorker,
)
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.task_parts import init_task_parts, update_task_part
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


def _chunk_config(threshold=10, chunk_duration=5, fallback=True):
    return SimpleNamespace(
        whisper=SimpleNamespace(
            asr_chunk_threshold_sec=threshold,
            asr_chunk_duration_sec=chunk_duration,
            asr_chunk_oom_fallback=fallback,
        )
    )


def _save_task(
    task_id: str, summary_mode: str, asr_chunk_total=None, asr_chunk_done=None
):
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
            "asr_chunk_total": asr_chunk_total,
            "asr_chunk_done": asr_chunk_done,
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


class FakeTranscriber:
    def transcribe(self, path, progress_callback=None, cancel_check=None):
        if progress_callback:
            progress_callback(1.0)
        return _result()


def _transcriber_payload(tmp_path, task_id: str, summary_mode: str):
    audio_file = tmp_path / "input.mp3"
    audio_file.write_bytes(b"fake-audio")
    return {
        "task_id": task_id,
        "audio_file": str(audio_file),
        "output_file": str(tmp_path / "out_summary.md"),
        "summary_mode": summary_mode,
    }


# ---- 契约：to_dict / 存取往返 --------------------------------------------------


def test_task_model_to_dict_includes_asr_chunk_fields():
    task = TaskModel(
        id="t1",
        video_url="https://example.com/a.mp4",
        status=TaskStatus.TRANSCRIBING,
        asr_chunk_total=10,
        asr_chunk_done=3,
    )
    data = task.to_dict()
    assert data["asr_chunk_total"] == 10
    assert data["asr_chunk_done"] == 3


def test_task_save_get_roundtrip_preserves_asr_chunk_fields():
    task_id = "asr-roundtrip-1"
    db.save_task(
        task_id,
        {
            "id": task_id,
            "video_url": "https://example.com/a.mp4",
            "status": TaskStatus.TRANSCRIBING,
            "created_at": datetime.now(timezone.utc),
            "latest_modified_at": datetime.now(timezone.utc),
            "progress": 30.0,
            "asr_chunk_total": 10,
            "asr_chunk_done": 3,
        },
    )
    task = db.get_task(task_id)
    assert task["asr_chunk_total"] == 10
    assert task["asr_chunk_done"] == 3

    # update_task 增量更新 done 后往返保留
    db.update_task(task_id, {"asr_chunk_done": 5})
    task = db.get_task(task_id)
    assert task["asr_chunk_total"] == 10
    assert task["asr_chunk_done"] == 5


def test_ensure_schema_adds_asr_chunk_columns_to_legacy_db(tmp_path):
    """存量库缺 asr_chunk_* 列时，TaskDB 构造 _ensure_schema 应 ALTER 补齐。"""
    legacy_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{legacy_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE tasks ("
                "id VARCHAR PRIMARY KEY, video_url VARCHAR NOT NULL, status VARCHAR, "
                "created_at DATETIME, latest_modified_at DATETIME, progress FLOAT, "
                "title VARCHAR, transcript TEXT, summary TEXT, error_message TEXT, "
                "audio_duration FLOAT, transcription_time FLOAT, topic VARCHAR, "
                "author_name VARCHAR, author_url VARCHAR, summary_mode VARCHAR, "
                "summary_chunk_total INTEGER, summary_chunk_done INTEGER, "
                "summary_meta TEXT, audio_downloaded BOOLEAN, "
                "audio_missing_reason VARCHAR, generate_topic BOOLEAN, source_name VARCHAR)"
            )
        )
    engine.dispose()

    task_db = TaskDB(sqlite_path=str(legacy_path))
    columns = {col["name"] for col in inspect(task_db.engine).get_columns("tasks")}
    assert "asr_chunk_total" in columns
    assert "asr_chunk_done" in columns
    # 旧列不受影响
    assert "source_name" in columns
    task_db.engine.dispose()


def test_ensure_schema_is_idempotent_for_existing_columns(tmp_path):
    """已含 asr_chunk 列的新库重复 _ensure_schema 不报错、列保持。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        TaskModel(
            id="t1",
            video_url="https://example.com/a.mp4",
            asr_chunk_total=10,
            asr_chunk_done=2,
        )
    )
    session.commit()
    session.close()
    engine.dispose()

    task_db = TaskDB(sqlite_path=str(tmp_path / "fresh.db"))
    columns = {col["name"] for col in inspect(task_db.engine).get_columns("tasks")}
    assert "asr_chunk_total" in columns
    assert "asr_chunk_done" in columns
    task = task_db.get_task("t1")
    assert task["asr_chunk_total"] == 10
    assert task["asr_chunk_done"] == 2
    task_db.engine.dispose()


# ---- 契约：Task Pydantic 响应模型 --------------------------------------------------


def test_task_schema_accepts_asr_chunk_fields():
    from src.main.python.sheng_wen.infra.api.routes.schemas import Task

    model = Task(
        id="t1",
        video_url="https://example.com/a.mp4",
        status=TaskStatus.TRANSCRIBING,
        created_at=datetime.now(timezone.utc),
        asr_chunk_total=10,
        asr_chunk_done=4,
    )
    dumped = model.model_dump()
    assert dumped["asr_chunk_total"] == 10
    assert dumped["asr_chunk_done"] == 4

    # 未传时缺省 None（旧客户端/旧后端兼容）
    model2 = Task(
        id="t2",
        video_url="https://example.com/b.mp4",
        status=TaskStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    assert model2.asr_chunk_total is None
    assert model2.asr_chunk_done is None


# ---- 分片写入路径：run_chunks 事件 --------------------------------------------------


def test_run_chunks_reports_asr_chunk_total_and_done_events(monkeypatch):
    """分片开始写 total=len(chunks)；每片完成写 done=已完成片数（与 progress 同路径）。"""
    events = []
    monkeypatch.setattr(worker_module, "config", _chunk_config())
    monkeypatch.setattr(worker_module, "get_audio_duration", lambda _: 15.0)
    monkeypatch.setattr(
        worker_module,
        "split_audio_into_chunks",
        lambda *_a, **_k: [("c0.wav", 0.0), ("c1.wav", 5.0), ("c2.wav", 10.0)],
    )
    monkeypatch.setattr(worker_module, "cleanup_chunks", lambda _c: None)

    class FakeChunkTranscriber:
        def transcribe(self, path, progress_callback=None, cancel_check=None):
            if progress_callback:
                progress_callback(0.5)
            return _result(path)

    worker = TranscriberWorker("test", FakeChunkTranscriber(), None)

    def progress_cb(progress, asr_chunk_total=None, asr_chunk_done=None):
        events.append((progress, asr_chunk_total, asr_chunk_done))

    result = worker._transcribe_audio_with_chunking(
        "audio.mp3", progress_cb, lambda: False
    )

    assert result is not None
    # 首个事件即写 total（开始分片），done=0
    first = events[0]
    assert first[1] == 3
    assert first[2] == 0
    # 每个分片完成事件携带递增 done 与 total
    done_events = [e for e in events if e[1] == 3 and e[2] is not None]
    assert [e[2] for e in done_events] == [0, 1, 2, 3]
    assert events[-1][1] == 3
    assert events[-1][2] == 3


# ---- 分片写入 + 清空路径：process_task 端到端 --------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_merges_asr_chunk_updates_and_clears_on_summarizing(
    monkeypatch, tmp_path
):
    """chunked 转录：progress 上报合并 asr_chunk 字段；SUMMARIZING 时置 None。"""
    import src.main.python.sheng_wen.task_updater as task_updater_module

    captured = []
    monkeypatch.setattr(
        task_updater_module,
        "update_and_notify",
        AsyncMock(
            side_effect=lambda task_id, updates: captured.append(
                (task_id, dict(updates))
            )
        ),
    )

    task_id = str(uuid.uuid4())
    _save_task(task_id, "auto", asr_chunk_total=10, asr_chunk_done=5)
    next_worker = AsyncMock()
    monkeypatch.setattr(
        worker_module, "config", _chunk_config(threshold=10, chunk_duration=5)
    )
    monkeypatch.setattr(worker_module, "get_audio_duration", lambda _path: 15.0)
    monkeypatch.setattr(worker_module, "cleanup_chunks", lambda _c: None)
    monkeypatch.setattr(
        worker_module,
        "split_audio_into_chunks",
        lambda *_a, **_k: [("c0.wav", 0.0), ("c1.wav", 5.0), ("c2.wav", 10.0)],
    )
    worker = TranscriberWorker("test", FakeTranscriber(), next_worker)
    worker._loop = asyncio.get_running_loop()

    payload = _transcriber_payload(tmp_path, task_id, "auto")
    worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    # 分片写入：至少一次更新携带 asr_chunk_total=3（len(chunks) 覆盖旧值 10）
    total_updates = [u for _, u in captured if u.get("asr_chunk_total") == 3]
    assert total_updates, f"未捕获 asr_chunk_total 写入: {captured}"
    # 最后一片完成后 done=3
    assert any(u.get("asr_chunk_done") == 3 for _, u in captured)
    # 清空路径：SUMMARIZING 终态更新两字段置 None
    final_update = captured[-1][1]
    assert final_update.get("status") == TaskStatus.SUMMARIZING
    assert "asr_chunk_total" in final_update and final_update["asr_chunk_total"] is None
    assert "asr_chunk_done" in final_update and final_update["asr_chunk_done"] is None


@pytest.mark.asyncio
async def test_process_task_clears_asr_chunk_fields_in_db_on_summarizing(
    monkeypatch, tmp_path
):
    """非 chunked 转录：DB 中预置的 asr_chunk 字段在转 SUMMARIZING 时清空（真实落库）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "auto", asr_chunk_total=10, asr_chunk_done=5)
    next_worker = AsyncMock()
    worker = _make_transcriber_worker(monkeypatch, tmp_path, next_worker)

    payload = _transcriber_payload(tmp_path, task_id, "auto")
    worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.SUMMARIZING
    assert task["asr_chunk_total"] is None
    assert task["asr_chunk_done"] is None


# ---- 清空路径补齐：re-transcribe reset 与 multipart 父任务合并 -------------------


@pytest.mark.asyncio
async def test_re_transcribe_reset_clears_asr_chunk_fields(monkeypatch, tmp_path):
    """重转录走非分片路径：reset 后不残留陈旧 asr 分片字段（与 summary_chunk 对称）。"""
    from src.main.python.sheng_wen.infra.api.routes import deps

    task_id = str(uuid.uuid4())
    _save_task(task_id, "none", asr_chunk_total=10, asr_chunk_done=5)
    db.update_task(
        task_id,
        {
            "status": TaskStatus.COMPLETED,
            "transcript": "旧转录",
            "progress": 100.0,
        },
    )

    local_media = tmp_path / "input.mp3"
    local_media.write_bytes(b"fake")
    monkeypatch.setattr(
        deps, "_resolve_local_media_file", lambda tid, task: str(local_media)
    )

    transcriber = _make_transcriber_worker(monkeypatch, tmp_path, AsyncMock())
    add_task_mock = AsyncMock()
    monkeypatch.setattr(transcriber, "add_task", add_task_mock)

    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    app.state.get_transcriber_worker = AsyncMock(return_value=transcriber)
    app.include_router(tasks_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/tasks/{task_id}/re-transcribe")

    assert resp.status_code == 200
    task = db.get_task(task_id)
    assert task["asr_chunk_total"] is None
    assert task["asr_chunk_done"] is None
    assert task["transcript"] == ""


class FakeLLMWorker:
    """具备 generate_topic 的替身（multipart 合并父任务测试用）。"""

    def __init__(self, topic: str | None = "合并标题"):
        self._topic = topic

    async def generate_topic(self, transcript: str) -> str | None:
        return self._topic


def _make_multipart_task(task_id: str) -> None:
    """多P父任务：处于合并阶段且残留陈旧 asr 分片字段（长音频分P曾写父任务）。"""
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
            "summary_mode": "none",
            "summary_chunk_total": None,
            "summary_chunk_done": None,
            "summary_meta": None,
            "asr_chunk_total": 10,
            "asr_chunk_done": 10,
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
        update_task_part(
            task_id,
            index,
            {
                "status": "COMPLETED",
                "transcript": f"transcript-{index}",
                "audio_duration": 10.0,
            },
        )


def _multipart_payload(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "video_url": "https://www.bilibili.com/video/BV1test",
        "summary_mode": "none",
        "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
        "multipart_batch": True,
        "generate_topic": True,
    }


@pytest.mark.asyncio
async def test_multipart_merge_clears_asr_chunk_fields(tmp_path):
    """multipart 父任务合并置终态时清空 asr 分片字段（不残留陈旧计数）。"""
    task_id = str(uuid.uuid4())
    _make_multipart_task(task_id)
    llm_worker = FakeLLMWorker()
    worker = VideoDownloaderWorker("test", summary_worker=llm_worker)
    worker.output_dir = str(tmp_path)
    worker._loop = asyncio.get_running_loop()

    worker._process_bilibili_multipart(_multipart_payload(task_id))
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["asr_chunk_total"] is None
    assert task["asr_chunk_done"] is None
