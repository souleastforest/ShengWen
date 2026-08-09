from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3

from loguru import logger

from src.main.python.sheng_wen.config.settings import config

_MIN_INTERVAL_SEC = 60


def _backup_sqlite(source_path: str, dest_path: str) -> None:
    """使用 SQLite 在线备份 API 生成一致性快照（服务运行中可安全调用）。"""
    src = sqlite3.connect(source_path)
    try:
        with sqlite3.connect(dest_path) as dst:
            src.backup(dst)
    finally:
        src.close()


class DatabaseBackupLoop:
    """
    独立的 asyncio 周期数据库备份任务。

    start() 首轮立即执行一次 run_once（先落一个备份），之后按
    config.database.backup_interval_sec 循环；stop() 取消循环。

    轮转策略：保留 backup_keep_count 份（默认 5），`.bak.1` 最新，
    `.bak.{keep}` 最老，新备份前轮转并删除超出保留数的旧备份。
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run())
        logger.info("[DatabaseBackup] 备份循环已启动（首轮立即执行）")

    async def _run(self) -> None:
        while not self._stopping:
            logger.debug(
                f"[DatabaseBackup] 备份循环心跳: interval={config.database.backup_interval_sec}"
            )
            try:
                await asyncio.to_thread(self.run_once)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[DatabaseBackup] 备份执行失败: {e}", exc_info=True)
            interval = max(_MIN_INTERVAL_SEC, int(config.database.backup_interval_sec))
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

    def run_once(self) -> int | None:
        """执行一次备份 + 轮转，返回备份文件路径（失败返回 None，不抛出）。"""
        source = config.database.sqlite_path
        keep = max(1, int(config.database.backup_keep_count))
        if not os.path.exists(source):
            logger.warning(f"[DatabaseBackup] 数据库不存在，跳过备份: {source}")
            return None
        try:
            # 删除超出保留数的最老备份
            oldest = f"{source}.bak.{keep}"
            if os.path.exists(oldest):
                os.remove(oldest)
            # 轮转：.bak.{keep-1} → .bak.{keep}，……，.bak.1 → .bak.2
            for i in range(keep - 1, 0, -1):
                src_bak = f"{source}.bak.{i}"
                if os.path.exists(src_bak):
                    shutil.move(src_bak, f"{source}.bak.{i + 1}")
            # 新备份落到 .bak.1
            dest = f"{source}.bak.1"
            _backup_sqlite(source, dest)
            logger.info(f"[DatabaseBackup] 备份完成: {dest}")
            return dest
        except Exception as e:
            logger.error(f"[DatabaseBackup] 备份失败: {e}", exc_info=True)
            return None

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping = True
        task, self._task = self._task, None
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"[DatabaseBackup] 停止备份循环异常: {e}")
        logger.info("[DatabaseBackup] 备份循环已停止")


_backup_loop: DatabaseBackupLoop | None = None


def get_database_backup_loop() -> DatabaseBackupLoop:
    global _backup_loop
    if _backup_loop is None:
        _backup_loop = DatabaseBackupLoop()
    return _backup_loop


async def start_database_backup() -> None:
    """lifespan 启动入口：启动周期备份循环（首轮立即备份一次）。"""
    get_database_backup_loop().start()


async def stop_database_backup() -> None:
    await get_database_backup_loop().stop()
