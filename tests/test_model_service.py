from unittest.mock import AsyncMock

import pytest

from src.main.python.sheng_wen.domain.model.service import ModelService
from src.main.python.sheng_wen.domain.model.type import LoaderType, ModelSpec, ModelState


@pytest.fixture
def mock_loader():
    loader = AsyncMock()
    loader.is_loaded = AsyncMock(return_value=False)
    loader.load = AsyncMock(
        return_value=ModelState(
            model_id="test",
            loader_type=LoaderType.LOCAL,
            status="loaded",
        )
    )
    loader.unload = AsyncMock()
    return loader


@pytest.fixture
def mock_bus():
    return AsyncMock()


@pytest.mark.asyncio
async def test_ensure_loaded(mock_loader, mock_bus):
    svc = ModelService(loader=mock_loader, event_bus=mock_bus)
    spec = ModelSpec(model_id="test", loader_type=LoaderType.LOCAL)

    state = await svc.ensure_loaded(spec)

    assert state.status == "loaded"
    mock_loader.load.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_loaded_skips_when_cached(mock_loader, mock_bus):
    mock_loader.is_loaded = AsyncMock(return_value=True)
    svc = ModelService(loader=mock_loader, event_bus=mock_bus)
    spec = ModelSpec(model_id="test", loader_type=LoaderType.LOCAL)

    state = await svc.ensure_loaded(spec)

    assert state.status == "loaded"
    mock_loader.load.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_loaded_publishes_events(mock_loader, mock_bus):
    svc = ModelService(loader=mock_loader, event_bus=mock_bus)
    spec = ModelSpec(model_id="test", loader_type=LoaderType.LOCAL)

    await svc.ensure_loaded(spec)

    assert mock_bus.publish.call_count >= 1

