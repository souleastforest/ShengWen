"""对抗性测试：仅转录"总结标题"开关（generate_topic）与快速重跑（re-transcribe）的缺陷探测。

实现 agent 已覆盖常规路径（tests/test_generate_topic.py），本文件从需求反推对抗点：

1. payload 中 **缺失** generate_topic 键（而非显式 true）时 worker 必须按默认 True 处理
   ——re-transcribe / re-download 路径不携带该键，行为由此决定；
2. re-transcribe 端到端：file:// 本地任务走 transcriber 分支、按任务已存 summary_mode、
   重置 PENDING 且清空 topic；**持久化决策：generate_topic 已入库，重跑恢复任务已存
   开关状态**（false 不生成标题 / true 生成 / 显式 payload 覆盖已存值 / 老任务无字段
   默认 True）；
3. LLMWorker.generate_topic 对超长转录只截取前 12000 字符（含边界断言）；
4. generate_topic_for_task 的降级矩阵：空串/纯空白/非字符串标题、worker 缺方法均跳过；
5. 分P separate 模式：每个分P是独立任务（payload 无 multipart_part），
   generate_topic 必须逐任务透传，且不受 merge 子任务跳过逻辑误伤。
"""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.db import TaskStatus
from src.main.python.sheng_wen.downloader.video_downloader_worker import (
    VideoDownloaderWorker,
)
from src.main.python.sheng_wen.infra.api.routes import deps
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.llm.llm_worker import (
    LLMWorker,
    TOPIC_GENERATION_MAX_CHARS,
    generate_topic_for_task,
)
from src.main.python.sheng_wen.task_updater import (
    update_and_notify as real_update_and_notify,
)
from src.main.python.sheng_wen.transcriber import transcriber_worker as worker_module
from src.main.python.sheng_wen.transcriber.transcriber import TranscriptionResult
from src.main.python.sheng_wen.transcriber.transcriber_worker import TranscriberWorker


# ---- 公共辅助（与实现 agent 测试同构，保证可对比） ---------------------------


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
    """具备 generate_topic 的替身：可配置返回标题/None/空白/非字符串/抛异常，并记录调用。"""

    def __init__(
        self,
        topic: str | None = "自动标题",
        error: Exception | None = None,
    ):
        self._topic = topic
        self._error = error
        self.calls: list[str] = []

    async def generate_topic(self, transcript: str) -> str | None:
        self.calls.append(transcript)
        if self._error is not None:
            raise self._error
        return self._topic


def _save_task(task_id: str, summary_mode: str) -> None:
    from src.main.python.sheng_wen.db import db

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
    tmp_path,
    task_id: str,
    summary_mode: str,
    *,
    generate_topic: bool | None = None,
    multipart_part: dict | None = None,
) -> dict:
    """generate_topic=None 表示 payload 中**不出现**该键（模拟 re-transcribe 派发）。"""
    audio_file = tmp_path / "input.mp3"
    audio_file.write_bytes(b"fake-audio")
    payload = {
        "task_id": task_id,
        "audio_file": str(audio_file),
        "output_file": str(tmp_path / "out_summary.md"),
        "summary_mode": summary_mode,
    }
    if generate_topic is not None:
        payload["generate_topic"] = generate_topic
    if multipart_part is not None:
        payload["multipart_part"] = multipart_part
    return payload


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


class FakeUpdater:
    """拦截 update_and_notify：记录调用并同步应用（与真实语义一致）。"""

    def __init__(self, db):
        self.db = db
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, task_id, updates):
        self.calls.append((task_id, dict(updates)))
        return self.db.update_task(task_id, updates)


class FakeTranscriberRouteWorker:
    def __init__(self):
        self.added: list[dict] = []

    async def add_task(self, payload):
        self.added.append(payload)


def _base_task(
    tid: str,
    status=TaskStatus.FAILED,
    video_url=None,
    summary_mode="none",
    generate_topic=None,
):
    task = {
        "id": tid,
        "video_url": video_url or f"https://example.com/{tid}",
        "status": status,
        "created_at": datetime.now(timezone.utc),
        "latest_modified_at": datetime.now(timezone.utc),
        "progress": 42.0,
        "title": f"task {tid}",
        "transcript": "已有转录",
        "summary": "",
        "topic": "旧标题",
        "error_message": "模型加载失败",
        "audio_downloaded": False,
        "audio_missing_reason": "reclaimed",
        "summary_mode": summary_mode,
    }
    # generate_topic=None 模拟老任务（DB 无该字段）
    if generate_topic is not None:
        task["generate_topic"] = generate_topic
    return task


