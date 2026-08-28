"""TDD: transcriber_worker 持久化 transcript_segments（主行 + 分P + 白名单）。

契约（plan step 4）：
- 主行更新（transcriber_worker.py:778-790）与分P更新（:759-768）的 update_data
  都带 "transcript_segments": segments_to_json(result.segments)；
- 分P子任务只写 task_parts（P1-2 铁律：主行只准 finalize 写）；
- HHMMSS txt（transcript 文本格式）保持不变（chunker 依赖）；
- 白名单：ASR 多余键（language/avg_logprob 等）不落库。
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.task_parts import get_task_parts, init_task_parts
from src.main.python.sheng_wen.transcriber.transcriber import TranscriptionResult
from src.main.python.sheng_wen.transcriber.transcriber_worker import TranscriberWorker
from src.main.python.sheng_wen.transcriber.type import Segment, segments_from_json


@pytest.fixture()
def isolated_task_parts(tmp_path, monkeypatch):
    """task_parts 走独立 sqlite（与 test_multipart_main_row_leak 同模式）。"""
    import src.main.python.sheng_wen.task_parts as task_parts_module

    monkeypatch.setattr(
        task_parts_module,
        "config",
        SimpleNamespace(
            database=SimpleNamespace(sqlite_path=str(tmp_path / "parts.db"))
        ),
    )


def _save_task(task_id: str) -> None:
    db.save_task(
        task_id,
        {
            "id": task_id,
            "video_url": "https://example.com/video.mp4",
            "status": TaskStatus.PENDING,
            "summary_mode": "none",
            "created_at": datetime.now(timezone.utc),
            "latest_modified_at": datetime.now(timezone.utc),
            "progress": 0.0,
            "title": "",
        },
    )


def _result(segments: list[dict]) -> TranscriptionResult:
    return TranscriptionResult(
        segments=segments,
        transcription_time=0.5,
        real_time_factor=0.1,
        total_time=0.6,
        model_load_time=0.2,
        audio_duration=60.0,
        language="zh",
        language_probability=0.9,
    )


def _make_worker(result: TranscriptionResult) -> TranscriberWorker:
    worker = TranscriberWorker("test", object(), None)
    worker._loop = None
    worker._transcribe_audio_with_chunking = lambda *a, **kw: result
    return worker


@pytest.mark.asyncio
async def test_main_row_update_carries_segments(tmp_path):
    """主行 update_data 携带 transcript_segments（红：现状 update_data 无该键）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    audio_file = tmp_path / "task.mp3"
    audio_file.write_bytes(b"fake-audio")
    out_file = tmp_path / "task_summary.md"

    worker = _make_worker(
        _result([{"start": 1.0, "end": 2.0, "text": "你好", "speaker_id": "A"}])
    )
    worker._loop = asyncio.get_running_loop()

    worker.process_task(
        {
            "task_id": task_id,
            "audio_file": str(audio_file),
            "output_file": str(out_file),
            "summary_mode": "none",
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["transcript_segments"], (
        "主行转录完成更新未携带 transcript_segments（红）"
    )
    assert segments_from_json(task["transcript_segments"]) == [
        Segment(1.0, 2.0, "你好", speaker_id="A")
    ]


@pytest.mark.asyncio
async def test_main_row_transcript_txt_format_unchanged(tmp_path):
    """HHMMSS txt 格式不变：transcript 仍为 HHMMSS+text 行（chunker 依赖）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    audio_file = tmp_path / "task.mp3"
    audio_file.write_bytes(b"fake-audio")
    out_file = tmp_path / "task_summary.md"

    worker = _make_worker(_result([{"start": 1.0, "end": 2.0, "text": "你好"}]))
    worker._loop = asyncio.get_running_loop()

    worker.process_task(
        {
            "task_id": task_id,
            "audio_file": str(audio_file),
            "output_file": str(out_file),
            "summary_mode": "none",
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    assert task["transcript"] == "000001你好\n"
    assert task["status"] == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_multipart_update_carries_segments(tmp_path, isolated_task_parts):
    """分P update_task_part 携带 transcript_segments（红：allowed 集合丢键）。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    init_task_parts(task_id, [{"index": 0, "cid": 1001, "title": "P1", "duration": 60}])
    audio_file = tmp_path / "part.mp3"
    audio_file.write_bytes(b"fake-audio")
    out_file = tmp_path / "p1_summary.md"

    worker = _make_worker(
        _result([{"start": 0.0, "end": 5.0, "text": "分P转录", "speaker_id": "B"}])
    )
    worker._loop = asyncio.get_running_loop()

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
    assert parts[0]["transcript_segments"], (
        "分P更新未携带 transcript_segments（红：allowed 集合丢键）"
    )
    assert segments_from_json(parts[0]["transcript_segments"]) == [
        Segment(0.0, 5.0, "分P转录", speaker_id="B")
    ]

    # P1-2 铁律：分P子任务不得写主行 transcript/status
    task = db.get_task(task_id)
    assert task["status"] == TaskStatus.PENDING.value
    assert task["transcript"] is None


@pytest.mark.asyncio
async def test_extra_segment_keys_not_persisted(tmp_path):
    """白名单：ASR 多余键（language/avg_logprob）不落库。"""
    task_id = str(uuid.uuid4())
    _save_task(task_id)
    audio_file = tmp_path / "task.mp3"
    audio_file.write_bytes(b"fake-audio")
    out_file = tmp_path / "task_summary.md"

    worker = _make_worker(
        _result(
            [
                {
                    "start": 1.0,
                    "end": 2.0,
                    "text": "你好",
                    "speaker_id": "A",
                    "language": "zh",
                    "avg_logprob": -0.5,
                    "no_speech_prob": 0.01,
                }
            ]
        )
    )
    worker._loop = asyncio.get_running_loop()

    worker.process_task(
        {
            "task_id": task_id,
            "audio_file": str(audio_file),
            "output_file": str(out_file),
            "summary_mode": "none",
        }
    )
    await worker._await_pending_updates(timeout=3.0)

    task = db.get_task(task_id)
    stored = json.loads(task["transcript_segments"])
    assert stored == [{"start": 1.0, "end": 2.0, "text": "你好", "speaker_id": "A"}]
    assert "language" not in json.dumps(stored, ensure_ascii=False)
    assert "avg_logprob" not in json.dumps(stored, ensure_ascii=False)
