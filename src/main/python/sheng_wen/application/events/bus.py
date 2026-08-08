from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from loguru import logger


@runtime_checkable
class EventBus(Protocol):
    async def publish(self, topic: str, payload: Any) -> None: ...
    def subscribe(self, topic: str, handler: Callable[[Any], Awaitable[None]]) -> str: ...
    def unsubscribe(self, topic: str, subscription_id: str) -> None: ...


class AsyncioEventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, dict[str, Callable[[Any], Awaitable[None]]]] = defaultdict(dict)
        self._dead_letters: list[dict[str, Any]] = []

    async def publish(self, topic: str, payload: Any) -> None:
        handlers = self._handlers.get(topic, {})
        if not handlers:
            return
        tasks = [self._invoke_handler(topic, sub_id, handler, payload) for sub_id, handler in handlers.items()]
        await asyncio.gather(*tasks)

    def subscribe(self, topic: str, handler: Callable[[Any], Awaitable[None]]) -> str:
        sub_id = uuid.uuid4().hex[:12]
        self._handlers[topic][sub_id] = handler
        return sub_id

    def unsubscribe(self, topic: str, subscription_id: str) -> None:
        handlers = self._handlers.get(topic, {})
        handlers.pop(subscription_id, None)
        if not handlers and topic in self._handlers:
            del self._handlers[topic]

    def consume_dead_letters(self) -> list[dict[str, Any]]:
        letters = self._dead_letters.copy()
        self._dead_letters.clear()
        return letters

    async def _invoke_handler(
        self,
        topic: str,
        sub_id: str,
        handler: Callable[[Any], Awaitable[None]],
        payload: Any,
    ) -> None:
        try:
            await handler(payload)
        except Exception as exc:
            logger.warning(f"[EventBus] Handler failed: topic={topic}, sub={sub_id}, error={exc}")
            self._dead_letters.append(
                {
                    "topic": topic,
                    "subscription_id": sub_id,
                    "payload": payload,
                    "error": str(exc),
                }
            )
