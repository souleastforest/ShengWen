from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class AudioMissingReason(str, Enum):
    """音频缺失原因（前端徽章展示用）。"""

    SUBTITLE_ONLY = "subtitle_only"  # 使用了 B 站字幕，从未下载媒体
    RECLAIMED = "reclaimed"  # 媒体文件已被回收器清理


@dataclass
class TempFileInfo:
    """temp 目录中单个文件的快照信息。"""

    path: str
    size_bytes: int
    mtime_ts: float


@dataclass
class TaskFileGroup:
    """回收单元：一个任务的媒体文件组与中间产物文件组。"""

    task_id: str
    status: str
    created_at: Optional[datetime] = None
    latest_modified_at: Optional[datetime] = None
    media_files: list[str] = field(default_factory=list)
    intermediate_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "latest_modified_at": (
                self.latest_modified_at.isoformat() if self.latest_modified_at else None
            ),
            "media_files": list(self.media_files),
            "intermediate_files": list(self.intermediate_files),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskFileGroup":
        def _parse(value: str | None) -> datetime | None:
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value)
                except ValueError:
                    return None
            return value

        return cls(
            task_id=str(data.get("task_id", "")),
            status=str(data.get("status", "")),
            created_at=_parse(data.get("created_at")),
            latest_modified_at=_parse(data.get("latest_modified_at")),
            media_files=[str(p) for p in data.get("media_files") or []],
            intermediate_files=[str(p) for p in data.get("intermediate_files") or []],
        )


@dataclass
class ReclaimResult:
    """单轮回收的结果统计。"""

    over_limit: bool = False
    usage_bytes: int = 0
    limit_bytes: int = 0
    eligible_tasks: int = 0
    reclaimed_files: int = 0
    reclaimed_bytes: int = 0
    orphan_files: int = 0
    orphan_bytes: int = 0
    marked_tasks: int = 0

    def to_dict(self) -> dict:
        return {
            "over_limit": self.over_limit,
            "usage_bytes": self.usage_bytes,
            "limit_bytes": self.limit_bytes,
            "eligible_tasks": self.eligible_tasks,
            "reclaimed_files": self.reclaimed_files,
            "reclaimed_bytes": self.reclaimed_bytes,
            "orphan_files": self.orphan_files,
            "orphan_bytes": self.orphan_bytes,
            "marked_tasks": self.marked_tasks,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ReclaimResult":
        return cls(
            over_limit=bool(data.get("over_limit", False)),
            usage_bytes=int(data.get("usage_bytes", 0)),
            limit_bytes=int(data.get("limit_bytes", 0)),
            eligible_tasks=int(data.get("eligible_tasks", 0)),
            reclaimed_files=int(data.get("reclaimed_files", 0)),
            reclaimed_bytes=int(data.get("reclaimed_bytes", 0)),
            orphan_files=int(data.get("orphan_files", 0)),
            orphan_bytes=int(data.get("orphan_bytes", 0)),
            marked_tasks=int(data.get("marked_tasks", 0)),
        )
