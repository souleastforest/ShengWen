from __future__ import annotations

import asyncio
import glob
import ipaddress
import os
from urllib.parse import unquote, urlparse

from fastapi import HTTPException, Request
from loguru import logger

from src.main.python.sheng_wen.config.settings import config
from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.downloader.bilibili_author_resolver import (
    BilibiliAuthorResolveError,
    resolve_bilibili_author,
)
from src.main.python.sheng_wen.downloader.bilibili_headers import (
    sanitize_cookie_value,
)
from src.main.python.sheng_wen.transcriber.transcriber import ModelLoadError
from src.main.python.sheng_wen.utils.media import SUPPORTED_MEDIA_EXTENSIONS


VALID_SUMMARY_MODES = {"auto", "standard", "agent", "none"}
_author_resolution_inflight_task_ids: set[str] = set()
_author_resolution_attempted_task_ids: set[str] = set()


def get_db():
    return db


def get_config():
    return config


def get_worker_factory(request: Request, worker_name: str):
    return getattr(request.app.state, worker_name, None)


def _build_model_load_error_detail(error: ModelLoadError) -> str:
    detail = str(error or "").strip()
    return detail or "转录模型加载失败，请检查模型配置、网络或代理设置后重试。"


def _is_loopback_client(request: Request) -> bool:
    client = request.client
    if client is None or not client.host:
        return False

    host = client.host.strip().lower()
    if host == "localhost":
        return True

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _resolve_file_url_path(file_url: str) -> str:
    if not file_url.startswith("file://"):
        return ""

    raw_path = file_url.replace("file://", "", 1)
    if raw_path:
        return raw_path

    parsed = urlparse(file_url)
    path = unquote(parsed.path or "")
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path


def _is_bilibili_video_url(video_url: str) -> bool:
    try:
        netloc = (urlparse(video_url).netloc or "").lower()
    except Exception:
        return False
    return "bilibili.com" in netloc or "b23.tv" in netloc


def _sanitize_cookie_value(value: str | None) -> str:
    return sanitize_cookie_value(value)


def _resolve_default_summary_mode() -> str:
    configured = str(config.summarization.mode or "auto").strip().lower()
    if configured in VALID_SUMMARY_MODES:
        return configured
    return "auto"


def _normalize_summary_mode(raw_value: str | None, fallback: str | None = None) -> str:
    candidate = str(raw_value or "").strip().lower()
    if candidate in VALID_SUMMARY_MODES:
        return candidate
    fb = str(fallback or "").strip().lower()
    if fb in VALID_SUMMARY_MODES:
        return fb
    return _resolve_default_summary_mode()


def _should_trigger_author_resolution(task: dict | None) -> bool:
    if not isinstance(task, dict):
        return False

    task_id = str(task.get("id") or "")
    video_url = str(task.get("video_url") or "")
    author_name = str(task.get("author_name") or "").strip()
    author_url = str(task.get("author_url") or "").strip()
    if not task_id or not video_url:
        return False
    if video_url.startswith("file://"):
        return False
    if not _is_bilibili_video_url(video_url):
        return False
    if author_name and author_url:
        return False
    if task_id in _author_resolution_attempted_task_ids:
        return False
    if task_id in _author_resolution_inflight_task_ids:
        return False
    return True


async def _resolve_author_once_in_background(task_id: str, video_url: str):
    _author_resolution_inflight_task_ids.add(task_id)
    _author_resolution_attempted_task_ids.add(task_id)
    try:
        await _try_resolve_and_persist_author(task_id, video_url)
    finally:
        _author_resolution_inflight_task_ids.discard(task_id)


async def _try_resolve_and_persist_author(task_id: str, video_url: str) -> bool:
    try:
        from src.main.python.sheng_wen.api import transcription_settings_manager
        from src.main.python.sheng_wen.task_updater import update_and_notify

        sessdata, _ = transcription_settings_manager.resolve_bilibili_sessdata()
        author_info = await resolve_bilibili_author(video_url, sessdata=sessdata)
        await update_and_notify(
            task_id,
            {
                "author_name": author_info.get("author_name"),
                "author_url": author_info.get("author_url"),
            },
        )
        return True
    except BilibiliAuthorResolveError as e:
        logger.info(
            f"[AuthorResolver] 任务 {task_id} 作者解析失败（仅提示，不影响流程）: {e}"
        )
        return False
    except Exception as e:
        logger.info(
            f"[AuthorResolver] 任务 {task_id} 作者回填失败（仅提示，不影响流程）: {e}"
        )
        return False


def _trigger_author_resolution_if_needed(task: dict | None):
    if not _should_trigger_author_resolution(task):
        return

    task_id = str(task.get("id") or "")
    video_url = str(task.get("video_url") or "")
    asyncio.create_task(_resolve_author_once_in_background(task_id, video_url))


def _resolve_local_media_file(task_id: str, task: dict) -> str | None:
    video_url = str(task.get("video_url") or "")
    if video_url.startswith("file://"):
        candidate = _resolve_file_url_path(video_url)
        if candidate and os.path.exists(candidate):
            return candidate

    for ext in SUPPORTED_MEDIA_EXTENSIONS:
        candidate = os.path.join(config.storage.resolved_base_dir, f"{task_id}{ext}")
        if os.path.exists(candidate):
            return candidate

    for path in glob.glob(
        os.path.join(config.storage.resolved_base_dir, f"{task_id}.*")
    ):
        ext = os.path.splitext(path)[1].lower()
        if ext in SUPPORTED_MEDIA_EXTENSIONS and os.path.exists(path):
            return path
    return None


async def _fail_task_with_model_error(
    task_id: str | None, error: ModelLoadError
) -> None:
    detail = _build_model_load_error_detail(error)
    logger.warning(f"[Transcriber] 模型加载失败: task_id={task_id}, detail={detail}")
    if not task_id:
        return
    from src.main.python.sheng_wen.task_updater import update_and_notify

    await update_and_notify(
        task_id,
        {
            "status": TaskStatus.FAILED,
            "error_message": detail,
        },
    )


async def _resolve_worker_or_raise(worker_factory, task_id: str | None):
    if worker_factory is None:
        raise HTTPException(status_code=500, detail="Worker factory not initialized")
    try:
        return await worker_factory()
    except ModelLoadError as e:
        await _fail_task_with_model_error(task_id, e)
        raise HTTPException(
            status_code=503, detail=_build_model_load_error_detail(e)
        ) from e
