from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    UPLOADING = "UPLOADING"
    TRANSCRIBING = "TRANSCRIBING"
    SUMMARIZING = "SUMMARIZING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass
class TaskState:
    task_id: str
    video_url: str
    status: TaskStatus
    progress: float = 0.0
    title: Optional[str] = None
    transcript: Optional[str] = None
    summary: Optional[str] = None
    error_message: Optional[str] = None
    audio_duration: Optional[float] = None
    transcription_time: Optional[float] = None
    topic: Optional[str] = None
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    summary_mode: Optional[str] = None
    summary_chunk_total: Optional[int] = None
    summary_chunk_done: Optional[int] = None
    summary_meta: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    _FIELDS = (
        "task_id",
        "video_url",
        "status",
        "progress",
        "title",
        "transcript",
        "summary",
        "error_message",
        "audio_duration",
        "transcription_time",
        "topic",
        "author_name",
        "author_url",
        "summary_mode",
        "summary_chunk_total",
        "summary_chunk_done",
        "summary_meta",
        "created_at",
        "updated_at",
    )

    def to_dict(self) -> dict:
        d = {}
        for field_name in self._FIELDS:
            val = getattr(self, field_name, None)
            if val is not None:
                if isinstance(val, Enum):
                    val = val.value
                d[field_name] = val
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TaskState":
        safe = dict(data)
        if "status" in safe and isinstance(safe["status"], str):
            try:
                safe["status"] = TaskStatus(safe["status"])
            except ValueError:
                safe["status"] = TaskStatus.PENDING
        if "id" in safe and "task_id" not in safe:
            safe["task_id"] = safe.pop("id")
        defaults = {
            "task_id": "",
            "video_url": "",
            "status": TaskStatus.PENDING,
            "progress": 0.0,
        }
        for field_name in cls._FIELDS:
            if field_name not in safe:
                safe[field_name] = defaults.get(field_name)
        safe = {field_name: safe.get(field_name) for field_name in cls._FIELDS}
        return cls(**safe)
