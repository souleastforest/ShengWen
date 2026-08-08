from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from src.main.python.sheng_wen.domain.model.type import ModelSpec, ModelState


class LocalModelLoader:
    def __init__(self) -> None:
        self._loaded_models: dict[str, Any] = {}

    def _load_sync(self, spec: ModelSpec) -> Any:
        from faster_whisper import WhisperModel

        return WhisperModel(
            spec.model_id,
            device=spec.device,
            compute_type=spec.compute_type,
        )

    async def load(self, spec: ModelSpec) -> ModelState:
        start = time.monotonic()
        try:
            loop = asyncio.get_running_loop()
            model = await loop.run_in_executor(None, self._load_sync, spec)
            elapsed = time.monotonic() - start
            self._loaded_models[spec.model_id] = model
            logger.info(
                f"[LocalModelLoader] Loaded {spec.model_id} in {elapsed:.1f}s"
            )
            return ModelState(
                model_id=spec.model_id,
                loader_type=spec.loader_type,
                status="loaded",
                load_time_sec=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error(f"[LocalModelLoader] Failed {spec.model_id}: {exc}")
            return ModelState(
                model_id=spec.model_id,
                loader_type=spec.loader_type,
                status="failed",
                load_time_sec=elapsed,
                error_message=str(exc),
            )

    async def unload(self, model_id: str) -> None:
        model = self._loaded_models.pop(model_id, None)
        if model is not None:
            del model
            logger.info(f"[LocalModelLoader] Unloaded {model_id}")

    async def is_loaded(self, model_id: str) -> bool:
        return model_id in self._loaded_models

