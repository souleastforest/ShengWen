import json
import os
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any

from loguru import logger
from sqlalchemy import (
    create_engine,
    Column,
    String,
    Float,
    Integer,
    Text,
    DateTime,
    Boolean,
    Enum as SQLEnum,
    inspect,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

from .config.settings import config

Base = declarative_base()


def _format_utc_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    UPLOADING = "UPLOADING"
    TRANSCRIBING = "TRANSCRIBING"
    SUMMARIZING = "SUMMARIZING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class TaskModel(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True)
    video_url = Column(String, nullable=False)
    status = Column(SQLEnum(TaskStatus), default=TaskStatus.PENDING)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    latest_modified_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    progress = Column(Float, default=0.0)
    title = Column(String, nullable=True)
    transcript = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    audio_duration = Column(Float, nullable=True)
    transcription_time = Column(Float, nullable=True)
    topic = Column(String, nullable=True)
    author_name = Column(String, nullable=True)
    author_url = Column(String, nullable=True)
    summary_mode = Column(String, nullable=True)
    summary_chunk_total = Column(Integer, nullable=True)
    summary_chunk_done = Column(Integer, nullable=True)
    summary_meta = Column(Text, nullable=True)
    audio_downloaded = Column(Boolean, nullable=True)
    audio_missing_reason = Column(String, nullable=True)
    # 仅转录模式的"总结标题"开关（创建时快照，供 re-transcribe 等重跑恢复）
    generate_topic = Column(Boolean, nullable=True)
    # 本地上传任务的原始文件名（None 表示非上传任务）
    source_name = Column(String, nullable=True)
    # ASR 分片进度（仅转录阶段非空；转录完成转 SUMMARIZING 时置 None，
    # 与 summary_chunk_* 对称）。供前端展示"转录分片 done/total"。
    asr_chunk_total = Column(Integer, nullable=True)
    asr_chunk_done = Column(Integer, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "video_url": self.video_url,
            "status": self.status.value
            if isinstance(self.status, TaskStatus)
            else self.status,
            "created_at": _format_utc_timestamp(self.created_at),
            "latest_modified_at": _format_utc_timestamp(self.latest_modified_at),
            "progress": self.progress,
            "title": self.title,
            "transcript": self.transcript,
            "summary": self.summary,
            "error_message": self.error_message,
            "audio_duration": self.audio_duration,
            "transcription_time": self.transcription_time,
            "topic": self.topic,
            "author_name": self.author_name,
            "author_url": self.author_url,
            "summary_mode": self.summary_mode,
            "summary_chunk_total": self.summary_chunk_total,
            "summary_chunk_done": self.summary_chunk_done,
            "summary_meta": self.summary_meta,
            "audio_downloaded": self.audio_downloaded,
            "audio_missing_reason": self.audio_missing_reason,
            "generate_topic": self.generate_topic,
            "source_name": self.source_name,
            "asr_chunk_total": self.asr_chunk_total,
            "asr_chunk_done": self.asr_chunk_done,
        }


class TaskDB:
    def __init__(
        self,
        file_path: str = "tasks.json",
        sqlite_path: str = "ShengWen.db",
    ):
        self.file_path = file_path
        self.use_db = False

        try:
            self.engine = create_engine(f"sqlite:///{sqlite_path}", echo=False)
            Base.metadata.create_all(self.engine)
            self._ensure_schema()
            self.SessionLocal = sessionmaker(bind=self.engine)
            self.use_db = True
            logger.info(f"Using SQLite database: {sqlite_path}")
            self._migrate_from_file_if_needed()
        except Exception as e:
            logger.error(
                f"Failed to initialize SQLite: {e}. Falling back to JSON file storage."
            )
            self.use_db = False
            self._load_from_file()

    def _ensure_schema(self):
        """Add backward-compatible columns for existing databases."""
        try:
            inspector = inspect(self.engine)
            columns = {col["name"] for col in inspector.get_columns("tasks")}
            has_latest_modified_at = "latest_modified_at" in columns
            has_author_name = "author_name" in columns
            has_author_url = "author_url" in columns
            has_summary_mode = "summary_mode" in columns
            has_summary_chunk_total = "summary_chunk_total" in columns
            has_summary_chunk_done = "summary_chunk_done" in columns
            has_summary_meta = "summary_meta" in columns
            has_audio_downloaded = "audio_downloaded" in columns
            has_audio_missing_reason = "audio_missing_reason" in columns
            has_generate_topic = "generate_topic" in columns
            has_source_name = "source_name" in columns
            has_asr_chunk_total = "asr_chunk_total" in columns
            has_asr_chunk_done = "asr_chunk_done" in columns
            if (
                has_latest_modified_at
                and has_author_name
                and has_author_url
                and has_summary_mode
                and has_summary_chunk_total
                and has_summary_chunk_done
                and has_summary_meta
                and has_audio_downloaded
                and has_audio_missing_reason
                and has_generate_topic
                and has_source_name
                and has_asr_chunk_total
                and has_asr_chunk_done
            ):
                return

            with self.engine.begin() as conn:
                if not has_latest_modified_at:
                    conn.execute(
                        text("ALTER TABLE tasks ADD COLUMN latest_modified_at DATETIME")
                    )
                    conn.execute(
                        text(
                            "UPDATE tasks SET latest_modified_at = created_at WHERE latest_modified_at IS NULL"
                        )
                    )
                    logger.info(
                        "Database schema updated: added tasks.latest_modified_at"
                    )
                if not has_author_name:
                    conn.execute(
                        text("ALTER TABLE tasks ADD COLUMN author_name VARCHAR")
                    )
                    logger.info("Database schema updated: added tasks.author_name")
                if not has_author_url:
                    conn.execute(
                        text("ALTER TABLE tasks ADD COLUMN author_url VARCHAR")
                    )
                    logger.info("Database schema updated: added tasks.author_url")
                if not has_summary_mode:
                    conn.execute(
                        text("ALTER TABLE tasks ADD COLUMN summary_mode VARCHAR")
                    )
                    logger.info("Database schema updated: added tasks.summary_mode")
                if not has_summary_chunk_total:
                    conn.execute(
                        text("ALTER TABLE tasks ADD COLUMN summary_chunk_total INTEGER")
                    )
                    logger.info(
                        "Database schema updated: added tasks.summary_chunk_total"
                    )
                if not has_summary_chunk_done:
                    conn.execute(
                        text("ALTER TABLE tasks ADD COLUMN summary_chunk_done INTEGER")
                    )
                    logger.info(
                        "Database schema updated: added tasks.summary_chunk_done"
                    )
                if not has_summary_meta:
                    conn.execute(text("ALTER TABLE tasks ADD COLUMN summary_meta TEXT"))
                    logger.info("Database schema updated: added tasks.summary_meta")
                if not has_audio_downloaded:
                    conn.execute(
                        text("ALTER TABLE tasks ADD COLUMN audio_downloaded BOOLEAN")
                    )
                    logger.info("Database schema updated: added tasks.audio_downloaded")
                if not has_audio_missing_reason:
                    conn.execute(
                        text(
                            "ALTER TABLE tasks ADD COLUMN audio_missing_reason VARCHAR"
                        )
                    )
                    logger.info(
                        "Database schema updated: added tasks.audio_missing_reason"
                    )
                if not has_generate_topic:
                    conn.execute(
                        text(
                            "ALTER TABLE tasks ADD COLUMN generate_topic BOOLEAN DEFAULT 1"
                        )
                    )
                    # 老任务（无该字段）按默认开启（True）回填，与新任务缺省语义一致
                    conn.execute(
                        text(
                            "UPDATE tasks SET generate_topic = 1 WHERE generate_topic IS NULL"
                        )
                    )
                    logger.info(
                        "Database schema updated: added tasks.generate_topic (legacy tasks default True)"
                    )
                if not has_source_name:
                    conn.execute(
                        text("ALTER TABLE tasks ADD COLUMN source_name VARCHAR")
                    )
                    logger.info("Database schema updated: added tasks.source_name")
                if not has_asr_chunk_total:
                    conn.execute(
                        text("ALTER TABLE tasks ADD COLUMN asr_chunk_total INTEGER")
                    )
                    logger.info("Database schema updated: added tasks.asr_chunk_total")
                if not has_asr_chunk_done:
                    conn.execute(
                        text("ALTER TABLE tasks ADD COLUMN asr_chunk_done INTEGER")
                    )
                    logger.info("Database schema updated: added tasks.asr_chunk_done")
        except Exception as e:
            logger.error(f"Failed to ensure database schema: {e}")

    def _load_from_file(self):
        self._memory_db: Dict[str, str] = {}
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    self._memory_db = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load tasks from file: {e}")

    def _save_to_file(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self._memory_db, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save tasks to file: {e}")

    def _migrate_from_file_if_needed(self):
        """Migrate data from tasks.json to SQLite if file exists and DB is empty"""
        if not os.path.exists(self.file_path):
            return

        session: Session = self.SessionLocal()
        try:
            count = session.query(TaskModel).count()
            if count > 0:
                return  # Already has data, skip migration

            with open(self.file_path, "r", encoding="utf-8") as f:
                file_data = json.load(f)

            for task_id, task_json in file_data.items():
                task_data = json.loads(task_json)
                task = TaskModel(
                    id=task_data.get("id", task_id),
                    video_url=task_data["video_url"],
                    status=TaskStatus(task_data.get("status", "PENDING")),
                    created_at=datetime.fromisoformat(task_data["created_at"])
                    if task_data.get("created_at")
                    else datetime.now(timezone.utc),
                    latest_modified_at=datetime.fromisoformat(
                        task_data["latest_modified_at"]
                    )
                    if task_data.get("latest_modified_at")
                    else datetime.fromisoformat(task_data["created_at"])
                    if task_data.get("created_at")
                    else datetime.now(timezone.utc),
                    progress=task_data.get("progress", 0.0),
                    title=task_data.get("title"),
                    transcript=task_data.get("transcript"),
                    summary=task_data.get("summary"),
                    error_message=task_data.get("error_message"),
                    audio_duration=task_data.get("audio_duration"),
                    transcription_time=task_data.get("transcription_time"),
                    topic=task_data.get("topic"),
                    author_name=task_data.get("author_name"),
                    author_url=task_data.get("author_url"),
                    summary_mode=task_data.get("summary_mode"),
                    summary_chunk_total=task_data.get("summary_chunk_total"),
                    summary_chunk_done=task_data.get("summary_chunk_done"),
                    summary_meta=task_data.get("summary_meta"),
                    # 老任务无该字段时按默认开启（True）迁移
                    generate_topic=task_data.get("generate_topic", True),
                )
                session.add(task)

            session.commit()
            logger.info(
                f"Migrated {len(file_data)} tasks from {self.file_path} to database"
            )
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to migrate data from file: {e}")
        finally:
            session.close()

    def _serialize_task(self, task_data: Dict[str, Any]) -> str:
        data = task_data.copy()
        if isinstance(data.get("created_at"), datetime):
            data["created_at"] = data["created_at"].isoformat()
        if isinstance(data.get("latest_modified_at"), datetime):
            data["latest_modified_at"] = data["latest_modified_at"].isoformat()
        return json.dumps(data)

    def _deserialize_task(self, task_json: str) -> Dict[str, Any]:
        data = json.loads(task_json)
        if data.get("created_at"):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data.get("latest_modified_at"):
            data["latest_modified_at"] = datetime.fromisoformat(
                data["latest_modified_at"]
            )
        elif data.get("created_at"):
            data["latest_modified_at"] = data["created_at"]
        return data

    def save_task(self, task_id: str, task_data: Dict[str, Any]):
        if self.use_db:
            session: Session = self.SessionLocal()
            try:
                task = session.query(TaskModel).filter(TaskModel.id == task_id).first()
                if task:
                    for key, value in task_data.items():
                        if key == "status" and isinstance(value, str):
                            value = TaskStatus(value)
                        elif key == "created_at" and isinstance(value, str):
                            value = datetime.fromisoformat(value)
                        elif key == "latest_modified_at" and isinstance(value, str):
                            value = datetime.fromisoformat(value)
                        setattr(task, key, value)
                else:
                    task_data_copy = task_data.copy()
                    if "status" in task_data_copy and isinstance(
                        task_data_copy["status"], str
                    ):
                        task_data_copy["status"] = TaskStatus(task_data_copy["status"])
                    if "created_at" in task_data_copy and isinstance(
                        task_data_copy["created_at"], str
                    ):
                        task_data_copy["created_at"] = datetime.fromisoformat(
                            task_data_copy["created_at"]
                        )
                    if "latest_modified_at" in task_data_copy and isinstance(
                        task_data_copy["latest_modified_at"], str
                    ):
                        task_data_copy["latest_modified_at"] = datetime.fromisoformat(
                            task_data_copy["latest_modified_at"]
                        )
                    if "latest_modified_at" not in task_data_copy:
                        task_data_copy["latest_modified_at"] = task_data_copy.get(
                            "created_at", datetime.now(timezone.utc)
                        )

                    # 确保 'id' 不在 task_data_copy 中，因为它已经作为关键字参数传递
                    task_data_copy.pop("id", None)

                    task = TaskModel(id=task_id, **task_data_copy)
                    session.add(task)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to save task to database: {e}")
            finally:
                session.close()
        else:
            serialized = self._serialize_task(task_data)
            self._memory_db[task_id] = serialized
            self._save_to_file()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        if self.use_db:
            session: Session = self.SessionLocal()
            try:
                task = session.query(TaskModel).filter(TaskModel.id == task_id).first()
                return task.to_dict() if task else None
            finally:
                session.close()
        else:
            serialized = self._memory_db.get(task_id)
            if serialized:
                return self._deserialize_task(serialized)
            return None

    def list_tasks(self) -> List[Dict[str, Any]]:
        if self.use_db:
            session: Session = self.SessionLocal()
            try:
                tasks = (
                    session.query(TaskModel).order_by(TaskModel.created_at.desc()).all()
                )
                return [task.to_dict() for task in tasks]
            finally:
                session.close()
        else:
            all_tasks = self._memory_db.values()
            return [self._deserialize_task(t) for t in all_tasks]

    def delete_task(self, task_id: str):
        if self.use_db:
            session: Session = self.SessionLocal()
            try:
                task = session.query(TaskModel).filter(TaskModel.id == task_id).first()
                if task:
                    session.delete(task)
                    session.commit()
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to delete task from database: {e}")
            finally:
                session.close()
        else:
            if task_id in self._memory_db:
                del self._memory_db[task_id]
                self._save_to_file()

    def update_task(self, task_id: str, updates: Dict[str, Any]):
        task = self.get_task(task_id)
        if task:
            task.update(updates)
            # 进度更新频繁，不纳入“最新修改时间”；其他字段更新都视为一次修改。
            if any(key != "progress" for key in updates.keys()):
                task["latest_modified_at"] = datetime.now(timezone.utc)
            self.save_task(task_id, task)
            return task
        return None

    def recover_interrupted_tasks(self) -> int:
        """
        启动恢复：将上次异常中断遗留在中间态的任务标记为 FAILED。
        返回变更数量。
        """
        interrupted_statuses = {
            TaskStatus.DOWNLOADING.value,
            TaskStatus.UPLOADING.value,
            TaskStatus.TRANSCRIBING.value,
            TaskStatus.SUMMARIZING.value,
        }
        recover_msg = "服务重启导致任务中断，请重试（可使用重新转录/重新总结）。"

        updated = 0
        for task in self.list_tasks():
            status = str(task.get("status") or "").upper()
            if status in interrupted_statuses:
                self.update_task(
                    task["id"],
                    {
                        "status": TaskStatus.FAILED,
                        "error_message": recover_msg,
                    },
                )
                updated += 1

        return updated


# Global instance
db = TaskDB(
    file_path=config.database.json_file_path,
    sqlite_path=config.database.sqlite_path,
)
