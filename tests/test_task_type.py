from src.main.python.sheng_wen.domain.task.type import TaskState, TaskStatus


def test_task_status_enum_values():
    assert TaskStatus.PENDING == "PENDING"
    assert TaskStatus.COMPLETED == "COMPLETED"
    assert TaskStatus.PARTIAL == "PARTIAL"
    assert TaskStatus.FAILED == "FAILED"


def test_task_status_has_eight_values():
    # 与 db.TaskStatus 对齐（8 值）
    from src.main.python.sheng_wen.db import TaskStatus as DbTaskStatus

    assert {s.value for s in TaskStatus} == {s.value for s in DbTaskStatus}


def test_task_state_to_dict_roundtrip():
    state = TaskState(task_id="abc", video_url="http://x", status=TaskStatus.PENDING)
    data = state.to_dict()
    restored = TaskState.from_dict(data)
    assert restored.task_id == state.task_id
    assert restored.status == state.status


def test_task_state_from_dict_missing_fields():
    restored = TaskState.from_dict(
        {"task_id": "x", "video_url": "http://x", "status": "PENDING"}
    )
    assert restored.progress == 0.0
