from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from src.main.python.sheng_wen.shared.types.exceptions import (
    DomainError,
    InfrastructureError,
    ShengWenError,
)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        logger.info(f"[DomainError] {exc.business_code}: {exc}")
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.business_code, "detail": str(exc)},
        )

    @app.exception_handler(InfrastructureError)
    async def handle_infra_error(
        request: Request, exc: InfrastructureError
    ) -> JSONResponse:
        logger.error(f"[InfrastructureError] {exc.business_code}: {exc}")
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.business_code, "detail": "Internal server error"},
        )

    @app.exception_handler(ShengWenError)
    async def handle_base_error(request: Request, exc: ShengWenError) -> JSONResponse:
        logger.error(f"[ShengWenError] {exc.business_code}: {exc}")
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.business_code, "detail": "Internal server error"},
        )
