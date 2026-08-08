from unittest.mock import AsyncMock, patch

import pytest

from src.main.python.sheng_wen.domain.model.repository import ModelLoader
from src.main.python.sheng_wen.domain.model.type import LoaderType, ModelSpec
from src.main.python.sheng_wen.infra.model.local_loader import LocalModelLoader


def test_local_loader_implements_protocol():
    loader = LocalModelLoader()
    assert isinstance(loader, ModelLoader)


@pytest.mark.asyncio
async def test_load_returns_loaded_state():
    loader = LocalModelLoader()
    spec = ModelSpec(model_id="test", loader_type=LoaderType.LOCAL, device="cpu")
    fake_loop = AsyncMock()
    fake_loop.run_in_executor = AsyncMock(return_value=object())

    with patch(
        "src.main.python.sheng_wen.infra.model.local_loader.asyncio.get_running_loop",
        return_value=fake_loop,
    ):
        state = await loader.load(spec)

    assert state.status == "loaded"
    assert state.model_id == "test"


@pytest.mark.asyncio
async def test_is_loaded_false_initially():
    loader = LocalModelLoader()
    assert await loader.is_loaded("any") is False


@pytest.mark.asyncio
async def test_unload():
    loader = LocalModelLoader()
    await loader.unload("any")
