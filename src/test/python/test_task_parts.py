from src.main.python.sheng_wen import task_parts


def test_task_parts_lifecycle(monkeypatch, tmp_path):
    database_path = tmp_path / "parts.db"
    monkeypatch.setattr(task_parts, "_db_path", lambda: str(database_path))

    task_id = "test-multipart"
    task_parts.ensure_task_parts_table()
    task_parts.init_task_parts(
        task_id,
        [
            {"index": 0, "cid": 11, "title": "第一集", "duration": 60},
            {"index": 1, "cid": 12, "title": "第二集", "duration": 90},
        ],
    )

    assert task_parts.get_task_part_stats(task_id)["part_count"] == 2
    task_parts.update_task_part(
        task_id,
        0,
        {"status": "FAILED", "error_message": "模拟失败"},
    )
    assert task_parts.get_task_part_stats(task_id)["part_failed"] == 1

    task_parts.reset_failed_parts(task_id, [0])
    part = task_parts.get_task_part(task_id, 0)
    assert part["status"] == "PENDING"
    assert part["error_message"] is None

    task_parts.delete_task_parts(task_id)
    assert task_parts.get_task_parts(task_id) == []
