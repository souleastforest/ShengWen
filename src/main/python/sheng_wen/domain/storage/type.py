from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class FileType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    SUMMARY = "summary"
    TEMP = "temp"
    CHUNK_DEBUG = "chunk_debug"


class CleanupTrigger(str, Enum):
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_DELETED = "task_deleted"
    SCHEDULED = "scheduled"
    MANUAL = "manual"


@dataclass
class FileRecord:
    path: str
    task_id: str
    file_type: FileType
    size_bytes: int
    created_at: datetime
    expired_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        data = {
            "path": self.path,
            "task_id": self.task_id,
            "file_type": self.file_type.value,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if self.expired_at is not None:
            data["expired_at"] = self.expired_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "FileRecord":
        created = data.get("created_at")
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        expired = data.get("expired_at")
        if isinstance(expired, str):
            expired = datetime.fromisoformat(expired)
        return cls(
            path=data.get("path", ""),
            task_id=data.get("task_id", ""),
            file_type=FileType(data.get("file_type", "temp")),
            size_bytes=int(data.get("size_bytes", 0)),
            created_at=created,
            expired_at=expired,
        )


@dataclass
class StoragePolicy:
    max_total_mb: float = 2048
    retention_completed_sec: int = 86400
    retention_failed_sec: int = 7200
    cleanup_interval_sec: int = 600

