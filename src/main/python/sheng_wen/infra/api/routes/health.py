from __future__ import annotations

from fastapi import APIRouter

from src.main.python.sheng_wen.infra.api.routes.schemas import VersionInfo
from src.main.python.sheng_wen.version import APP_VERSION


router = APIRouter()


@router.get("/version", response_model=VersionInfo)
async def get_version():
    return {"version": APP_VERSION}
