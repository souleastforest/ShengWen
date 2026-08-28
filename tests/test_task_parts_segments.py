"""TDD: task_parts transcript_segments 列（老表 ALTER 迁移、include_text、reset）。

契约（plan step 3）：
- CREATE TABLE IF NOT EXISTS 不给老表补列——必须显式 _ensure_task_parts_columns()
  ALTER，否则 update_task_part 对老表 UPDATE 报错/静默丢键；
- update_task_part allowed 集合含 transcript_segments；
- get_task_parts include_text=True 含该列、False 不含；
- reset_failed_parts 清空该列。
"""

import json
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

import src.main.python.sheng_wen.task_parts as task_parts_module
from src.main.python.sheng_wen.task_parts import (
    get_task_parts,
    init_task_parts,
    reset_failed_parts,
    update_task_part,
)

OLD_SCHEMA_SQL = """
CREATE TABLE task_parts (
    task_id TEXT NOT NULL,
    part_index INTEGER NOT NULL,
    cid INTEGER,
    title TEXT,
    duration REAL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    progress REAL NOT NULL DEFAULT 0,
    error_message TEXT,
    transcript TEXT,
    summary TEXT,
    audio_duration REAL,
    transcription_time REAL,
    updated_at TEXT,
    PRIMARY KEY (task_id, part_index)
)
"""


@pytest.fixture()
def parts_db(tmp_path, monkeypatch):
    """task_parts 指向独立 sqlite 文件（禁止触碰真实 ShengWen.db）。"""
    path = str(tmp_path / "parts.db")
    monkeypatch.setattr(
        task_parts_module,
        "config",
        SimpleNamespace(database=SimpleNamespace(sqlite_path=path)),
    )
    return path


def _make_old_table(path: str) -> None:
    """模拟老版本 task_parts 表（无 transcript_segments 列）。"""
    with sqlite3.connect(path) as conn:
        conn.execute(OLD_SCHEMA_SQL)
        conn.commit()


def _part_json(part: dict) -> str:
    return json.dumps(
        [
            {
                "start": part["start"],
                "end": part["end"],
                "text": part["text"],
                "speaker_id": part.get("speaker_id"),
            }
        ],
        ensure_ascii=False,
    )


def test_old_table_migrated_by_update(parts_db):
    """红：老表无 transcript_segments 列且已有存量行时，update_task_part 必须先
    ALTER 再 UPDATE（修前：allowed 集合丢键 → 静默不落库；即便 allowed 有键，
    老表 UPDATE 也会报 no such column）。"""
    _make_old_table(parts_db)
    task_id = str(uuid.uuid4())
    # 模拟老表存量行（与 _make_old_table 的 schema 对齐，无 transcript_segments）
    with sqlite3.connect(parts_db) as conn:
        conn.execute(
            "INSERT INTO task_parts "
            "(task_id, part_index, cid, title, duration, status, progress, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'COMPLETED', 100, ?)",
            (task_id, 0, 1001, "P1", 60, "now"),
        )
        conn.commit()

    update_task_part(
        task_id,
        0,
        {
            "status": "COMPLETED",
            "progress": 100,
            "transcript": "000000第一段",
            "transcript_segments": _part_json(
                {"start": 0.0, "end": 3.0, "text": "第一段"}
            ),
        },
    )

    parts = get_task_parts(task_id)
    assert parts[0]["transcript_segments"] is not None, (
        "老表迁移失败：transcript_segments 未落库（红）"
    )
    assert json.loads(parts[0]["transcript_segments"]) == [
        {"start": 0.0, "end": 3.0, "text": "第一段", "speaker_id": None}
    ]


def test_new_table_has_column(parts_db):
    """新表 CREATE 自带 transcript_segments 列。"""
    init_task_parts(
        str(uuid.uuid4()),
        [{"index": 0, "cid": 1001, "title": "P1", "duration": 60}],
    )
    with sqlite3.connect(parts_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(task_parts)")}
    assert "transcript_segments" in cols


def test_include_text_true_includes_false_excludes(parts_db):
    task_id = str(uuid.uuid4())
    init_task_parts(task_id, [{"index": 0, "cid": 1001, "title": "P1", "duration": 60}])
    update_task_part(
        task_id,
        0,
        {"status": "COMPLETED", "transcript_segments": "[]"},
    )

    detailed = get_task_parts(task_id, include_text=True)
    assert "transcript_segments" in detailed[0]
    assert detailed[0]["transcript_segments"] == "[]"

    lightweight = get_task_parts(task_id, include_text=False)
    assert "transcript_segments" not in lightweight[0]
    assert "transcript" not in lightweight[0]


def test_reset_failed_parts_clears_segments(parts_db):
    task_id = str(uuid.uuid4())
    init_task_parts(task_id, [{"index": 0, "cid": 1001, "title": "P1", "duration": 60}])
    update_task_part(
        task_id,
        0,
        {
            "status": "FAILED",
            "progress": 50,
            "transcript": "000000旧转录",
            "transcript_segments": _part_json(
                {"start": 0.0, "end": 3.0, "text": "旧转录"}
            ),
        },
    )
    assert get_task_parts(task_id)[0]["transcript_segments"] is not None

    reset_failed_parts(task_id, [0])

    part = get_task_parts(task_id)[0]
    assert part["status"] == "PENDING"
    assert part["transcript_segments"] is None
    assert part["transcript"] is None


def test_update_task_part_allowed_whitelist_still_blocks_unknown_keys(parts_db):
    """白名单回归：allowed 集合之外键仍被丢弃。"""
    task_id = str(uuid.uuid4())
    init_task_parts(task_id, [{"index": 0, "cid": 1001, "title": "P1", "duration": 60}])
    update_task_part(task_id, 0, {"status": "COMPLETED", "evil_key": "x"})
    part = get_task_parts(task_id)[0]
    assert part["status"] == "COMPLETED"
    assert "evil_key" not in part
