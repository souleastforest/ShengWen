from __future__ import annotations

from typing import Protocol, runtime_checkable

from .type import ModelSpec, ModelState


@runtime_checkable
class ModelLoader(Protocol):
    async def load(self, spec: ModelSpec) -> ModelState: ...
    async def unload(self, model_id: str) -> None: ...
    async def is_loaded(self, model_id: str) -> bool: ...