@pytest.fixture
def retranscribe_env(monkeypatch):
    """构造独立 app + 假 db/updater/transcriber-worker，专测 re-transcribe 路由。"""
    from src.main.python.sheng_wen.infra.api.routes import tasks as tasks_module

    db = FakeDB([])
    worker = FakeTranscriberRouteWorker()
    updater = FakeUpdater(db)
    app = FastAPI()

    async def fake_worker_factory():
        return worker

    app.state.get_transcriber_worker = fake_worker_factory
    app.include_router(tasks_router)

    monkeypatch.setattr(tasks_module, "db", db)
    monkeypatch.setattr(
        real_update_and_notify.__module__ + ".update_and_notify", updater
    )
    # file:// 本地任务走 transcriber 分支：本地媒体文件存在
    monkeypatch.setattr(
        deps, "_resolve_local_media_file", lambda tid, task: "/tmp/local.mp3"
    )
    return {"app": app, "db": db, "worker": worker, "updater": updater}


# ---- 对抗点 1：payload 缺失 generate_topic 键（re-transcribe 路径语义） -------


@pytest.mark.asyncio
async def test_transcriber_none_missing_key_defaults_topic_on(monkeypatch, tmp_path):
    """payload 不携带 generate_topic 键 → 按默认 True 生成标题。

    re-transcribe/re-download 派发 payload 均不带该键，此用例固化其语义：
    只要缺失键就生成标题，与显式 true 等价。
    """
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    llm_worker = FakeLLMWorker(topic="缺省标题")
    worker = _make_transcriber_worker(monkeypatch, tmp_path, llm_worker)

    payload = _transcriber_payload(tmp_path, task_id, "none", generate_topic=None)
    assert "generate_topic" not in payload
    worker.process_task(payload)
    await worker._await_pending_updates(timeout=2.0)

    from src.main.python.sheng_wen.db import db

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["topic"] == "缺省标题"
    assert task["summary"] is None
    # 标题基于本次转录全文生成（中间文件格式为时间戳前缀+文本行）
    assert len(llm_worker.calls) == 1
    assert "hello" in llm_worker.calls[0]


# ---- 对抗点 2：re-transcribe 端到端 ----------------------------------------


@pytest.mark.asyncio
async def test_re_transcribe_404_for_unknown_task(retranscribe_env):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=retranscribe_env["app"]),
        base_url="http://test",
    ) as client:
        resp = await client.post("/tasks/ghost/re-transcribe")
    assert resp.status_code == 404
    assert retranscribe_env["worker"].added == []


@pytest.mark.asyncio
async def test_re_transcribe_local_task_resets_and_uses_stored_mode(retranscribe_env):
    """file:// 本地 FAILED 任务：重置 PENDING + 清空 topic + 按任务已存 summary_mode。"""
    env = retranscribe_env
    env["db"].tasks["t1"] = _base_task("t1", video_url="file:///tmp/a.mp4")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-transcribe")

    assert resp.status_code == 200
    reset_call = env["updater"].calls[0][1]
    assert reset_call["status"] == TaskStatus.PENDING
    assert reset_call["topic"] is None
    assert reset_call["transcript"] == ""
    assert reset_call["summary"] == ""
    assert reset_call["summary_mode"] == "none"  # 按任务已存模式
    assert env["updater"].calls[1][1]["status"] == TaskStatus.TRANSCRIBING

    payload = env["worker"].added[0]
    assert payload["summary_mode"] == "none"
    # 重跑恢复开关：老任务（无 generate_topic 字段）按默认 True 恢复并显式派发
    assert payload["generate_topic"] is True
    assert reset_call["generate_topic"] is True


