from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .config.settings import config


PART_STATUSES = {
    "PENDING",
    "DOWNLOADING",
    "TRANSCRIBING",
    "SUMMARIZING",
    "COMPLETED",
    "FAILED",
}


def _db_path() -> str:
    return str(config.database.sqlite_path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_task_parts_columns() -> None:
    """为老版本 task_parts 表显式补列（CREATE TABLE IF NOT EXISTS 不会给已存在
    的表补列——必须先 ALTER，否则对老表 UPDATE 新列会报 no such column /
    静默丢键）。"""
    with sqlite3.connect(_db_path()) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(task_parts)")}
        if "transcript_segments" not in columns:
            conn.execute("ALTER TABLE task_parts ADD COLUMN transcript_segments TEXT")
        conn.commit()


def ensure_task_parts_table() -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_parts (
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
                transcript_segments TEXT,
                updated_at TEXT,
                PRIMARY KEY (task_id, part_index)
            )
            """
        )
        conn.commit()
    _ensure_task_parts_columns()


def init_task_parts(task_id: str, parts: Iterable[dict[str, Any]]) -> None:
    ensure_task_parts_table()
    with sqlite3.connect(_db_path()) as conn:
        for part in parts:
            conn.execute(
                """
                INSERT INTO task_parts
                    (task_id, part_index, cid, title, duration, status, progress, updated_at)
                VALUES (?, ?, ?, ?, ?, 'PENDING', 0, ?)
                ON CONFLICT(task_id, part_index) DO UPDATE SET
                    cid=excluded.cid,
                    title=excluded.title,
                    duration=excluded.duration
                """,
                (
                    task_id,
                    int(part["index"]),
                    part.get("cid"),
                    str(part.get("title") or ""),
                    float(part.get("duration") or 0),
                    _now(),
                ),
            )
        conn.commit()


def get_task_parts(task_id: str, include_text: bool = True) -> list[dict[str, Any]]:
    ensure_task_parts_table()
    columns = (
        "task_id, part_index, cid, title, duration, status, progress, error_message, "
        "transcript, transcript_segments, summary, audio_duration, transcription_time, "
        "updated_at"
    )
    if not include_text:
        columns = (
            "task_id, part_index, cid, title, duration, status, progress, "
            "error_message, audio_duration, transcription_time, updated_at"
        )
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {columns} FROM task_parts WHERE task_id=? ORDER BY part_index",
            (task_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_task_part(
    task_id: str, part_index: int, updates: dict[str, Any]
) -> dict[str, Any] | None:
    ensure_task_parts_table()
    allowed = {
        "cid",
        "title",
        "duration",
        "status",
        "progress",
        "error_message",
        "transcript",
        "transcript_segments",
        "summary",
        "audio_duration",
        "transcription_time",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        parts = get_task_parts(task_id)
        return next(
            (part for part in parts if part["part_index"] == int(part_index)), None
        )
    if "status" in values and str(values["status"]) not in PART_STATUSES:
        raise ValueError(f"invalid part status: {values['status']}")
    values["updated_at"] = _now()
    assignments = ", ".join(f"{key}=?" for key in values)
    params = list(values.values()) + [task_id, int(part_index)]
    with sqlite3.connect(_db_path()) as conn:
        cursor = conn.execute(
            f"UPDATE task_parts SET {assignments} WHERE task_id=? AND part_index=?",
            params,
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
    parts = get_task_parts(task_id)
    return next((part for part in parts if part["part_index"] == int(part_index)), None)


def get_task_part(task_id: str, part_index: int) -> dict[str, Any] | None:
    return next(
        (
            part
            for part in get_task_parts(task_id)
            if part["part_index"] == int(part_index)
        ),
        None,
    )


def get_task_part_stats(task_id: str) -> dict[str, Any]:
    parts = get_task_parts(task_id, include_text=False)
    completed = sum(part["status"] == "COMPLETED" for part in parts)
    failed = sum(part["status"] == "FAILED" for part in parts)
    active = next(
        (
            part
            for part in parts
            if part["status"] in {"DOWNLOADING", "TRANSCRIBING", "SUMMARIZING"}
        ),
        None,
    )
    return {
        "part_count": len(parts),
        "part_completed": completed,
        "part_failed": failed,
        "current_part": active["part_index"] if active else None,
        "has_parts": bool(parts),
    }


def reset_failed_parts(task_id: str, indices: Iterable[int]) -> list[int]:
    ensure_task_parts_table()
    normalized = sorted({int(index) for index in indices})
    with sqlite3.connect(_db_path()) as conn:
        for index in normalized:
            conn.execute(
                """
                UPDATE task_parts
                SET status='PENDING', progress=0, error_message=NULL,
                    transcript=NULL, transcript_segments=NULL, summary=NULL,
                    audio_duration=NULL, transcription_time=NULL, updated_at=?
                WHERE task_id=? AND part_index=?
                """,
                (_now(), task_id, index),
            )
        conn.commit()
    return normalized


def delete_task_parts(task_id: str) -> None:
    ensure_task_parts_table()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute("DELETE FROM task_parts WHERE task_id=?", (task_id,))
        conn.commit()


ensure_task_parts_table()
