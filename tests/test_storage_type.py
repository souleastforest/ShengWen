"""storage 域公共类型：AudioMissingReason/TaskFileGroup/ReclaimResult 字典往返。"""

from datetime import datetime, timezone

from src.main.python.sheng_wen.domain.storage.type import (
    AudioMissingReason,
    ReclaimResult,
    TaskFileGroup,
)


def test_audio_missing_reason_values():
    assert AudioMissingReason.SUBTITLE_ONLY.value == "subtitle_only"
    assert AudioMissingReason.RECLAIMED.value == "reclaimed"


def test_task_file_group_roundtrip():
    group = TaskFileGroup(
        task_id="t1",
        status="COMPLETED",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        latest_modified_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        media_files=["temp/t1.mp3"],
        intermediate_files=["temp/t1_subtitle.txt"],
    )
    data = group.to_dict()
    restored = TaskFileGroup.from_dict(data)
    assert restored.task_id == "t1"
    assert restored.status == "COMPLETED"
    assert restored.created_at == group.created_at
    assert restored.latest_modified_at == group.latest_modified_at
    assert restored.media_files == ["temp/t1.mp3"]
    assert restored.intermediate_files == ["temp/t1_subtitle.txt"]


def test_task_file_group_from_dict_tolerant():
    """缺省/畸形输入应容错返回最小安全视图。"""
    restored = TaskFileGroup.from_dict({})
    assert restored.task_id == ""
    assert restored.created_at is None
    assert restored.media_files == []


def test_reclaim_result_roundtrip():
    result = ReclaimResult(
        over_limit=True,
        usage_bytes=2048,
        limit_bytes=1024,
        eligible_tasks=2,
        reclaimed_files=3,
        reclaimed_bytes=512,
        orphan_files=1,
        orphan_bytes=128,
        marked_tasks=2,
    )
    restored = ReclaimResult.from_dict(result.to_dict())
    assert restored == result


def test_reclaim_result_defaults():
    result = ReclaimResult()
    assert result.over_limit is False
    assert result.reclaimed_files == 0
    assert result.marked_tasks == 0