async def _process_retranscribe_payload(
    task_data: dict,
    payload: dict,
    monkeypatch,
    tmp_path,
    topic: str | None = "重跑后新标题",
) -> FakeLLMWorker:
    """用真实 TranscriberWorker 处理 re-transcribe 派发 payload，返回 llm_worker。

    真实 worker 的 update_and_notify 同样被 FakeUpdater 拦截（模块属性在调用期
    解析），断言目标 = FakeDB 中 t1 的最新状态。is_task_cancelled 对真实 db 中
    不存在的任务视为已删除（取消），因此先把 t1 落盘到真实 db（隔离库）。
    """
    from src.main.python.sheng_wen.db import db as real_db

    real_db.save_task("t1", dict(task_data))
    llm_worker = FakeLLMWorker(topic=topic)
    worker = _make_transcriber_worker(monkeypatch, tmp_path, llm_worker)
    real_payload = dict(payload)
    # 覆盖音频/输出为临时真实文件（build_transcriber_payload 的 /tmp/local.mp3
    # 不存在，FakeTranscriber 不校验文件，但 TranscriberWorker 会检查 exists）
    audio_file = tmp_path / "input.mp3"
    audio_file.write_bytes(b"fake-audio")
    real_payload.update(
        {
            "audio_file": str(audio_file),
            "output_file": str(tmp_path / "out.md"),
        }
    )
    worker.process_task(real_payload)
    # 等待 worker 提交的异步更新（COMPLETED 广播 / 标题生成）在 loop 上执行完毕
    await worker._await_pending_updates(timeout=2.0)
    return llm_worker


