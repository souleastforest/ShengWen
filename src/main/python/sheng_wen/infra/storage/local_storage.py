from __future__ import annotations

import os
from datetime import datetime, timezone

from loguru import logger

from src.main.python.sheng_wen.domain.storage.type import FileRecord


class LocalStorageRepository:
    def __init__(self) -> None:
        self._records: dict[str, FileRecord] = {}

    async def register_file(self, record: FileRecord) -> None:
        self._records[record.path] = record

    async def get_files_by_task(self, task_id: str) -> list[FileRecord]:
        return [record for record in self._records.values() if record.task_id == task_id]

    async def delete_file(self, path: str) -> None:
        record = self._records.pop(path, None)
        if record and os.path.exists(path):
            os.remove(path)
            logger.info(f"[LocalStorage] Deleted: {path}")

    async def get_total_size_bytes(self) -> int:
        return sum(record.size_bytes for record in self._records.values())

    async def get_expired_files(self) -> list[FileRecord]:
        now = datetime.now(timezone.utc)
        return [
            record
            for record in self._records.values()
            if record.expired_at and record.expired_at <= now
        ]

