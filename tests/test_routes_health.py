import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.main.python.sheng_wen.infra.api.routes.health import router as health_router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(health_router)
    return app


@pytest.mark.asyncio
async def test_get_version_returns_version_payload(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/version")

    assert response.status_code == 200
    body = response.json()
    assert "version" in body
    assert isinstance(body["version"], str)
