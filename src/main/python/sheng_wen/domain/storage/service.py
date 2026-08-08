from __future__ import annotations

from typing import Any

from loguru import logger

from src.main.python.sheng_wen.application.events.topics import (
    STORAGE_CLEANUP_COMPLETED,
)
from src.main.python.sheng_wen.domain.storage.type import (
    CleanupTrigger,
    FileRecord,
    FileType,
    StoragePolicy,
)

_CLEANUP_RULES: dict[CleanupTrigger, set[FileType]] = {
    CleanupTrigger.TASK_COMPLETED: {FileType.VIDEO, FileType.AUDIO, FileType.TEMP},
    CleanupTrigger.TASK_FAILED: {
        FileType.VIDEO,
        FileType.AUDIO,
        FileType.TRANSCRIPT,
        FileType.SUMMARY,
        FileType.TEMP,
    },
    CleanupTrigger.TASK_DELETED: {
        FileType.VIDEO,
        FileType.AUDIO,
        FileType.TRANSCRIPT,
        FileType.SUMMARY,
        FileType.TEMP,
    },
    CleanupTrigger.SCHEDULED: {
        FileType.VIDEO,
        FileType.AUDIO,
        FileType.TRANSCRIPT,
        FileType.SUMMARY,
        FileType.TEMP,
        FileType.CHUNK_DEBUG,
    },
    CleanupTrigger.MANUAL: {
        FileType.VIDEO,
        FileType.AUDIO,
        FileType.TRANSCRIPT,
        FileType.SUMMARY,
        FileType.TEMP,
        FileType.CHUNK_DEBUG,
    },
}


class StorageService:
    def __init__(
        self,
        repository: Any,
        event_bus: Any,
        policy: StoragePolicy | None = None,
    ) -> None:
        self._repo = repository
        self._bus = event_bus
        self._policy = policy or StoragePolicy()

    async def register(self, record: FileRecord) -> None:
        await self._repo.register_file(record)

    async def schedule_cleanup(self, trigger: CleanupTrigger, task_id: str) -> None:
        files = await self._repo.get_files_by_task(task_id)
        types_to_clean = _CLEANUP_RULES.get(trigger, set())
        deleted = 0

        for file_record in files:
            if file_record.file_type in types_to_clean:
                await self._repo.delete_file(file_record.path)
                deleted += 1

        await self._bus.publish(
            STORAGE_CLEANUP_COMPLETED,
            {
                "trigger": trigger.value,
                "task_id": task_id,
                "files_deleted": deleted,
            },
        )
        logger.info(
            f"[StorageService] Cleanup ({trigger.value}): "
            f"deleted {deleted} files for task {task_id}"
        )

    async def get_usage(self) -> dict[str, Any]:
        total = await self._repo.get_total_size_bytes()
        return {
            "total_bytes": total,
            "total_mb": round(total / (1024 * 1024), 2),
            "max_mb": self._policy.max_total_mb,
        }
