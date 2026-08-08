"""Pipeline: wires event bus to worker lifecycle.

Subscribes to task lifecycle events and dispatches workers accordingly.
This replaces the direct worker.add_task() calls with event-driven dispatch.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from loguru import logger

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.events.topics import (
    TASK_CREATED,
    TASK_FAILED,
)


class Pipeline:
    """Orchestrates worker dispatch via the event bus."""

    def __init__(self, event_bus: AsyncioEventBus) -> None:
        self._bus = event_bus
        self._worker_factories: dict[str, Callable[[], Awaitable[Any]]] = {}
        self._subscription_ids: list[str] = []
        self._started = False

    def register_worker_factory(
        self, name: str, factory: Callable[[], Awaitable[Any]]
    ) -> None:
        """Register a worker factory (e.g., get_downloader_worker)."""
        self._worker_factories[name] = factory

    async def start(self) -> None:
        """Subscribe to task lifecycle events.

        幂等：主应用（ShengWen-app.py）与挂载子应用（api.py）各自的 lifespan
        都会调用本方法，重复订阅会导致同一 TASK_CREATED 事件被派发多次
        （任务重复入队、文件任务被二次处理误标 FAILED）。
        """
        if self._started:
            logger.debug("[Pipeline] Already started, skip")
            return
        sub_id = self._bus.subscribe(TASK_CREATED, self._on_task_created)
        self._subscription_ids.append(sub_id)
        self._started = True
        logger.info("[Pipeline] Subscribed to TASK_CREATED")

    async def stop(self) -> None:
        """Unsubscribe from all events."""
        if not self._started:
            logger.debug("[Pipeline] Not started, skip")
            return
        for sub_id in self._subscription_ids:
            self._bus.unsubscribe(TASK_CREATED, sub_id)
        self._subscription_ids.clear()
        self._started = False
        logger.info("[Pipeline] Stopped")

    async def _on_task_created(self, payload: Any) -> None:
        """Handle TASK_CREATED: dispatch to appropriate worker."""
        if not isinstance(payload, dict):
            return

        task_id = payload.get("task_id", "")

        if not task_id:
            logger.warning("[Pipeline] TASK_CREATED without task_id")
            return

        # Determine worker based on task type
        factory_name = self._resolve_worker(payload)
        factory = self._worker_factories.get(factory_name)
        if not factory:
            logger.warning(f"[Pipeline] No worker factory for: {factory_name}")
            return

        try:
            worker = await factory()
            await worker.add_task(payload)
            logger.info(f"[Pipeline] Dispatched task {task_id} to {factory_name}")
        except Exception as exc:
            logger.error(f"[Pipeline] Failed to dispatch task {task_id}: {exc}")
            await self._bus.publish(
                TASK_FAILED,
                {
                    "task_id": task_id,
                    "error": str(exc),
                },
            )

    def _resolve_worker(self, payload: dict) -> str:
        """Determine which worker factory to use based on task payload."""
        video_url = str(payload.get("video_url", ""))
        file_path = payload.get("file_path", "")
        if file_path or video_url.startswith("file://"):
            return "get_file_upload_worker"
        return "get_downloader_worker"
