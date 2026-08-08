from __future__ import annotations

import asyncio

from loguru import logger

from src.main.python.sheng_wen.config.settings import config
from src.main.python.sheng_wen.domain.storage.repository import TempFileRepository
from src.main.python.sheng_wen.domain.storage.service import StorageReclaimService

_MIN_INTERVAL_SEC = 60


class StorageReclaimerLoop:
    """
    独立的 asyncio 周期回收任务。

    start() 首轮立即执行一次 run_once，之后按 config.storage.cleanup_interval_sec
    循环（每轮重读间隔与限额）；stop() 取消循环。
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
        logger.info("[StorageReclaimer] 回收循环已启动（首轮立即执行）")

    async def _run(self) -> None:
        while not self._stopping:
            logger.debug(f"[StorageReclaimer] 回收循环心跳: interval={config.storage.cleanup_interval_sec}")
            try:
                service = StorageReclaimService(TempFileRepository())
                await service.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[StorageReclaimer] 回收执行失败: {e}", exc_info=True)
            # 每轮从配置重读间隔
            interval = max(_MIN_INTERVAL_SEC, int(config.storage.cleanup_interval_sec))
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

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
                logger.warning(f"[StorageReclaimer] 停止回收循环异常: {e}")
        logger.info("[StorageReclaimer] 回收循环已停止")


_reclaimer_loop: StorageReclaimerLoop | None = None


def get_storage_reclaimer_loop() -> StorageReclaimerLoop:
    global _reclaimer_loop
    if _reclaimer_loop is None:
        _reclaimer_loop = StorageReclaimerLoop()
    return _reclaimer_loop


async def start_storage_reclaimer() -> None:
    """lifespan 启动入口：先做存量音频状态回填，再启动周期循环。"""
    service = StorageReclaimService(TempFileRepository())
    try:
        backfilled = await service.backfill_legacy()
        if backfilled:
            logger.info(
                f"[StorageReclaimer] 存量回填完成: {backfilled} 个任务（仅写库不广播）"
            )
    except Exception as e:
        logger.warning(f"[StorageReclaimer] 存量回填失败（不影响回收启动）: {e}")
    get_storage_reclaimer_loop().start()


async def stop_storage_reclaimer() -> None:
    await get_storage_reclaimer_loop().stop()
