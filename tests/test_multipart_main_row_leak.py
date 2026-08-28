"""TDD: 多P merge 分P子任务流式总结泄漏主任务行 + b23.tv 短链 p=1 分P选择。

根因（2026-08-19 缺陷，侦查 CONFIRMED，file:line 已核实）：
- transcriber_worker.py:745 每个分P转录完成 `if task_id:` 无条件写主行
  status=SUMMARIZING/progress=0/transcript=该子P转录/清空 asr_chunk（无
  multipart_part 门槛）→ 主行被任一子P的转录内容污染；
- llm_worker.py:636 update_chunk_progress / :673 flush_chunk_stream /
  :529 flush_partial_summary 三个流式回调仅以"主行 status==SUMMARIZING"
  为守卫写主行 summary/progress/summary_chunk_*，无 multipart_part 判断
  → 分P流式中间快照泄漏进主行，主内容区显示"上一分P的流式中间快照"；
- video_downloader_worker.py:1143/1321 每个子P下载开始/结束轮番写主行
  DOWNLOADING/TRANSCRIBING；
- b23.tv 短链重定向自带 &p=1 → 每个分P子任务（video_url=同一短链）下载
  的都是第一个分P（实测 P0/P1 转录字节数一致）。

修复契约：
① 分P子任务转录完成只写 task_parts，不写主行 status/transcript/清空 asr_chunk
  （单P任务 multipart_part=None 行为不变）；
② 分P子任务 agent 模式 update_chunk_progress / flush_chunk_stream 不写主行；
③ 分P子任务 standard 模式 flush_partial_summary 不写主行；
④ 分P子任务下载开始/结束不写主行 DOWNLOADING/TRANSCRIBING；
⑤ 分P子任务 asr_chunk_total/done 不上报主行（加权 progress 写主行保留）；
⑥ finalize 合并转录写主行时显式清空 summary（防御中途脏数据残留）；
⑦ b23.tv 短链 / 自带 p 参数 URL 在分P子任务下载时规范化到对应分P（p=N）；
⑧ 单P任务（无 multipart_part）流式总结仍写主行（回归，不破坏单P路径）。

失败原因须与线上一致：主行 summary 被分P流式中间快照覆盖。
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
from src.main.python.sheng_wen.llm.llm_worker import LLMWorker
from src.main.python.sheng_wen.task_parts import get_task_parts, init_task_parts
from src.main.python.sheng_wen.transcriber.transcriber import TranscriptionResult
from src.main.python.sheng_wen.transcriber.transcriber_worker import TranscriberWorker

import src.main.python.sheng_wen.downloader.video_downloader_worker as dl_module

BILIBILI_URL = "https://www.bilibili.com/video/BV1eh411h7xH"
SHORT_LINK = "https://b23.tv/CD6M1qC"
SIMPLE_TRANSCRIPT = (
    "000000第一段内容，介绍视频主题。\n"
    "000010第二段内容，深入讲解细节。\n"
    "000020第三段内容，总结要点。"
)


# ---- 基础设施 ---------------------------------------------------------------


@pytest.fixture()
def isolated_task_parts(tmp_path, monkeypatch):
    """task_parts 走独立 sqlite：该模块 sqlite3 直连 config.database.sqlite_path，
    不随 db 单例（conftest isolated_db）隔离——必须显式重定向，禁止触碰真实
    ShengWen.db 的 task_parts 表。"""
    import src.main.python.sheng_wen.task_parts as task_parts_module

    monkeypatch.setattr(
        task_parts_module,
        "config",
        SimpleNamespace(
            database=SimpleNamespace(sqlite_path=str(tmp_path / "parts.db"))
        ),
    )


def _save_task(
    task_id: str, status: TaskStatus = TaskStatus.PENDING, summary: str | None = None
) -> None:
    db.save_task(
        task_id,
        {
            "id": task_id,
            "video_url": BILIBILI_URL,
            "status": status,
            "summary": summary,
            "transcript": "主行旧转录内容",
            "summary_mode": "auto",
            "created_at": datetime.now(timezone.utc),
            "latest_modified_at": datetime.now(timezone.utc),
            "progress": 0.0,
            "title": "",
        },
    )


def _init_one_part(task_id: str) -> None:
    init_task_parts(
        task_id,
        [{"index": 0, "cid": 1001, "title": "P1", "duration": 60}],
    )


class StreamingFakeLLM:
    """可编程 LLM：每次 response 按预设 delta 序列驱动 resp_callback；
    delay>0 时模拟真实流式节奏（越过 0.5s 防抖），用于驱动流式回调。"""

    def __init__(self, responses: list[list[str]], delay: float = 0.0):
        self._responses = list(responses)
        self.delay = delay
        self.call_count = 0

    async def response(self, messages, resp_callback, stream=True, timeout=60):
        idx = min(self.call_count, len(self._responses) - 1)
        self.call_count += 1
        for delta in self._responses[idx]:
            if self.delay:
                await asyncio.sleep(self.delay)
            resp_callback(delta)


def _result(text: str, duration: float = 60.0) -> TranscriptionResult:
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


def _make_fake_ytdl(recorder: dict):
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
                if recorder.get("download_raise"):
                    raise recorder["download_raise"]
                return recorder.get("download_info") or {"id": "p1"}
            recorder["dry_calls"].append(url)
            return recorder.get("dry_info") or {}

        def prepare_filename(self, entry):
            return recorder.get("path") or "/nonexistent/out.mp4"

    return FakeYDL


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


# ---- ② agent 模式分P流式回调不写主行 ----------------------------------------


@pytest.mark.asyncio
async def test_agent_child_streaming_does_not_write_main_row_summary(
    monkeypatch, tmp_path, isolated_task_parts
):
    """② 红：分P子任务 agent 模式流式（update_chunk_progress / flush_chunk_stream）
    不得写主行 summary——修前主行 summary 被分P流式中间快照覆盖（线上症状）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)  # 模拟分P转录完成后的主行态
    _init_one_part(task_id)
    in_file = tmp_path / "part.txt"
    out_file = tmp_path / "part_summary.md"
    in_file.write_text(SIMPLE_TRANSCRIPT, encoding="utf-8")

    fake = StreamingFakeLLM([["分块总结正文"]], delay=0.7)
    worker = LLMWorker("test", fake)
    worker._loop = asyncio.get_running_loop()
    monkeypatch.setattr(worker, "is_task_cancelled", lambda _tid: False)

    await worker.process_task(
        {
            "task_id": task_id,
            "intermediate_file_path": str(in_file),
            "output_file": str(out_file),
            "summary_mode": "agent",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    # 分P最终总结写入 task_parts（:334-342 语义保持）
    parts = get_task_parts(task_id)
    assert len(parts) == 1
    assert parts[0]["status"] == "COMPLETED"
    assert parts[0]["summary"] == "分块总结正文"

    # 主行 summary 不得被流式中间快照写入（线上缺陷症状）
    task = db.get_task(task_id)
    assert task["summary"] is None, (
        f"分P流式总结泄漏写入主行: summary={task['summary']!r}"
    )


@pytest.mark.asyncio
async def test_single_part_agent_streaming_still_writes_main_row(monkeypatch, tmp_path):
    """⑧ 回归：单P任务（无 multipart_part）agent 流式总结仍写主行。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)
    in_file = tmp_path / "task.txt"
    out_file = tmp_path / "task_summary.md"
    in_file.write_text(SIMPLE_TRANSCRIPT, encoding="utf-8")

    fake = StreamingFakeLLM([["单P分块总结正文"]])
    worker = LLMWorker("test", fake)
    worker._loop = asyncio.get_running_loop()
    monkeypatch.setattr(worker, "is_task_cancelled", lambda _tid: False)

    await worker.process_task(
        {
            "task_id": task_id,
            "intermediate_file_path": str(in_file),
            "output_file": str(out_file),
            "summary_mode": "agent",
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED.value
    assert task["summary"] == "单P分块总结正文"


# ---- ③ standard 模式分P flush_partial_summary 不写主行 ------------------------


@pytest.mark.asyncio
async def test_standard_child_streaming_does_not_write_main_row_summary(
    monkeypatch, tmp_path, isolated_task_parts
):
    """③ 红：分P子任务 standard 模式 flush_partial_summary 不得写主行 summary。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)
    _init_one_part(task_id)
    in_file = tmp_path / "part.txt"
    out_file = tmp_path / "part_summary.md"
    in_file.write_text("普通单块转录文本，无时间戳也可。", encoding="utf-8")

    fake = StreamingFakeLLM([["标准总结第一段", "标准总结第二段"]], delay=0.7)
    worker = LLMWorker("test", fake)
    worker.system_prompt = "你是总结助手，直接输出总结正文。"
    worker._loop = asyncio.get_running_loop()
    monkeypatch.setattr(worker, "is_task_cancelled", lambda _tid: False)

    await worker.process_task(
        {
            "task_id": task_id,
            "intermediate_file_path": str(in_file),
            "output_file": str(out_file),
            "summary_mode": "standard",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    parts = get_task_parts(task_id)
    assert parts[0]["status"] == "COMPLETED"
    assert parts[0]["summary"] == "标准总结第一段标准总结第二段"

    task = db.get_task(task_id)
    assert task["summary"] is None, (
        f"standard 分P流式泄漏写入主行: summary={task['summary']!r}"
    )


# ---- ① 分P转录完成不写主行 ---------------------------------------------------


@pytest.mark.asyncio
async def test_transcriber_child_completion_does_not_mutate_main_row(
    monkeypatch, tmp_path, isolated_task_parts
):
    """① 红：分P子任务转录完成只写 task_parts——主行 status 不得被置
    SUMMARIZING、transcript 不得被子P转录覆盖、asr_chunk 不得被清空/写入。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.TRANSCRIBING)
    _init_one_part(task_id)
    audio_file = tmp_path / "p1.mp3"
    audio_file.write_bytes(b"fake-audio")
    out_file = tmp_path / "p1_summary.md"

    worker = TranscriberWorker("test", object(), None)
    worker._loop = asyncio.get_running_loop()
    worker._transcribe_audio_with_chunking = lambda *a, **kw: _result("P1 转录内容")

    worker.process_task(
        {
            "task_id": task_id,
            "audio_file": str(audio_file),
            "output_file": str(out_file),
            "summary_mode": "none",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    # 分P行：COMPLETED + transcript（task_parts 写入保持；
    # 文件保存格式为「HHMMSS + 文本」，时间戳前缀属正常产物）
    parts = get_task_parts(task_id)
    assert parts[0]["status"] == "COMPLETED"
    assert parts[0]["transcript"] == "000001P1 转录内容\n"

    # 主行：不得被分P转录完成污染
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.TRANSCRIBING.value, (
        f"分P转录完成将主行置为 {task['status']!r}"
    )
    assert task["transcript"] == "主行旧转录内容", "分P转录完成覆盖了主行 transcript"


@pytest.mark.asyncio
async def test_transcriber_child_progress_keeps_weighted_progress_no_asr_chunks(
    monkeypatch, tmp_path, isolated_task_parts
):
    """⑤ 红：分P子任务 progress 回调——加权 progress 写主行保留（设计内），
    但 asr_chunk_total/done 不得上报主行。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.TRANSCRIBING)
    _init_one_part(task_id)
    audio_file = tmp_path / "p1.mp3"
    audio_file.write_bytes(b"fake-audio")
    out_file = tmp_path / "p1_summary.md"

    worker = TranscriberWorker("test", object(), None)
    worker._loop = asyncio.get_running_loop()

    def fake_transcribe(audio_file, progress_callback=None, cancel_check=None):
        progress_callback(0.5, 3, 1)  # 50%，ASR 分片 3/1
        return _result("P1 转录内容")

    worker._transcribe_audio_with_chunking = fake_transcribe

    worker.process_task(
        {
            "task_id": task_id,
            "audio_file": str(audio_file),
            "output_file": str(out_file),
            "summary_mode": "none",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    # 分P子任务全程不得改写主行 status（修前被转录完成写为 COMPLETED）
    assert task["status"] == TaskStatus.TRANSCRIBING.value, (
        f"分P子任务将主行置为 {task['status']!r}"
    )
    # 加权 progress（单分P 50%）写主行属设计内行为，保留
    assert task["progress"] == 50.0
    # 子P ASR 分片计数不得泄漏到主行
    assert task.get("asr_chunk_total") is None, (
        f"分P asr_chunk_total 泄漏到主行: {task.get('asr_chunk_total')!r}"
    )
    assert task.get("asr_chunk_done") is None


# ---- ④ 分P子任务下载不写主行状态 ---------------------------------------------


@pytest.mark.asyncio
async def test_downloader_child_skips_main_row_status_writes(
    monkeypatch, tmp_path, isolated_task_parts
):
    """④ 红：分P子任务下载开始/结束不得写主行 DOWNLOADING/TRANSCRIBING。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.PENDING)
    _init_one_part(task_id)
    video_file = tmp_path / "p1.mp4"
    video_file.write_bytes(b"fake-video")
    recorder = _new_recorder(
        download_info={"id": "p1"},
        path=str(video_file),
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader._loop = asyncio.get_running_loop()
    downloader._resolve_and_save_bilibili_author = AsyncMock()

    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": BILIBILI_URL,
            "quality": "best",
            "summary_mode": "none",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
            "bilibili_batch_child": True,
            "bilibili_parts": {"mode": "merge", "indices": [0]},
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.PENDING.value, (
        f"分P子任务下载将主行置为 {task['status']!r}"
    )
    # 分P行自身状态更新保持
    parts = get_task_parts(task_id)
    assert parts[0]["status"] == "DOWNLOADING"


# ---- ⑦ b23.tv 短链 p=1 分P选择 ----------------------------------------------


@pytest.mark.asyncio
async def test_b23_shortlink_child_download_normalizes_to_part_url(
    monkeypatch, tmp_path, isolated_task_parts
):
    """⑦ 红：b23.tv 短链（重定向自带 p=1）+ multipart_part.index=1
    → 下载 URL 规范化为完整 BV + ?p=2（不得下载第一个分P）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    video_file = tmp_path / "p2.mp4"
    video_file.write_bytes(b"fake-video")
    recorder = _new_recorder(
        download_info={"id": "p2"},
        path=str(video_file),
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )
    # 短链解析打桩：CD6M1qC → 完整 BV 链接（禁止真实网络）
    monkeypatch.setattr(
        VideoDownloaderWorker,
        "_resolve_final_url",
        classmethod(lambda cls, u: BILIBILI_URL),
    )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader._loop = asyncio.get_running_loop()
    downloader._resolve_and_save_bilibili_author = AsyncMock()

    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": SHORT_LINK,
            "quality": "best",
            "summary_mode": "none",
            "multipart_part": {"index": 1, "title": "P2", "duration": 60},
            "bilibili_batch_child": True,
            "bilibili_parts": {"mode": "merge", "indices": [1]},
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    assert recorder["download_calls"] == [BILIBILI_URL + "?p=2"], (
        f"分P子任务下载 URL 未规范化到对应分P: {recorder['download_calls']}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, expected",
    [
        # 完整链接无 p 参数 → 追加 p=N
        (BILIBILI_URL, BILIBILI_URL + "?p=2"),
        # 完整链接自带 p=1（同 b23 短链缺陷类）→ 覆盖为 p=N
        (BILIBILI_URL + "?p=1", BILIBILI_URL + "?p=2"),
    ],
)
async def test_full_bv_url_child_download_uses_part_param(
    monkeypatch, tmp_path, isolated_task_parts, url, expected
):
    """⑦ 完整 BV 链接：无 p / 自带 p=1 都规范化为 ?p=N。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    video_file = tmp_path / "p2.mp4"
    video_file.write_bytes(b"fake-video")
    recorder = _new_recorder(
        download_info={"id": "p2"},
        path=str(video_file),
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader._loop = asyncio.get_running_loop()
    downloader._resolve_and_save_bilibili_author = AsyncMock()

    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": url,
            "quality": "best",
            "summary_mode": "none",
            "multipart_part": {"index": 1, "title": "P2", "duration": 60},
            "bilibili_batch_child": True,
            "bilibili_parts": {"mode": "merge", "indices": [1]},
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    assert recorder["download_calls"] == [expected]


@pytest.mark.asyncio
async def test_non_bilibili_child_url_unchanged(monkeypatch, tmp_path):
    """⑦ 非 B 站 URL 不受规范化影响。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"fake-video")
    recorder = _new_recorder(
        download_info={"id": "clip"},
        path=str(video_file),
    )
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader._loop = asyncio.get_running_loop()

    url = "https://example.com/video/abc"
    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": url,
            "quality": "best",
            "summary_mode": "none",
            "multipart_part": {"index": 1, "title": "P2", "duration": 60},
            "bilibili_batch_child": True,
            "bilibili_parts": {"mode": "merge", "indices": [1]},
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    assert recorder["download_calls"] == [url]


# ---- ⑥ finalize 显式清空主行 summary -----------------------------------------


@pytest.mark.asyncio
async def test_multipart_finalize_clears_stale_main_summary(
    monkeypatch, tmp_path, isolated_task_parts
):
    """⑥ 红：finalize 合并转录写主行时显式清空 summary，覆盖中途脏数据。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING, summary="中途脏数据残留")
    init_task_parts(
        task_id,
        [
            {"index": 0, "cid": 1001, "title": "P1", "duration": 60},
            {"index": 1, "cid": 1002, "title": "P2", "duration": 60},
        ],
    )
    from src.main.python.sheng_wen.task_parts import update_task_part

    for index, text in [(0, "P1 转录"), (1, "P2 转录")]:
        update_task_part(
            task_id,
            index,
            {"status": "COMPLETED", "progress": 100, "transcript": text},
        )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader.output_dir = str(tmp_path)

    downloader._process_bilibili_multipart(
        {
            "task_id": task_id,
            "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
            "summary_mode": "none",
        }
    )

    task = db.get_task(task_id)
    assert task["summary"] is None, (
        f"finalize 后主行仍残留脏 summary: {task['summary']!r}"
    )
    assert task["status"] == TaskStatus.COMPLETED.value


# ---- 评审 P2-1: finalize 显式写 audio_downloaded -----------------------------


def _make_completed_parts(task_id: str, indices: list[int]) -> None:
    init_task_parts(
        task_id,
        [
            {
                "index": index,
                "cid": 1001 + index,
                "title": f"P{index + 1}",
                "duration": 60,
            }
            for index in indices
        ],
    )
    from src.main.python.sheng_wen.task_parts import update_task_part

    for index in indices:
        update_task_part(
            task_id,
            index,
            {
                "status": "COMPLETED",
                "progress": 100,
                "transcript": f"P{index + 1} 转录",
            },
        )


@pytest.mark.asyncio
async def test_multipart_finalize_sets_audio_downloaded_when_part_media_exists(
    tmp_path, isolated_task_parts
):
    """P2-1 红：任一子P媒体文件在磁盘 → finalize 后主行 audio_downloaded=True
    （消除前端"未知"徽章窗口，不得依赖存储回收器 backfill 自愈）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)
    _make_completed_parts(task_id, [0, 1])
    (tmp_path / f"{task_id}_p1.mp3").write_bytes(b"fake-audio")

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader.output_dir = str(tmp_path)
    downloader.process_task = lambda payload: None  # 分P已就绪，跳过子P处理

    downloader._process_bilibili_multipart(
        {
            "task_id": task_id,
            "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
            "summary_mode": "none",
        }
    )

    task = db.get_task(task_id)
    assert task["audio_downloaded"] is True, (
        f"finalize 未显式写 audio_downloaded: {task.get('audio_downloaded')!r}"
    )
    assert task["status"] == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_multipart_finalize_marks_subtitle_only_when_no_part_media(
    tmp_path, isolated_task_parts
):
    """P2-1：无任何子P媒体文件（字幕直取路径）→ audio_downloaded=False +
    audio_missing_reason=subtitle_only。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)
    _make_completed_parts(task_id, [0, 1])

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader.output_dir = str(tmp_path)
    downloader.process_task = lambda payload: None

    downloader._process_bilibili_multipart(
        {
            "task_id": task_id,
            "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
            "summary_mode": "none",
        }
    )

    task = db.get_task(task_id)
    assert task["audio_downloaded"] is False
    assert task["audio_missing_reason"] == "subtitle_only"


# ---- 评审 P1-2: 分P子任务失败只写 task_parts，不写主行 FAILED ------------------


@pytest.mark.asyncio
async def test_transcriber_child_missing_audio_fails_only_part_row(
    tmp_path, isolated_task_parts
):
    """P1-2 红：分P子任务音频文件缺失 → 只写 task_parts FAILED，主行不得 FAILED。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.TRANSCRIBING)
    _init_one_part(task_id)
    out_file = tmp_path / "p1_summary.md"  # audio_file 故意不存在

    worker = TranscriberWorker("test", object(), None)
    worker._loop = asyncio.get_running_loop()

    worker.process_task(
        {
            "task_id": task_id,
            "audio_file": str(tmp_path / "missing.mp3"),
            "output_file": str(out_file),
            "summary_mode": "none",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    parts = get_task_parts(task_id)
    assert parts[0]["status"] == "FAILED"
    assert "找不到要转录的音频文件" in parts[0]["error_message"]
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.TRANSCRIBING.value, (
        f"分P子任务失败仍写主行 FAILED: {task['status']!r}"
    )


@pytest.mark.asyncio
async def test_transcriber_child_empty_transcript_fails_only_part_row(
    tmp_path, isolated_task_parts
):
    """P1-2 红：分P子任务空转录 → 只写 task_parts FAILED，主行不得 FAILED。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.TRANSCRIBING)
    _init_one_part(task_id)
    audio_file = tmp_path / "p1.mp3"
    audio_file.write_bytes(b"fake-audio")
    out_file = tmp_path / "p1_summary.md"

    worker = TranscriberWorker("test", object(), None)
    worker._loop = asyncio.get_running_loop()
    worker._transcribe_audio_with_chunking = lambda *a, **kw: _result("  ")

    worker.process_task(
        {
            "task_id": task_id,
            "audio_file": str(audio_file),
            "output_file": str(out_file),
            "summary_mode": "none",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    parts = get_task_parts(task_id)
    assert parts[0]["status"] == "FAILED"
    assert "转录结果为空" in parts[0]["error_message"]
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.TRANSCRIBING.value, (
        f"分P子任务空转录仍写主行 FAILED: {task['status']!r}"
    )


@pytest.mark.asyncio
async def test_transcriber_child_generic_exception_fails_only_part_row(
    tmp_path, isolated_task_parts
):
    """P1-2 红：分P子任务转录抛异常 → 只写 task_parts FAILED，主行不得 FAILED。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.TRANSCRIBING)
    _init_one_part(task_id)
    audio_file = tmp_path / "p1.mp3"
    audio_file.write_bytes(b"fake-audio")
    out_file = tmp_path / "p1_summary.md"

    worker = TranscriberWorker("test", object(), None)
    worker._loop = asyncio.get_running_loop()

    def boom(*a, **kw):
        raise RuntimeError("模拟转录崩溃")

    worker._transcribe_audio_with_chunking = boom

    worker.process_task(
        {
            "task_id": task_id,
            "audio_file": str(audio_file),
            "output_file": str(out_file),
            "summary_mode": "none",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    parts = get_task_parts(task_id)
    assert parts[0]["status"] == "FAILED"
    assert "模拟转录崩溃" in parts[0]["error_message"]
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.TRANSCRIBING.value


@pytest.mark.asyncio
async def test_llm_child_summary_failure_fails_only_part_row(
    monkeypatch, tmp_path, isolated_task_parts
):
    """P1-2 红：分P子任务 agent+standard 均失败 → _mark_failed 只写 task_parts
    FAILED，主行不得 FAILED。"""
    from src.main.python.sheng_wen.config.settings import config

    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.TRANSCRIBING)
    _init_one_part(task_id)
    in_file = tmp_path / "part.txt"
    out_file = tmp_path / "part_summary.md"
    in_file.write_text(SIMPLE_TRANSCRIPT, encoding="utf-8")

    retry_max = config.summarization.llm_call_retry_max
    fake = StreamingFakeLLM([[] for _ in range(retry_max + 1)])  # 分块+回退全空
    worker = LLMWorker("test", fake)
    worker.system_prompt = "你是总结助手，直接输出总结正文。"
    worker._loop = asyncio.get_running_loop()
    monkeypatch.setattr(worker, "is_task_cancelled", lambda _tid: False)

    await worker.process_task(
        {
            "task_id": task_id,
            "intermediate_file_path": str(in_file),
            "output_file": str(out_file),
            "summary_mode": "agent",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    parts = get_task_parts(task_id)
    assert parts[0]["status"] == "FAILED"
    assert "回退标准模式失败" in parts[0]["error_message"]
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.TRANSCRIBING.value, (
        f"分P子任务总结失败仍写主行 FAILED: {task['status']!r}"
    )


@pytest.mark.asyncio
async def test_multipart_parent_converges_partial_and_keeps_part_error(
    tmp_path, isolated_task_parts
):
    """P1-2：部分分P失败 → 父任务 PARTIAL（finalize 汇总收敛），
    且分P具体失败原因不被父任务 generic 文案覆盖。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)
    init_task_parts(
        task_id,
        [
            {"index": 0, "cid": 1001, "title": "P1", "duration": 60},
            {"index": 1, "cid": 1002, "title": "P2", "duration": 60},
        ],
    )
    from src.main.python.sheng_wen.task_parts import update_task_part

    update_task_part(
        task_id,
        0,
        {"status": "COMPLETED", "progress": 100, "transcript": "P1 转录"},
    )
    update_task_part(
        task_id,
        1,
        {
            "status": "FAILED",
            "progress": 0,
            "error_message": "P2 具体失败原因",
        },
    )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader.output_dir = str(tmp_path)
    downloader.process_task = lambda payload: None  # 子P已终态，跳过处理

    downloader._process_bilibili_multipart(
        {
            "task_id": task_id,
            "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
            "summary_mode": "none",
        }
    )

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.PARTIAL.value
    assert "部分分P处理失败" in task["error_message"]
    parts = get_task_parts(task_id)
    assert parts[1]["error_message"] == "P2 具体失败原因", (
        f"分P具体失败原因被覆盖: {parts[1]['error_message']!r}"
    )


@pytest.mark.asyncio
async def test_multipart_parent_fails_when_all_parts_fail(
    tmp_path, isolated_task_parts
):
    """P1-2：全部分P失败 → 父任务 FAILED（finalize 汇总收敛）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)
    init_task_parts(
        task_id,
        [
            {"index": 0, "cid": 1001, "title": "P1", "duration": 60},
            {"index": 1, "cid": 1002, "title": "P2", "duration": 60},
        ],
    )
    from src.main.python.sheng_wen.task_parts import update_task_part

    for index in (0, 1):
        update_task_part(
            task_id,
            index,
            {"status": "FAILED", "progress": 0, "error_message": f"P{index + 1} 失败"},
        )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader.output_dir = str(tmp_path)
    downloader.process_task = lambda payload: None

    downloader._process_bilibili_multipart(
        {
            "task_id": task_id,
            "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
            "summary_mode": "none",
        }
    )

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.FAILED.value
    assert "所有选中的分P均处理失败" in task["error_message"]


# ---- 评审 P1-1: b23 短链解析失败 → 分P FAILED 指引（禁止静默回退 p=1）----------


@pytest.mark.asyncio
async def test_b23_shortlink_resolve_failure_fails_child_with_guidance(
    monkeypatch, tmp_path, isolated_task_parts
):
    """P1-1 红：b23.tv 短链解析失败（重定向网络异常/412）→ 分P子任务置 FAILED
    并带"完整 BV 链接"指引，不得静默回退短链 p=1 继续下载。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    _init_one_part(task_id)
    recorder = _new_recorder()
    monkeypatch.setattr(
        dl_module, "yt_dlp", SimpleNamespace(YoutubeDL=_make_fake_ytdl(recorder))
    )

    def resolve_boom(video_url):
        raise RuntimeError("Temporary failure in name resolution")

    monkeypatch.setattr(
        VideoDownloaderWorker, "_resolve_final_url", classmethod(resolve_boom)
    )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader._loop = asyncio.get_running_loop()

    downloader.process_task(
        {
            "task_id": task_id,
            "video_url": SHORT_LINK,
            "quality": "best",
            "summary_mode": "none",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
            "bilibili_batch_child": True,
            "bilibili_parts": {"mode": "merge", "indices": [0]},
        }
    )
    await downloader._await_pending_updates(timeout=3.0)

    parts = get_task_parts(task_id)
    assert parts[0]["status"] == "FAILED", (
        f"短链解析失败未置分P FAILED: {parts[0]['status']!r}"
    )
    assert "完整 BV" in parts[0]["error_message"], parts[0]["error_message"]
    # 不得触发实际下载（禁止静默回退短链 p=1）
    assert recorder["download_calls"] == []
    # 主行保持原状（父任务由 finalize 收敛）
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.PENDING.value


# ---- 评审 P2-3 ①: multipart_overview 聚合必须仍写主行 --------------------------


@pytest.mark.asyncio
async def test_multipart_overview_aggregation_still_writes_main_row(
    monkeypatch, tmp_path, isolated_task_parts
):
    """P2-3 ① 回归（最关键）：multipart_overview 聚合路径无 multipart_part——
    流式回调与最终 summary 必须仍写主行（守卫误伤则 merge 主行永远无总结）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)
    init_task_parts(
        task_id,
        [
            {"index": 0, "cid": 1001, "title": "P1", "duration": 60},
            {"index": 1, "cid": 1002, "title": "P2", "duration": 60},
        ],
    )
    from src.main.python.sheng_wen.task_parts import update_task_part

    for index in (0, 1):
        update_task_part(
            task_id,
            index,
            {
                "status": "COMPLETED",
                "progress": 100,
                "transcript": f"P{index + 1} 转录",
                "summary": f"P{index + 1} 总结",
            },
        )
    in_file = tmp_path / "overview.txt"
    out_file = tmp_path / "overview_summary.md"
    in_file.write_text(SIMPLE_TRANSCRIPT, encoding="utf-8")

    fake = StreamingFakeLLM([["总体概览正文"]])
    worker = LLMWorker("test", fake)
    worker._loop = asyncio.get_running_loop()
    monkeypatch.setattr(worker, "is_task_cancelled", lambda _tid: False)

    await worker.process_task(
        {
            "task_id": task_id,
            "intermediate_file_path": str(in_file),
            "output_file": str(out_file),
            "summary_mode": "agent",
            "multipart_overview": True,
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED.value
    assert "总体概览" in (task["summary"] or ""), (
        f"overview 聚合未写主行 summary: {task['summary']!r}"
    )
    assert "P1 总结" in (task["summary"] or "")


# ---- 评审 P2-3 ④: 子P agent→standard 回退路径 multipart_part 透传不丢 ----------


@pytest.mark.asyncio
async def test_llm_child_agent_fallback_to_standard_keeps_part_context(
    monkeypatch, tmp_path, isolated_task_parts
):
    """P2-3 ④ 回归：分P子任务 agent 分块全失败 → 回退 standard 时 multipart_part
    透传不丢——最终总结写 task_parts，主行不被污染。"""
    from src.main.python.sheng_wen.config.settings import config

    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)
    _init_one_part(task_id)
    in_file = tmp_path / "part.txt"
    out_file = tmp_path / "part_summary.md"
    in_file.write_text(SIMPLE_TRANSCRIPT, encoding="utf-8")

    retry_max = config.summarization.llm_call_retry_max
    fake = StreamingFakeLLM([[] for _ in range(retry_max)] + [["标准回退总结正文"]])
    worker = LLMWorker("test", fake)
    worker.system_prompt = "你是总结助手，直接输出总结正文。"
    worker._loop = asyncio.get_running_loop()
    monkeypatch.setattr(worker, "is_task_cancelled", lambda _tid: False)

    await worker.process_task(
        {
            "task_id": task_id,
            "intermediate_file_path": str(in_file),
            "output_file": str(out_file),
            "summary_mode": "agent",
            "multipart_part": {"index": 0, "title": "P1", "duration": 60},
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    parts = get_task_parts(task_id)
    assert parts[0]["status"] == "COMPLETED"
    assert parts[0]["summary"] == "标准回退总结正文"
    task = db.get_task(task_id)
    assert task["summary"] is None, (
        f"回退路径 multipart_part 丢失，主行被写: {task['summary']!r}"
    )


# ---- ⑦ multipart 入口主行置 DOWNLOADING（修复后主行不再被子P轮番改写） --------


@pytest.mark.asyncio
async def test_multipart_entry_sets_main_row_downloading(
    monkeypatch, tmp_path, isolated_task_parts
):
    """⑦ 红：merge 入口应将主行从 PENDING 置 DOWNLOADING 且先于 finalize 终态
    （同步写——异步投递会落在 finalize 之后把任务卡死在 DOWNLOADING）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.PENDING)
    init_task_parts(
        task_id,
        [
            {"index": 0, "cid": 1001, "title": "P1", "duration": 60},
            {"index": 1, "cid": 1002, "title": "P2", "duration": 60},
        ],
    )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader.output_dir = str(tmp_path)
    # 子P不真正下载：拦截 process_task（分P保持 PENDING，finalize 将收敛 FAILED）
    downloader.process_task = lambda payload: None  # type: ignore[method-assign]

    writes: list[tuple[str, str]] = []
    original_update = db.update_task
    monkeypatch.setattr(
        db,
        "update_task",
        lambda tid, data: (
            writes.append(
                (
                    str(tid),
                    (data.get("status") or "").value
                    if hasattr(data.get("status"), "value")
                    else data.get("status") or "",
                )
            )
            or original_update(tid, data)
        ),
    )

    downloader._process_bilibili_multipart(
        {
            "task_id": task_id,
            "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
            "summary_mode": "none",
        }
    )

    statuses = [status for tid, status in writes if tid == task_id]
    assert statuses, "主行应有写入记录"
    assert statuses[0] == TaskStatus.DOWNLOADING.value, (
        f"入口首次写入应为 DOWNLOADING，实际: {statuses}"
    )
    # 终态（全失败 FAILED）必须在 DOWNLOADING 之后写入，不得被 DOWNLOADING 覆盖
    assert statuses.index(TaskStatus.DOWNLOADING.value) < len(statuses) - 1, (
        f"DOWNLOADING 不得是最后一次写入（否则卡死），实际序列: {statuses}"
    )


# ---- transcript-segments: finalize 合并分P segments + offset 写主行 ------------


def _completed_part_with_segments(
    task_id: str,
    index: int,
    transcript: str,
    segments: list[dict],
    audio_duration: float,
) -> None:
    """把分P置为 COMPLETED 并写入 transcript + transcript_segments（直接 sqlite，
    避免依赖 update_task_part 的 allowed 集合——本测试验证的是 finalize 行为）。"""
    from src.main.python.sheng_wen.task_parts import update_task_part
    from src.main.python.sheng_wen.transcriber.type import segments_to_json

    update_task_part(
        task_id,
        index,
        {
            "status": "COMPLETED",
            "progress": 100,
            "transcript": transcript,
            "audio_duration": audio_duration,
            "transcript_segments": segments_to_json(segments),
        },
    )


@pytest.mark.asyncio
async def test_multipart_finalize_merges_part_segments_with_offsets(
    tmp_path, isolated_task_parts
):
    """红：finalize 逐 completed part 读 segments，累加 offset（audio_duration
    or duration）写主行 transcript_segments——修前主行无该字段（分P字幕时间轴
    各自从 0 开始，合并后必须错开）。"""
    from src.main.python.sheng_wen.transcriber.type import segments_from_json

    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)
    init_task_parts(
        task_id,
        [
            {"index": 0, "cid": 1001, "title": "P1", "duration": 60},
            {"index": 1, "cid": 1002, "title": "P2", "duration": 60},
        ],
    )
    # P1 时长 60s（audio_duration 优先于 duration），segments 从 0 开始；
    # P2 时长 30s，segments 也从 0 开始——合并后 P2 应偏移 60s。
    _completed_part_with_segments(
        task_id,
        0,
        "000000P1第一句\n000010P1第二句",
        [
            {"start": 0.0, "end": 5.0, "text": "P1第一句"},
            {"start": 5.0, "end": 10.0, "text": "P1第二句", "speaker_id": "A"},
        ],
        60.0,
    )
    _completed_part_with_segments(
        task_id,
        1,
        "000000P2第一句",
        [{"start": 0.0, "end": 8.0, "text": "P2第一句"}],
        30.0,
    )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader.output_dir = str(tmp_path)
    downloader.process_task = lambda payload: None  # type: ignore[method-assign]

    downloader._process_bilibili_multipart(
        {
            "task_id": task_id,
            "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
            "summary_mode": "none",
        }
    )

    task = db.get_task(task_id)
    assert task["transcript_segments"], (
        "finalize 未合并分P segments 写主行 transcript_segments（红）"
    )
    merged = segments_from_json(task["transcript_segments"])
    assert [s.text for s in merged] == ["P1第一句", "P1第二句", "P2第一句"]
    assert merged[0].start == 0.0
    assert merged[1].start == 5.0
    assert merged[1].speaker_id == "A"
    # P2 偏移 = P1 audio_duration = 60s
    assert merged[2].start == 60.0
    assert merged[2].end == 68.0
    assert task["status"] == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_multipart_finalize_falls_back_to_hhmmss_for_parts_without_segments(
    tmp_path, isolated_task_parts
):
    """存量分P无 segments → parse_hhmmss_transcript 回退解析，offset 同样累加。"""
    from src.main.python.sheng_wen.task_parts import update_task_part
    from src.main.python.sheng_wen.transcriber.type import segments_from_json

    task_id = str(uuid.uuid4())
    _save_task(task_id, status=TaskStatus.SUMMARIZING)
    init_task_parts(
        task_id,
        [
            {"index": 0, "cid": 1001, "title": "P1", "duration": 60},
            {"index": 1, "cid": 1002, "title": "P2", "duration": 60},
        ],
    )
    for index, transcript, duration in [
        (0, "000000P1第一句\n000010P1第二句", 60.0),
        (1, "000000P2第一句", 30.0),
    ]:
        update_task_part(
            task_id,
            index,
            {
                "status": "COMPLETED",
                "progress": 100,
                "transcript": transcript,
                "audio_duration": duration,
            },
        )

    downloader = VideoDownloaderWorker("test", summary_worker=None)
    downloader.output_dir = str(tmp_path)
    downloader.process_task = lambda payload: None  # type: ignore[method-assign]

    downloader._process_bilibili_multipart(
        {
            "task_id": task_id,
            "bilibili_parts": {"mode": "merge", "indices": [0, 1]},
            "summary_mode": "none",
        }
    )

    task = db.get_task(task_id)
    merged = segments_from_json(task["transcript_segments"])
    assert [s.text for s in merged] == ["P1第一句", "P1第二句", "P2第一句"]
    # HHMMSS 回退：P1 首句 0-10s；P1 第二句 10-60+? 末行 end=start+3
    assert merged[0].start == 0.0
    assert merged[0].end == 10.0
    assert merged[1].start == 10.0
    assert merged[1].end == 13.0  # 末行 +3（在 P1 段内）
    assert merged[2].start == 60.0  # P2 offset = 60s
    assert merged[2].end == 63.0  # P2 末行 +3
