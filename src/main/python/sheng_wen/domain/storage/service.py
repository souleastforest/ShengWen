from __future__ import annotations

import inspect
import os
from datetime import datetime, timezone
from typing import Any, Callable

from loguru import logger

from src.main.python.sheng_wen.config.settings import StorageConfig, config
from src.main.python.sheng_wen.domain.storage.repository import (
    TempFileRepository,
    extract_bvid,
)
from src.main.python.sheng_wen.domain.storage.type import (
    AudioMissingReason,
    ReclaimResult,
    TaskFileGroup,
)

TERMINAL_STATUSES = {"COMPLETED", "PARTIAL", "FAILED"}
_COMPLETED_RETENTION_STATUSES = {"COMPLETED", "PARTIAL"}


def _parse_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


class StorageReclaimService:
    """
    存储回收服务：基于任务文件组的 FIFO 两档回收 + 孤儿清扫 + 存量音频状态推断。

    与 4 个 worker 完全解耦：删除前通过队列快照保护在跑任务；
    数据库与通知通过惰性依赖注入，便于单元测试。
    """

    def __init__(
        self,
        repository: TempFileRepository | None = None,
        base_dir: str | None = None,
        storage_config: StorageConfig | None = None,
        db: Any | None = None,
        snapshots_provider: Callable[[], Any] | None = None,
        notifier: Callable[[str, dict], Any] | None = None,
    ) -> None:
        self._repo = repository or TempFileRepository()
        self._base_dir = base_dir if base_dir is not None else config.storage.base_dir
        self._storage_config = storage_config
        self._db = db
        self._snapshots_provider = snapshots_provider
        self._notifier = notifier

    # ---- 惰性依赖注入（避免循环导入） ----

    @property
    def _cfg(self) -> StorageConfig:
        return self._storage_config or config.storage

    @property
    def _tasks_db(self) -> Any:
        if self._db is None:
            from src.main.python.sheng_wen.db import db

            self._db = db
        return self._db

    @property
    def _queue_provider(self) -> Callable[[], Any]:
        if self._snapshots_provider is None:
            from src.main.python.sheng_wen.api import get_queue_snapshots

            self._snapshots_provider = get_queue_snapshots
        return self._snapshots_provider

    @property
    def _notify(self) -> Callable[[str, dict], Any]:
        if self._notifier is None:
            from src.main.python.sheng_wen.task_updater import update_and_notify

            self._notifier = update_and_notify
        return self._notifier

    # ---- 主流程 ----

    async def run_once(self) -> ReclaimResult:
        """
        单轮回收：
        1) 孤儿清扫（_temp.* 残留、*.part/*.ytdl 超期）
        2) 超限时按任务 created_at FIFO 两档回收（媒体 → 中间产物）
        3) 删除媒体文件后标记 audio_downloaded/reclaimed（先删文件后标记，每任务一次）
        """
        base_dir = self._base_dir
        limit_bytes = int(self._cfg.max_total_mb * 1024 * 1024)
        files = await self._repo.scan(base_dir)
        usage = sum(f.size_bytes for f in files)
        file_index = {f.path for f in files}
        size_by_path = {f.path: f.size_bytes for f in files}
        result = ReclaimResult(
            over_limit=usage > limit_bytes,
            usage_bytes=usage,
            limit_bytes=limit_bytes,
        )

        tasks = self._tasks_db.list_tasks()
        terminal_task_ids = {
            str(t.get("id") or "")
            for t in tasks
            if str(t.get("status") or "").upper() in TERMINAL_STATUSES
        }

        # 1) 孤儿清扫
        orphans = await self._repo.list_orphans(
            base_dir,
            tasks,
            terminal_task_ids,
            int(self._cfg.retention_failed_sec),
        )
        for info in orphans:
            if await self._repo.delete_file(info.path):
                result.orphan_files += 1
                result.orphan_bytes += info.size_bytes
                usage -= info.size_bytes
        if result.orphan_files:
            logger.info(
                f"[StorageReclaim] 孤儿清扫: 删除 {result.orphan_files} 个文件, "
                f"释放 {result.orphan_bytes / 1024 / 1024:.1f}MB"
            )

        # 2) 超限判断
        if usage <= limit_bytes:
            return result

        # 3) 资格组（终态 + 超过保留期 + 不在 4 worker 在跑集合）
        protected = await self._collect_protected_task_ids()
        eligible = await self._build_eligible_groups(tasks, file_index, protected)
        result.eligible_tasks = len(eligible)
        if not eligible:
            logger.warning(
                f"[StorageReclaim] 存储超限 ({usage / 1024 / 1024:.0f}MB > "
                f"{limit_bytes / 1024 / 1024:.0f}MB) 但无符合资格的任务可回收"
            )
            return result

        # 共享 BV 引用计数：仅当全部引用任务都在本次回收集合才允许删除
        bv_refs: dict[str, set[str]] = {}
        for t in tasks:
            bvid = extract_bvid(str(t.get("video_url") or ""))
            if bvid:
                bv_refs.setdefault(bvid, set()).add(str(t.get("id") or ""))
        reclaim_task_ids = {g.task_id for g in eligible}
        can_delete_bv = {
            bvid: refs.issubset(reclaim_task_ids) for bvid, refs in bv_refs.items()
        }

        # 4) 第一档：媒体文件（组序 FIFO，组内 mtime 升序）
        # 共享 BV 文件被某个组删除时，引用它的全部组一并标记为 reclaimed
        path_to_group_ids: dict[str, set[str]] = {}
        for group in eligible:
            for path in group.media_files:
                path_to_group_ids.setdefault(path, set()).add(group.task_id)

        marked_task_ids: set[str] = set()
        for group in eligible:
            if usage <= limit_bytes:
                break
            for path in sorted(group.media_files, key=self._file_mtime):
                if usage <= limit_bytes:
                    break
                if not self._can_delete_file(path, can_delete_bv):
                    continue
                if await self._repo.delete_file(path):
                    usage -= size_by_path.get(path, 0)
                    result.reclaimed_files += 1
                    result.reclaimed_bytes += size_by_path.get(path, 0)
                    for affected_task_id in path_to_group_ids.get(path, set()):
                        if affected_task_id not in marked_task_ids:
                            await self._mark_reclaimed(affected_task_id)
                            marked_task_ids.add(affected_task_id)
                            result.marked_tasks += 1

        # 5) 第二档：中间产物（仅当第一档后仍超限）
        for group in eligible:
            if usage <= limit_bytes:
                break
            for path in sorted(group.intermediate_files, key=self._file_mtime):
                if usage <= limit_bytes:
                    break
                if await self._repo.delete_file(path):
                    usage -= size_by_path.get(path, 0)
                    result.reclaimed_files += 1
                    result.reclaimed_bytes += size_by_path.get(path, 0)

        if result.reclaimed_files:
            logger.info(
                f"[StorageReclaim] 回收完成: 删除 {result.reclaimed_files} 个文件, "
                f"释放 {result.reclaimed_bytes / 1024 / 1024:.1f}MB, "
                f"标记 {result.marked_tasks} 个任务"
            )
        return result

    # ---- 资格与分组 ----

    async def _collect_protected_task_ids(self) -> set[str]:
        """4 worker 快照的 active ∪ waiting 任务集合。"""
        protected: set[str] = set()
        try:
            raw = self._queue_provider()
            if inspect.isawaitable(raw):
                snapshots = await raw
            else:
                snapshots = raw
        except Exception as e:
            logger.warning(f"[StorageReclaim] 获取队列快照失败，跳过在跑保护: {e}")
            return protected
        for snap in snapshots or []:
            if not isinstance(snap, dict):
                continue
            active = snap.get("active_task_id")
            if active:
                protected.add(str(active))
            for waiting in snap.get("waiting_task_ids") or []:
                protected.add(str(waiting))
        return protected

    async def _build_eligible_groups(
        self,
        tasks: list[dict],
        file_index: set[str],
        protected: set[str],
    ) -> list[TaskFileGroup]:
        now = datetime.now(timezone.utc)
        groups: list[TaskFileGroup] = []
        for task in tasks:
            task_id = str(task.get("id") or "")
            if not task_id or task_id in protected:
                continue
            status = str(task.get("status") or "").upper()
            if status not in TERMINAL_STATUSES:
                continue
            retention_sec = (
                int(self._cfg.retention_completed_sec)
                if status in _COMPLETED_RETENTION_STATUSES
                else int(self._cfg.retention_failed_sec)
            )
            latest = _parse_utc(task.get("latest_modified_at"))
            if latest is None:
                continue
            if (now - latest).total_seconds() < retention_sec:
                continue

            video_url = str(task.get("video_url") or "")
            media_files = [
                p
                for p in await self._repo.resolve_task_media_files(
                    task_id, video_url, file_index, self._base_dir
                )
                if os.path.exists(p)
            ]
            intermediate_files = [
                p
                for p in await self._repo.resolve_task_intermediate_files(
                    task_id, video_url, file_index, self._base_dir
                )
                if os.path.exists(p)
            ]
            groups.append(
                TaskFileGroup(
                    task_id=task_id,
                    status=status,
                    created_at=_parse_utc(task.get("created_at")),
                    latest_modified_at=latest,
                    media_files=media_files,
                    intermediate_files=intermediate_files,
                )
            )
        # 队列语义：按任务 created_at 升序（FIFO，先创建先回收）
        groups.sort(
            key=lambda g: g.created_at or datetime.max.replace(tzinfo=timezone.utc)
        )
        return groups

    # ---- 删除与标记 ----

    @staticmethod
    def _file_mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    @staticmethod
    def _is_bv_file(path: str) -> bool:
        name = os.path.basename(path)
        return bool(extract_bvid(name))

    def _can_delete_file(self, path: str, can_delete_bv: dict[str, bool]) -> bool:
        if not os.path.exists(path):
            return False
        if not self._is_bv_file(path):
            return True
        # 共享 BV 文件：仅当全部引用任务都在本次回收集合才可删
        bvid = extract_bvid(os.path.basename(path))
        if bvid is None:
            return True
        return can_delete_bv.get(bvid, True)

    async def _mark_reclaimed(self, task_id: str) -> None:
        """删除媒体文件后标记任务（先删文件后标记）。"""
        try:
            await self._notify(
                task_id,
                {
                    "audio_downloaded": False,
                    "audio_missing_reason": AudioMissingReason.RECLAIMED.value,
                },
            )
        except Exception as e:
            logger.warning(f"[StorageReclaim] 标记任务 {task_id} 失败: {e}")

    # ---- 存量推断与回填 ----

    async def infer_audio_status(self, task: dict) -> tuple[bool | None, str | None]:
        """
        按媒体文件存在性推断存量任务的音频状态：
        有媒体 → (True, None)；有 subtitle.txt → (False, subtitle_only)；
        有 transcript → (False, reclaimed)；其余 → (None, None)。
        """
        task_id = str(task.get("id") or "")
        video_url = str(task.get("video_url") or "")
        if not task_id:
            return (None, None)
        files = await self._repo.scan(self._base_dir)
        file_index = {f.path for f in files}
        media = await self._repo.resolve_task_media_files(
            task_id, video_url, file_index, self._base_dir
        )
        if any(os.path.exists(path) for path in media):
            return (True, None)
        subtitle_path = os.path.join(self._base_dir, f"{task_id}_subtitle.txt")
        if os.path.exists(subtitle_path):
            return (False, AudioMissingReason.SUBTITLE_ONLY.value)
        if task.get("transcript"):
            return (False, AudioMissingReason.RECLAIMED.value)
        return (None, None)

    async def backfill_legacy(self) -> int:
        """
        存量回填：对 audio_downloaded IS NULL 的终态任务按存在性推断，
        用 db.update_task 直接写入，不广播。
        """
        updated = 0
        tasks = self._tasks_db.list_tasks()
        for task in tasks:
            if task.get("audio_downloaded") is not None:
                continue
            status = str(task.get("status") or "").upper()
            if status not in TERMINAL_STATUSES:
                continue
            inferred, reason = await self.infer_audio_status(task)
            if inferred is None:
                continue
            updates: dict[str, Any] = {"audio_downloaded": inferred}
            if reason is not None:
                updates["audio_missing_reason"] = reason
            self._tasks_db.update_task(str(task.get("id") or ""), updates)
            updated += 1
        if updated:
            logger.info(f"[StorageReclaim] 存量音频状态回填完成: {updated} 个任务")
        return updated
