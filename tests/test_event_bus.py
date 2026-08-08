from dataclasses import dataclass

import pytest

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus


@dataclass
class FakePayload:
    value: str = ""


@pytest.fixture
def bus():
    return AsyncioEventBus()


@pytest.mark.asyncio
async def test_publish_delivers_to_subscriber(bus):
    received = []

    async def handler(payload):
        received.append(payload)

    bus.subscribe("test.topic", handler)
    await bus.publish("test.topic", FakePayload(value="hello"))
    assert len(received) == 1
    assert received[0].value == "hello"


@pytest.mark.asyncio
async def test_unsubscribe(bus):
    received = []

    async def handler(payload):
        received.append(payload)

    sub_id = bus.subscribe("test.topic", handler)
    bus.unsubscribe("test.topic", sub_id)
    await bus.publish("test.topic", FakePayload(value="ignored"))
    assert len(received) == 0


@pytest.mark.asyncio
async def test_dead_letter_on_handler_error(bus):
    async def failing_handler(payload):
        raise RuntimeError("intentional failure")

    bus.subscribe("test.topic", failing_handler)
    await bus.publish("test.topic", FakePayload(value="boom"))
    dead = bus.consume_dead_letters()
    assert len(dead) == 1
    assert dead[0]["topic"] == "test.topic"


@pytest.mark.asyncio
async def test_no_subscribers_does_not_error(bus):
    await bus.publish("test.unused", FakePayload(value="nobody"))
    assert len(bus.consume_dead_letters()) == 0
