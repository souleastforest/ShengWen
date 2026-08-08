from datetime import datetime, timezone

from src.main.python.sheng_wen.domain.storage.type import (
    FileRecord,
    FileType,
    StoragePolicy,
)


def test_file_record_roundtrip():
    record = FileRecord(
        path="/tmp/audio.mp3",
        task_id="t1",
        file_type=FileType.AUDIO,
        size_bytes=1024,
        created_at=datetime.now(timezone.utc),
    )
    data = record.to_dict()
    restored = FileRecord.from_dict(data)
    assert restored.path == "/tmp/audio.mp3"
    assert restored.file_type == FileType.AUDIO


def test_storage_policy_defaults():
    policy = StoragePolicy()
    assert policy.max_total_mb == 2048
    assert policy.retention_completed_sec == 86400
