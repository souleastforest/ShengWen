import asyncio
from typing import List

from loguru import logger

from .worker import Worker

class WorkerManager:
    """
    管理所有工作单元的生命周期。
    """
    def __init__(self, workers: List[Worker]):
        """
        初始化工作单元管理器。

        Args:
            workers: 要管理的工作单元实例列表。
        """
        self.s_workers = workers

    def start_all(self):
        """
        启动所有已注册的工作单元。
        """
        logger.info("正在启动所有工作单元...")
        for worker in self.s_workers:
            logger.info(f"正在启动工作单元: {worker.name}")
            worker.start()
            logger.info(f"工作单元启动请求已提交: {worker.name}")
        logger.info("所有工作单元已启动。")

    async def stop_all(self):
        """
        停止所有已注册的工作单元。
        """
        logger.info("正在停止所有工作单元...")
        # 使用 asyncio.gather 并发停止所有 worker
        stop_tasks = [worker.stop() for worker in self.s_workers]
        results = await asyncio.gather(*stop_tasks, return_exceptions=True)
        for worker, result in zip(self.s_workers, results):
            if isinstance(result, Exception):
                logger.error(f"停止工作单元失败: {worker.name}, error={result}", exc_info=True)
        logger.info("所有工作单元已完全停止。")
