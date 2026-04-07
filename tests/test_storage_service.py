from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.main.python.sheng_wen.domain.storage.service import StorageService
from src.main.python.sheng_wen.domain.storage.type import (
    CleanupTrigger,
    FileRecord,
    FileType,
)


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.delete_file = AsyncMock()
    repo.get_files_by_task = AsyncMock(
        return_value=[
            FileRecord(
                path="/tmp/video.mp4",
                task_id="t1",
                file_type=FileType.VIDEO,
                size_bytes=1024,
                created_at=datetime.now(timezone.utc),
            ),
        ]
    )
    repo.get_total_size_bytes = AsyncMock(return_value=512)
    return repo


@pytest.fixture
def mock_bus():
    return AsyncMock()


@pytest.mark.asyncio
async def test_register_tracks_file(mock_repo, mock_bus):
    svc = StorageService(repository=mock_repo, event_bus=mock_bus)
    record = FileRecord(
        path="/tmp/a.mp3",
        task_id="t1",
        file_type=FileType.AUDIO,
        size_bytes=2048,
        created_at=datetime.now(timezone.utc),
    )

    await svc.register(record)

    mock_repo.register_file.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_deletes_video(mock_repo, mock_bus):
    svc = StorageService(repository=mock_repo, event_bus=mock_bus)

    await svc.schedule_cleanup(CleanupTrigger.TASK_COMPLETED, "t1")

    mock_repo.delete_file.assert_called()


@pytest.mark.asyncio
async def test_get_usage(mock_repo, mock_bus):
    svc = StorageService(repository=mock_repo, event_bus=mock_bus)

    usage = await svc.get_usage()

    assert usage["total_bytes"] == 512
