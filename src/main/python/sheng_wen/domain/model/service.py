from __future__ import annotations

from typing import Any

from loguru import logger

from src.main.python.sheng_wen.application.events.topics import (
    MODEL_LOAD_FAILED,
    MODEL_LOADED,
    MODEL_LOAD_STARTED,
    MODEL_UNLOADED,
)
from src.main.python.sheng_wen.domain.model.type import ModelSpec, ModelState


class ModelService:
    def __init__(self, loader: Any, event_bus: Any) -> None:
        self._loader = loader
        self._bus = event_bus
        self._states: dict[str, ModelState] = {}

    async def ensure_loaded(self, spec: ModelSpec) -> ModelState:
        if await self._loader.is_loaded(spec.model_id):
            logger.debug(f"[ModelService] Reusing loaded model {spec.model_id}")
            return self._states.get(
                spec.model_id,
                ModelState(
                    model_id=spec.model_id,
                    loader_type=spec.loader_type,
                    status="loaded",
                ),
            )

        await self._bus.publish(MODEL_LOAD_STARTED, {"model_id": spec.model_id})
        state = await self._loader.load(spec)
        self._states[spec.model_id] = state

        if state.status == "loaded":
            await self._bus.publish(MODEL_LOADED, state.to_dict())
        else:
            await self._bus.publish(MODEL_LOAD_FAILED, state.to_dict())

        return state

    async def unload(self, model_id: str) -> None:
        await self._loader.unload(model_id)
        self._states.pop(model_id, None)
        await self._bus.publish(MODEL_UNLOADED, {"model_id": model_id})
        logger.info(f"[ModelService] Unloaded model {model_id}")

