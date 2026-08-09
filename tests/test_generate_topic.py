"""generate_topic（仅转录"总结标题"开关）后端行为测试。

覆盖：
- 三个创建入口（POST /tasks/、/upload/local-path、/upload multipart）：
  缺省默认 generate_topic=True 透传到 TASK_CREATED payload；显式 false 透传 false；
- TranscriberWorker：none + 默认开启 → 转录完成后写入 topic、summary 为空、任务 COMPLETED；
  none + 显式 false → 不写 topic；LLM 失败 → 静默降级，任务仍 COMPLETED；
  standard/agent 模式不受 generate_topic 影响（照常派发 LLM 总结）；
  multipart 分P子任务跳过标题生成（由 merge 父任务统一生成）；
- multipart merge 父任务：none 时对合并全文生成一次标题；
- LLMWorker.generate_topic：复用 _llm_client 单次非流式调用，失败返回 None，
  响应收敛为单行无引号标题。
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
from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.downloader.video_downloader_worker import (
    VideoDownloaderWorker,
)
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.infra.api.routes.upload import router as upload_router
from src.main.python.sheng_wen.llm.llm_worker import (
    LLMWorker,
    generate_topic_for_task,
)
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


class FakeLLMWorker:
    """具备 generate_topic 的替身：可配置返回标题、返回 None 或抛异常。"""

    def __init__(self, topic: str | None = "自动标题", error: Exception | None = None):
        self._topic = topic
        self._error = error

    async def generate_topic(self, transcript: str) -> str | None:
        if self._error is not None:
            raise self._error
        return self._topic


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
    tmp_path, task_id: str, summary_mode: str, generate_topic=True, multipart_part=None
):
    audio_file = tmp_path / "input.mp3"
    audio_file.write_bytes(b"fake-audio")
    payload = {
        "task_id": task_id,
        "audio_file": str(audio_file),
        "output_file": str(tmp_path / "out_summary.md"),
        "summary_mode": summary_mode,
        "generate_topic": generate_topic,
    }
    if multipart_part is not None:
        payload["multipart_part"] = multipart_part
    return payload


def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    app.include_router(tasks_router)
    app.include_router(upload_router)
    return app


# ---- 创建入口：generate_topic 默认与显式透传 ---------------------------------


@pytest.mark.asyncio
async def test_create_task_default_generate_topic_true():
    app = _make_app()
    published = []

    async def capture(payload):
        published.append(payload)

    app.state.event_bus.subscribe(TASK_CREATED, capture)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/tasks/",
            json={
                "video_url": "https://example.com/gt1.mp4",
                "summary_mode": "none",
            },
        )

    assert resp.status_code == 201
    assert published[0]["generate_topic"] is True
    # 开关状态持久化到任务记录（re-transcribe 重跑时恢复用）
    assert db.get_task(resp.json()["id"])["generate_topic"] is True


@pytest.mark.asyncio
async def test_create_task_explicit_false_passes_false():
    app = _make_app()
    published = []

    async def capture(payload):
        published.append(payload)

    app.state.event_bus.subscribe(TASK_CREATED, capture)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/tasks/",
            json={
                "video_url": "https://example.com/gt2.mp4",
                "summary_mode": "none",
                "generate_topic": False,
            },
        )

    assert resp.status_code == 201
    assert published[0]["generate_topic"] is False
    assert db.get_task(resp.json()["id"])["generate_topic"] is False


@pytest.mark.asyncio
async def test_upload_local_path_default_generate_topic_true(tmp_path):
    app = _make_app()
    published = []

    async def capture(payload):
        published.append(payload)

    app.state.event_bus.subscribe(TASK_CREATED, capture)

    media = tmp_path / "in.mp3"
    media.write_bytes(b"fake")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/upload/local-path",
            json={"file_path": str(media), "summary_mode": "none"},
        )

    assert resp.status_code == 201
    assert published[0]["generate_topic"] is True
    assert db.get_task(resp.json()["id"])["generate_topic"] is True


@pytest.mark.asyncio
async def test_upload_local_path_explicit_false_passes_false(tmp_path):
    app = _make_app()
    published = []

    async def capture(payload):
        published.append(payload)

    app.state.event_bus.subscribe(TASK_CREATED, capture)

    media = tmp_path / "in.mp3"
    media.write_bytes(b"fake")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/upload/local-path",
            json={
                "file_path": str(media),
                "summary_mode": "none",
                "generate_topic": False,
            },
        )

    assert resp.status_code == 201
    assert published[0]["generate_topic"] is False
    assert db.get_task(resp.json()["id"])["generate_topic"] is False


@pytest.mark.asyncio
async def test_upload_multipart_generate_topic_default_and_explicit(tmp_path):
    app = _make_app()
    published = []

    async def capture(payload):
        published.append(payload)

    app.state.event_bus.subscribe(TASK_CREATED, capture)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 缺省：默认开启
        resp = await client.post(
            "/upload",
            files={"file": ("a.mp3", b"fake", "audio/mpeg")},
            data={"summary_mode": "none"},
        )
        assert resp.status_code == 201
        assert published[-1]["generate_topic"] is True
        assert db.get_task(resp.json()["id"])["generate_topic"] is True
        # 显式 false
        resp = await client.post(
            "/upload",
            files={"file": ("b.mp3", b"fake", "audio/mpeg")},
            data={"summary_mode": "none", "generate_topic": "false"},
        )
        assert resp.status_code == 201
        assert published[-1]["generate_topic"] is False
        assert db.get_task(resp.json()["id"])["generate_topic"] is False


# ---- TranscriberWorker：none 分支标题生成 ------------------------------------


@pytest.mark.asyncio
async def test_transcriber_none_generates_topic_and_stays_completed(
    monkeypatch, tmp_path
):
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    llm_worker = FakeLLMWorker(topic="测试标题")
    worker = _make_transcriber_worker(monkeypatch, tmp_path, llm_worker)

    worker.process_task(_transcriber_payload(tmp_path, task_id, "none"))
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["progress"] == 100
    assert task["topic"] == "测试标题"
    # 不生成 AI 总结
    assert task["summary"] is None


@pytest.mark.asyncio
async def test_transcriber_none_generate_topic_false_skips(monkeypatch, tmp_path):
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    llm_worker = FakeLLMWorker(topic="不应写入")
    worker = _make_transcriber_worker(monkeypatch, tmp_path, llm_worker)

    worker.process_task(
        _transcriber_payload(tmp_path, task_id, "none", generate_topic=False)
    )
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["topic"] is None
    assert task["summary"] is None


@pytest.mark.asyncio
async def test_transcriber_none_llm_failure_still_completed(monkeypatch, tmp_path):
    """LLM 标题生成抛异常 → 静默降级，任务仍 COMPLETED、topic 为空。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    llm_worker = FakeLLMWorker(error=RuntimeError("LLM 挂了"))
    worker = _make_transcriber_worker(monkeypatch, tmp_path, llm_worker)

    worker.process_task(_transcriber_payload(tmp_path, task_id, "none"))
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["topic"] is None


