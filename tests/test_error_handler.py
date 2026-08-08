import httpx
import pytest
from fastapi import FastAPI

from src.main.python.sheng_wen.infra.api.error_handler import register_error_handlers
from src.main.python.sheng_wen.shared.types.exceptions import (
    DatabaseError,
    ModelLoadError,
    StorageQuotaExceededError,
    TaskNotFoundError,
)


@pytest.fixture
def app():
    app = FastAPI()
    register_error_handlers(app)
    return app


@pytest.mark.asyncio
async def test_domain_error_returns_4xx(app):
    @app.get("/test-domain")
    async def raise_domain():
        raise TaskNotFoundError("task-123")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/test-domain")

    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "TASK_NOT_FOUND"


@pytest.mark.asyncio
async def test_model_load_error_returns_503(app):
    @app.get("/test-model")
    async def raise_model():
        raise ModelLoadError("model failed")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/test-model")

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_infra_error_returns_500(app):
    @app.get("/test-infra")
    async def raise_infra():
        raise DatabaseError("connection refused")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/test-infra")

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_storage_quota_returns_507(app):
    @app.get("/test-quota")
    async def raise_quota():
        raise StorageQuotaExceededError("disk full")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/test-quota")

    assert resp.status_code == 507