@pytest.mark.asyncio
async def test_re_transcribe_off_switch_persisted_skips_topic(
    retranscribe_env, monkeypatch, tmp_path
):
    """用户决策（持久化）：创建时关闭"总结标题"（generate_topic=false）的任务，
    FAILED 后快速重跑（re-transcribe）→ 恢复任务已存开关（false）→ 不再生成标题。
    """
    env = retranscribe_env
    env["db"].tasks["t1"] = _base_task(
        "t1", video_url="https://example.com/v.mp4", generate_topic=False
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-transcribe")

    assert resp.status_code == 200
    payload = env["worker"].added[0]
    assert payload["generate_topic"] is False  # 开关状态已恢复并显式派发

    llm_worker = await _process_retranscribe_payload(
        env["db"].tasks["t1"], payload, monkeypatch, tmp_path
    )

    t1 = env["db"].tasks["t1"]
    assert t1["status"] == TaskStatus.COMPLETED
    assert t1["topic"] is None  # 不生成标题
    assert llm_worker.calls == []  # LLM 完全不被调用


@pytest.mark.asyncio
async def test_re_transcribe_stored_true_regenerates_topic(
    retranscribe_env, monkeypatch, tmp_path
):
    """任务已存 generate_topic=true → 重跑恢复开启 → 重新生成标题。"""
    env = retranscribe_env
    env["db"].tasks["t1"] = _base_task(
        "t1", video_url="https://example.com/v.mp4", generate_topic=True
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-transcribe")

    assert resp.status_code == 200
    payload = env["worker"].added[0]
    assert payload["generate_topic"] is True

    llm_worker = await _process_retranscribe_payload(
        env["db"].tasks["t1"], payload, monkeypatch, tmp_path, topic="重跑后新标题"
    )

    t1 = env["db"].tasks["t1"]
    assert t1["status"] == TaskStatus.COMPLETED
    assert t1["topic"] == "重跑后新标题"
    assert len(llm_worker.calls) == 1


@pytest.mark.asyncio
async def test_re_transcribe_explicit_payload_true_overrides_stored_false(
    retranscribe_env, monkeypatch, tmp_path
):
    """payload 显式 generate_topic=true 覆盖任务已存 false → 生成标题。"""
    env = retranscribe_env
    env["db"].tasks["t1"] = _base_task(
        "t1", video_url="https://example.com/v.mp4", generate_topic=False
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/tasks/t1/re-transcribe", json={"generate_topic": True}
        )

    assert resp.status_code == 200
    payload = env["worker"].added[0]
    assert payload["generate_topic"] is True
    # 显式值同时写回任务记录（与 summary_mode 先例一致）
    assert env["db"].tasks["t1"]["generate_topic"] is True

    llm_worker = await _process_retranscribe_payload(
        env["db"].tasks["t1"], payload, monkeypatch, tmp_path, topic="显式开启标题"
    )

    t1 = env["db"].tasks["t1"]
    assert t1["status"] == TaskStatus.COMPLETED
    assert t1["topic"] == "显式开启标题"
    assert len(llm_worker.calls) == 1


@pytest.mark.asyncio
async def test_re_transcribe_explicit_payload_false_overrides_stored_true(
    retranscribe_env, monkeypatch, tmp_path
):
    """payload 显式 generate_topic=false 覆盖任务已存 true → 不生成标题。"""
    env = retranscribe_env
    env["db"].tasks["t1"] = _base_task(
        "t1", video_url="https://example.com/v.mp4", generate_topic=True
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/tasks/t1/re-transcribe", json={"generate_topic": False}
        )

    assert resp.status_code == 200
    payload = env["worker"].added[0]
    assert payload["generate_topic"] is False
    assert env["db"].tasks["t1"]["generate_topic"] is False

    llm_worker = await _process_retranscribe_payload(
        env["db"].tasks["t1"], payload, monkeypatch, tmp_path
    )

    t1 = env["db"].tasks["t1"]
    assert t1["status"] == TaskStatus.COMPLETED
    assert t1["topic"] is None
    assert llm_worker.calls == []


@pytest.mark.asyncio
async def test_re_transcribe_legacy_task_without_field_defaults_true(
    retranscribe_env, monkeypatch, tmp_path
):
    """老任务（DB 无 generate_topic 字段）→ 默认 True → 重跑生成标题。"""
    env = retranscribe_env
    env["db"].tasks["t1"] = _base_task("t1", video_url="https://example.com/v.mp4")
    assert "generate_topic" not in env["db"].tasks["t1"]  # 模拟老任务

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-transcribe")

    assert resp.status_code == 200
    payload = env["worker"].added[0]
    assert payload["generate_topic"] is True  # 老任务默认开启

    llm_worker = await _process_retranscribe_payload(
        env["db"].tasks["t1"], payload, monkeypatch, tmp_path, topic="老任务标题"
    )

    t1 = env["db"].tasks["t1"]
    assert t1["status"] == TaskStatus.COMPLETED
    assert t1["topic"] == "老任务标题"
    assert len(llm_worker.calls) == 1


@pytest.mark.asyncio
async def test_re_transcribe_explicit_body_mode_overrides_stored(retranscribe_env):
    """带 body 时按请求指定模式（前端总是发送当前 UI 模式，会覆盖任务已存模式）。"""
    env = retranscribe_env
    env["db"].tasks["t1"] = _base_task("t1", video_url="file:///tmp/a.mp4")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/tasks/t1/re-transcribe", json={"summary_mode": "standard"}
        )

    assert resp.status_code == 200
    assert env["worker"].added[0]["summary_mode"] == "standard"


@pytest.mark.asyncio
async def test_re_transcribe_400_when_no_media_and_online_url_unresolvable(
    retranscribe_env, monkeypatch
):
    """无本地媒体且非可重下载 URL（file:// 且文件丢失）→ 400。"""
    env = retranscribe_env
    env["db"].tasks["t1"] = _base_task("t1", video_url="file:///tmp/lost.mp4")
    monkeypatch.setattr(deps, "_resolve_local_media_file", lambda tid, task: None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        resp = await client.post("/tasks/t1/re-transcribe")

    assert resp.status_code == 400
    assert env["worker"].added == []


# ---- 对抗点 3：LLMWorker.generate_topic 截断边界 ----------------------------


class RecordingClient:
    """记录 messages 的 LLM 客户端替身。"""

    def __init__(self, chunks: list[str] | None = None):
        self._chunks = chunks or ["标题"]
        self.calls: list[dict] = []

    async def response(self, messages, resp_callback, stream=True, timeout=60):
        self.calls.append(
            {
                "messages": messages,
                "stream": stream,
                "timeout": timeout,
            }
        )
        for chunk in self._chunks:
            resp_callback(chunk)


@pytest.mark.asyncio
async def test_llm_worker_generate_topic_truncates_to_12000_chars():
    """超长转录：只把前 TOPIC_GENERATION_MAX_CHARS 字符发给 LLM（成本/上下文保护）。"""
    client = RecordingClient()
    worker = LLMWorker("test", llm_client=client)
    long_text = "甲" * (TOPIC_GENERATION_MAX_CHARS + 5000)

    topic = await worker.generate_topic(long_text)

    assert topic is not None
    assert len(client.calls) == 1
    user_msg = client.calls[0]["messages"][1]
    assert len(user_msg.content) == TOPIC_GENERATION_MAX_CHARS
    assert user_msg.content == long_text[:TOPIC_GENERATION_MAX_CHARS]
    assert len(client.calls[0]["messages"]) == 2  # system + user


@pytest.mark.asyncio
async def test_llm_worker_generate_topic_exact_boundary_not_truncated():
    """恰好 12000 字符：完整发送；12001 字符：截断到 12000。"""
    client = RecordingClient()
    worker = LLMWorker("test", llm_client=client)

    text = "乙" * TOPIC_GENERATION_MAX_CHARS
    await worker.generate_topic(text)
    assert len(client.calls[0]["messages"][1].content) == TOPIC_GENERATION_MAX_CHARS

    client.calls.clear()
    await worker.generate_topic(text + "丙")
    assert len(client.calls[0]["messages"][1].content) == TOPIC_GENERATION_MAX_CHARS
    assert client.calls[0]["messages"][1].content.endswith(text[-1])


# ---- 对抗点 4：generate_topic_for_task 降级矩阵 -----------------------------


@pytest.mark.asyncio
async def test_generate_topic_for_task_whitespace_or_non_string_topic_skipped():
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")

    # 纯空白标题
    await generate_topic_for_task(FakeLLMWorker(topic="   "), task_id, "全文")
    # 非字符串标题
    await generate_topic_for_task(FakeLLMWorker(topic=12345), task_id, "全文")

    from src.main.python.sheng_wen.db import db

    task = db.get_task(task_id)
    assert task["topic"] is None
    assert task["status"] == TaskStatus.TRANSCRIBING  # 状态未被扰动


@pytest.mark.asyncio
async def test_generate_topic_for_task_worker_without_method_degrades():
    """worker 无 generate_topic 方法（如普通 Worker 实例）→ 跳过且不抛错。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")

    await generate_topic_for_task(object(), task_id, "全文")

    from src.main.python.sheng_wen.db import db

    assert db.get_task(task_id)["topic"] is None


@pytest.mark.asyncio
async def test_generate_topic_for_task_writes_trimmed_topic():
    """标题首尾空白被收敛后写入。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")

    await generate_topic_for_task(FakeLLMWorker(topic="  新标题  "), task_id, "全文")

    from src.main.python.sheng_wen.db import db

    assert db.get_task(task_id)["topic"] == "新标题"


# ---- 对抗点 5：分P separate 模式透传 ---------------------------------------


def _make_app_with_bus() -> FastAPI:
    app = FastAPI()
    app.state.event_bus = AsyncioEventBus()
    app.include_router(tasks_router)
    return app


@pytest.mark.asyncio
async def test_separate_parts_carry_generate_topic_and_no_multipart_part(monkeypatch):
    """separate 模式：每个分P是独立任务，payload 无 multipart_part，
    generate_topic 逐任务透传（false 不生成标题的开关必须对每个分P生效）。"""
    from src.main.python.sheng_wen.infra.api.routes import tasks as tasks_module

    app = _make_app_with_bus()
    published: list[dict] = []

    async def capture(payload):
        published.append(payload)

    app.state.event_bus.subscribe(TASK_CREATED, capture)

    async def fake_fetch_parts(url):
        return (
            "测试标题",
            [
                {"index": 0, "cid": 1001, "title": "P1", "duration": 60},
                {"index": 1, "cid": 1002, "title": "P2", "duration": 60},
            ],
        )

    monkeypatch.setattr(
        tasks_module, "_get_bilibili_video_title_and_parts", fake_fetch_parts
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/tasks/",
            json={
                "video_url": "https://www.bilibili.com/video/BV1xx0000000",
                "summary_mode": "none",
                "generate_topic": False,
                "bilibili_parts": {"mode": "separate", "indices": [0, 1]},
            },
        )

    assert resp.status_code == 201
    assert len(published) == 2
    for payload in published:
        assert payload["generate_topic"] is False
        assert "multipart_part" not in payload  # 不会被 merge 子任务跳过逻辑误伤
        assert payload["bilibili_parts"]["mode"] == "merge"
        assert payload["bilibili_parts"]["indices"] == [0] or payload["bilibili_parts"][
            "indices"
        ] == [1]


@pytest.mark.asyncio
async def test_separate_parts_default_generate_topic_true(monkeypatch):
    """separate 模式缺省：generate_topic 默认 True 透传每个分P。"""
    from src.main.python.sheng_wen.infra.api.routes import tasks as tasks_module

    app = _make_app_with_bus()
    published: list[dict] = []

    async def capture(payload):
        published.append(payload)

    app.state.event_bus.subscribe(TASK_CREATED, capture)

    async def fake_fetch_parts(url):
        return "测试标题", [{"index": 0, "cid": 1001, "title": "P1", "duration": 60}]

    monkeypatch.setattr(
        tasks_module, "_get_bilibili_video_title_and_parts", fake_fetch_parts
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/tasks/",
            json={
                "video_url": "https://www.bilibili.com/video/BV1xx0000000",
                "summary_mode": "none",
                "bilibili_parts": {"mode": "separate", "indices": [0]},
            },
        )

    assert resp.status_code == 201
    assert published[0]["generate_topic"] is True


# ---- 对抗点 6：字幕直取（_try_process_with_bilibili_subtitle）----------------


def _make_subtitle_worker(monkeypatch, tmp_path, llm_worker) -> VideoDownloaderWorker:
    worker = VideoDownloaderWorker("test", summary_worker=llm_worker)
    worker.output_dir = str(tmp_path)
    worker._loop = asyncio.get_running_loop()
    return worker


@pytest.mark.asyncio
async def test_subtitle_direct_none_missing_key_generates_topic(monkeypatch, tmp_path):
    """字幕直取 + none：payload 缺 generate_topic 键 → 默认 True → 写标题。"""
    from src.main.python.sheng_wen.db import db
    from src.main.python.sheng_wen.downloader import (
        video_downloader_worker as dl_module,
    )

    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    # 任务必须真实落库（is_task_cancelled 视缺失任务为已删除）
    db.save_task(task_id, dict(db.get_task(task_id)))

    llm_worker = FakeLLMWorker(topic="字幕标题")
    worker = _make_subtitle_worker(monkeypatch, tmp_path, llm_worker)
    monkeypatch.setattr(
        dl_module.VideoDownloaderWorker,
        "_try_extract_bilibili_subtitle",
        lambda self, url, sess: {
            "transcript": "字幕直取全文",
            "title": "原视频标题",
            "language": "zh",
            "duration": 10.0,
        },
    )

    payload = {
        "task_id": task_id,
        "video_url": "https://www.bilibili.com/video/BV1xx0000000",
        "summary_mode": "none",
        # 刻意不携带 generate_topic 键
    }
    ok = worker._try_process_with_bilibili_subtitle(payload)
    assert ok is True
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["summary"] is None
    assert task["topic"] == "字幕标题"
    assert llm_worker.calls == ["字幕直取全文"]


@pytest.mark.asyncio
async def test_subtitle_direct_none_generate_topic_false_skips(monkeypatch, tmp_path):
    from src.main.python.sheng_wen.db import db
    from src.main.python.sheng_wen.downloader import (
        video_downloader_worker as dl_module,
    )

    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    db.save_task(task_id, dict(db.get_task(task_id)))

    llm_worker = FakeLLMWorker(topic="不应写入")
    worker = _make_subtitle_worker(monkeypatch, tmp_path, llm_worker)
    monkeypatch.setattr(
        dl_module.VideoDownloaderWorker,
        "_try_extract_bilibili_subtitle",
        lambda self, url, sess: {
            "transcript": "字幕直取全文",
            "title": "原视频标题",
            "language": "zh",
            "duration": 10.0,
        },
    )

    payload = {
        "task_id": task_id,
        "video_url": "https://www.bilibili.com/video/BV1xx0000000",
        "summary_mode": "none",
        "generate_topic": False,
    }
    ok = worker._try_process_with_bilibili_subtitle(payload)
    assert ok is True
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["topic"] is None
    assert llm_worker.calls == []  # LLM 完全不被调用


@pytest.mark.asyncio
async def test_subtitle_direct_multipart_child_skips_topic(monkeypatch, tmp_path):
    """字幕直取路径同样尊重 merge 分P子任务跳过规则。"""
    from src.main.python.sheng_wen.db import db
    from src.main.python.sheng_wen.downloader import (
        video_downloader_worker as dl_module,
    )

    task_id = str(uuid.uuid4())
    _save_task(task_id, "none")
    db.save_task(task_id, dict(db.get_task(task_id)))

    llm_worker = FakeLLMWorker(topic="不应写入")
    worker = _make_subtitle_worker(monkeypatch, tmp_path, llm_worker)
    monkeypatch.setattr(
        dl_module.VideoDownloaderWorker,
        "_try_extract_bilibili_subtitle",
        lambda self, url, sess: {
            "transcript": "分P字幕",
            "title": "原视频标题",
            "language": "zh",
            "duration": 10.0,
        },
    )

    payload = {
        "task_id": task_id,
        "video_url": "https://www.bilibili.com/video/BV1xx0000000",
        "summary_mode": "none",
        "multipart_part": {"index": 0, "title": "P1"},
    }
    ok = worker._try_process_with_bilibili_subtitle(payload)
    assert ok is True
    await worker._await_pending_updates(timeout=2.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["topic"] is None
    assert llm_worker.calls == []