@pytest.mark.asyncio
async def test_transcriber_none_empty_topic_not_written(monkeypatch, tmp_path):
    """LLM 返回空串/非字符串 → 不写入 topic。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    worker = _make_transcriber_worker(
        monkeypatch, tmp_path, FakeLLMWorker(topic=None)
    )

    worker.process_task(_transcriber_payload(tmp_path, task_id, "none"))
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["topic"] is None


@pytest.mark.asyncio
async def test_transcriber_standard_mode_unaffected_by_generate_topic(
    monkeypatch, tmp_path
):
    """standard/agent 模式：generate_topic 不生效，照常派发 LLM 总结。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "standard")
    next_worker = AsyncMock()
    worker = _make_transcriber_worker(monkeypatch, tmp_path, next_worker)

    worker.process_task(_transcriber_payload(tmp_path, task_id, "standard"))
    await worker._await_pending_updates(timeout=2.0)

    next_worker.add_task.assert_awaited_once()
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.SUMMARIZING
    assert task["topic"] is None


@pytest.mark.asyncio
async def test_transcriber_none_multipart_child_skips_topic(monkeypatch, tmp_path):
    """multipart 分P子任务不生成标题（由 merge 父任务统一生成一次）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    init_task_parts(task_id, [{"index": 0, "cid": None, "title": "P1", "duration": 8}])
    llm_worker = FakeLLMWorker(topic="不应写入")
    worker = _make_transcriber_worker(monkeypatch, tmp_path, llm_worker)

    worker.process_task(
        _transcriber_payload(
            tmp_path,
            task_id,
            "none",
            multipart_part={"index": 0, "title": "P1"},
        )
    )
    await worker._await_pending_updates(timeout=2.0)

    part = get_task_parts(task_id)[0]
    assert part["status"] == "COMPLETED"
    assert db.get_task(task_id)["topic"] is None


# ---- multipart merge 父任务：对合并全文生成一次标题 ---------------------------


def _make_multipart_task(task_id: str) -> None:
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


def _multipart_payload(task_id: str, generate_topic=True) -> dict:
    payload = {
        "task_id": task_id,
        "video_url": "https://www.bilibili.com/video/BV1test",
        "summary_mode": "none",
        "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
        "multipart_batch": True,
        "generate_topic": generate_topic,
    }
    return payload


@pytest.mark.asyncio
async def test_multipart_merge_none_generates_topic_once(tmp_path):
    task_id = str(uuid.uuid4())
    _make_multipart_task(task_id)
    llm_worker = FakeLLMWorker(topic="合并标题")
    worker = VideoDownloaderWorker("test", summary_worker=llm_worker)
    worker.output_dir = str(tmp_path)
    worker._loop = asyncio.get_running_loop()

    worker._process_bilibili_multipart(_multipart_payload(task_id))
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["topic"] == "合并标题"
    assert task["summary"] is None
    # 标题基于合并后的完整转录生成
    assert "transcript-0" in task["transcript"]
    assert "transcript-1" in task["transcript"]


@pytest.mark.asyncio
async def test_multipart_merge_none_generate_topic_false_skips(tmp_path):
    task_id = str(uuid.uuid4())
    _make_multipart_task(task_id)
    llm_worker = FakeLLMWorker(topic="不应写入")
    worker = VideoDownloaderWorker("test", summary_worker=llm_worker)
    worker.output_dir = str(tmp_path)
    worker._loop = asyncio.get_running_loop()

    worker._process_bilibili_multipart(_multipart_payload(task_id, generate_topic=False))
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["topic"] is None


# ---- LLMWorker.generate_topic 单元行为 --------------------------------------


class FakeLLMClient:
    """可编程的 LLM 客户端替身：正常流/错误回调/抛异常三种模式。"""

    def __init__(self, chunks: list[str] | None = None, error: str | None = None):
        self._chunks = chunks or []
        self._error = error
        self.calls: list[dict] = []

    async def response(self, messages, resp_callback, stream: bool = True, timeout: int = 60):
        self.calls.append({"messages": messages, "stream": stream})
        if self._error:
            from src.main.python.sheng_wen.llm.llm import LLMResponseError

            resp_callback(LLMResponseError(self._error))
            return
        for chunk in self._chunks:
            resp_callback(chunk)


@pytest.mark.asyncio
async def test_llm_worker_generate_topic_success_single_line():
    worker = LLMWorker(
        "test", llm_client=FakeLLMClient(chunks=["「AI 视频内容速览」", "，30秒看懂"])
    )
    topic = await worker.generate_topic("这是一段很长的转录内容")
    # 首尾引号/括号被收敛剥除（仅限整串首尾），保留串中字符
    assert topic == "AI 视频内容速览」，30秒看懂"
    # 复用 _llm_client，单次非流式调用
    client = worker._llm_client
    assert client.calls[0]["stream"] is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_llm_worker_generate_topic_response_error_returns_none():
    worker = LLMWorker("test", llm_client=FakeLLMClient(error="模型返回错误"))
    assert await worker.generate_topic("转录内容") is None


@pytest.mark.asyncio
async def test_llm_worker_generate_topic_request_exception_returns_none():
    class BoomClient:
        async def response(self, messages, resp_callback, stream=True, timeout=60):
            raise RuntimeError("网络中断")

    worker = LLMWorker("test", llm_client=BoomClient())
    assert await worker.generate_topic("转录内容") is None


@pytest.mark.asyncio
async def test_llm_worker_generate_topic_no_client_or_empty_text():
    worker = LLMWorker("test", llm_client=None)
    assert await worker.generate_topic("转录内容") is None
    worker2 = LLMWorker("test", llm_client=FakeLLMClient(chunks=["x"]))
    assert await worker2.generate_topic("   ") is None


@pytest.mark.asyncio
async def test_generate_topic_for_task_missing_llm_worker_degrades_gracefully():
    """缺 LLM worker / 抛异常时静默降级，任务不被失败。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")

    # 缺 worker
    await generate_topic_for_task(None, task_id, "全文")
    assert db.get_task(task_id)["topic"] is None

    # worker 抛异常
    await generate_topic_for_task(
        FakeLLMWorker(error=RuntimeError("boom")), task_id, "全文"
    )
    task = db.get_task(task_id)
    assert task["topic"] is None
    assert task["status"] == TaskStatus.TRANSCRIBING
