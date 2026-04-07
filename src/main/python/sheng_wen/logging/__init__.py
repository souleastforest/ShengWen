from __future__ import annotations

import sys

from loguru import logger


def setup_logging(level: str = "INFO", json_format: bool = False) -> None:
    """Configure loguru as the unified logger for the application."""
    logger.remove()

    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <5}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stdout,
        format=fmt,
        level=level,
        serialize=json_format,
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
