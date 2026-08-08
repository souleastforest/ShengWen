"""Test: app lifespan creates event bus + pipeline and wires them correctly.

TDD RED phase — these tests should FAIL until the integration is wired.
"""
import pytest

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.pipeline import Pipeline


@pytest.mark.asyncio
async def test_app_state_has_event_bus():
    """App should expose an AsyncioEventBus singleton on app.state."""
    from src.main.python.sheng_wen.api import app

    bus = getattr(app.state, "event_bus", None)
    assert bus is not None
    assert isinstance(bus, AsyncioEventBus)


@pytest.mark.asyncio
async def test_app_state_has_pipeline():
    """App should expose a Pipeline singleton on app.state."""
    from src.main.python.sheng_wen.api import app

    pipeline = getattr(app.state, "pipeline", None)
    assert pipeline is not None
    assert isinstance(pipeline, Pipeline)


@pytest.mark.asyncio
async def test_pipeline_registered_worker_factories():
    """Pipeline should have all worker factories registered."""
    from src.main.python.sheng_wen.api import app

    pipeline = getattr(app.state, "pipeline", None)
    assert pipeline is not None
    assert "get_downloader_worker" in pipeline._worker_factories
    assert "get_file_upload_worker" in pipeline._worker_factories


@pytest.mark.asyncio
async def test_pipeline_and_bus_are_same_instance():
    """Pipeline's event bus should be the same instance as app.state.event_bus."""
    from src.main.python.sheng_wen.api import app

    bus = getattr(app.state, "event_bus", None)
    pipeline = getattr(app.state, "pipeline", None)
    assert bus is not None
    assert pipeline is not None
    assert pipeline._bus is bus
