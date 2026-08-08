# ruff: noqa: E402

import asyncio
import os
import sys
import unittest


path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, path)

from src.main.python.sheng_wen.lazy_worker_manager import LazyWorkerManager


class _StubLazyWorkerManager(LazyWorkerManager):
    def __init__(self):
        super().__init__()
        self.init_calls = 0
        self._stub_worker = object()

    async def _initialize_workers(self):
        async with self._init_lock:
            if self._initialized:
                return
            self.init_calls += 1
            await asyncio.sleep(0.01)
            self._downloader_worker = self._stub_worker
            self._initialized = True


class TestLazyWorkerManager(unittest.IsolatedAsyncioTestCase):
    async def test_stop_all_before_init_should_not_initialize(self):
        manager = LazyWorkerManager()
        await manager.stop_all()
        self.assertFalse(manager.is_initialized)
        self.assertEqual(manager.get_initialized_workers(), [])

    async def test_concurrent_get_downloader_worker_should_initialize_once(self):
        manager = _StubLazyWorkerManager()
        workers = await asyncio.gather(
            *[manager.get_downloader_worker() for _ in range(10)]
        )
        self.assertTrue(manager.is_initialized)
        self.assertEqual(manager.init_calls, 1)
        self.assertTrue(all(worker is workers[0] for worker in workers))


if __name__ == "__main__":
    unittest.main()
