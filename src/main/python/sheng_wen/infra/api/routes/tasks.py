from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.infra.api.routes import deps
from src.main.python.sheng_wen.infra.api.routes.schemas import (
    ReSummarizeRequest,
    ReTranscribeRequest,
    Task,
    TaskCreate,
    TaskUpdate,
)
from src.main.python.sheng_wen.infra.api.routes.websocket import notify_task_update
from src.main.python.sheng_wen.utils.media import build_transcriber_payload


router = APIRouter(prefix="/tasks")


async def _get_bilibili_video_title_and_parts(video_url: str) -> tuple[str, list]:
    from bilibili_api import sync, video

    candidate = video_url
    if "b23.tv" in video_url:
        try:
            request = UrlRequest(video_url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=15) as response:
                candidate = response.geturl()
        except Exception:
            pass

    match = re.search(r"/video/(BV[0-9A-Za-z]+)", candidate)
    if not match:
        fallback = re.search(r"(BV[0-9A-Za-z]+)", candidate)
        if fallback:
            bvid = fallback.group(1)
        else:
            return ("未知标题", [])
    else:
        bvid = match.group(1)

    video_obj = video.Video(bvid=bvid)
    info = sync(video_obj.get_info())

    title = str(info.get("title") or "未知标题")
    pages = info.get("pages", [])
    parts = []
    for page in pages:
        if isinstance(page, dict):
            parts.append(
                {
                    "index": int(page.get("page", 0)) - 1,
                    "title": str(page.get("part") or ""),
                }
            )
    return (title, parts)


@router.post("/", response_model=Task, status_code=201)
async def create_task(task_in: TaskCreate, request: Request):
    if task_in.bilibili_parts and task_in.bilibili_parts.mode == "separate":
        try:
            video_title, parts_info = await _get_bilibili_video_title_and_parts(
                str(task_in.video_url)
            )
        except Exception as e:
            logger.warning(f"获取 B 站视频标题失败: {e}")
            video_title = "未知标题"
            parts_info = []

        first_task_data = None
        for part_index in task_in.bilibili_parts.indices:
            task_id = str(uuid.uuid4())
            resolved_summary_mode = deps._normalize_summary_mode(task_in.summary_mode)

            part_title = ""
            for p in parts_info:
                if p["index"] == part_index:
                    part_title = p["title"]
                    break

            task_title = f"{video_title} - P{part_index + 1}"
            if part_title:
                task_title = f"{video_title} - P{part_index + 1}: {part_title}"

            task_data = {
                "id": task_id,
                "video_url": str(task_in.video_url),
                "status": TaskStatus.PENDING,
                "created_at": datetime.now(timezone.utc),
                "latest_modified_at": datetime.now(timezone.utc),
                "progress": 0.0,
                "title": task_title,
                "author_name": None,
                "author_url": None,
                "summary_mode": resolved_summary_mode,
                "summary_chunk_total": None,
                "summary_chunk_done": None,
                "summary_meta": None,
            }
            db.save_task(task_id, task_data)

            task_payload = {
                "task_id": task_id,
                "video_url": str(task_in.video_url),
                "quality": task_in.quality,
                "summary_mode": resolved_summary_mode,
                "bilibili_parts": {
                    "mode": "merge",
                    "indices": [part_index],
                },
            }
            task_cookie = deps._sanitize_cookie_value(task_in.bilibili_sessdata)
            if task_cookie:
                task_payload["bilibili_sessdata"] = task_cookie

            await request.app.state.event_bus.publish(TASK_CREATED, task_payload)
            await notify_task_update(task_id)

            if first_task_data is None:
                first_task_data = task_data

        return first_task_data

    task_id = str(uuid.uuid4())
    resolved_summary_mode = deps._normalize_summary_mode(task_in.summary_mode)
    task_data = {
        "id": task_id,
        "video_url": str(task_in.video_url),
        "status": TaskStatus.PENDING,
        "created_at": datetime.now(timezone.utc),
        "latest_modified_at": datetime.now(timezone.utc),
        "progress": 0.0,
        "author_name": None,
        "author_url": None,
        "summary_mode": resolved_summary_mode,
        "summary_chunk_total": None,
        "summary_chunk_done": None,
        "summary_meta": None,
    }
    db.save_task(task_id, task_data)

    task_payload = {
        "task_id": task_id,
        "video_url": str(task_in.video_url),
        "quality": task_in.quality,
        "summary_mode": resolved_summary_mode,
    }
    task_cookie = deps._sanitize_cookie_value(task_in.bilibili_sessdata)
    if task_cookie:
        task_payload["bilibili_sessdata"] = task_cookie

    if task_in.bilibili_parts:
        task_payload["bilibili_parts"] = {
            "mode": task_in.bilibili_parts.mode,
            "indices": task_in.bilibili_parts.indices,
        }

    await request.app.state.event_bus.publish(TASK_CREATED, task_payload)
    await notify_task_update(task_id)
    return task_data


@router.get("/", response_model=list[Task])
async def list_tasks():
    tasks = db.list_tasks()
    return sorted(tasks, key=lambda x: x["created_at"], reverse=True)


@router.get("/{task_id}", response_model=Task)
async def get_task(task_id: str):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    deps._trigger_author_resolution_if_needed(task)
    return task


@router.patch("/{task_id}", response_model=Task)
async def update_task(task_id: str, task_update: TaskUpdate):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    updates = task_update.dict(exclude_unset=True)
    if updates:
        from src.main.python.sheng_wen.task_updater import update_and_notify

        updated_task = await update_and_notify(task_id, updates)
        return updated_task

    return task


@router.post("/{task_id}/re-summarize", response_model=Task)
async def re_summarize_task(
    task_id: str, request: Request, payload: ReSummarizeRequest | None = None
):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.get("transcript"):
        raise HTTPException(
            status_code=400, detail="No transcript available for re-summarization"
        )

    requested_mode = payload.summary_mode if payload else None
    resolved_summary_mode = deps._normalize_summary_mode(
        requested_mode,
        fallback=str(task.get("summary_mode") or ""),
    )

    from src.main.python.sheng_wen.task_updater import update_and_notify

    worker_factory = deps.get_worker_factory(request, "get_llm_worker")
    worker = await deps._resolve_worker_or_raise(worker_factory, task_id=task_id)
    await update_and_notify(
        task_id,
        {
            "status": TaskStatus.SUMMARIZING,
            "summary": "",
            "progress": 0.0,
            "summary_mode": resolved_summary_mode,
            "summary_chunk_total": None,
            "summary_chunk_done": None,
            "summary_meta": None,
        },
    )

    temp_file = os.path.join("temp", f"{task_id}_re.txt")
    os.makedirs("temp", exist_ok=True)
    with open(temp_file, "w", encoding="utf-8") as f:
        f.write(task["transcript"])

    await worker.add_task(
        {
            "task_id": task_id,
            "intermediate_file_path": temp_file,
            "output_file": os.path.join("temp", f"{task_id}_re_summary.md"),
            "summary_mode": resolved_summary_mode,
        }
    )

    return db.get_task(task_id)


@router.post("/{task_id}/resolve-author", response_model=Task)
async def resolve_task_author(task_id: str):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    video_url = str(task.get("video_url") or "")
    if (
        not video_url
        or video_url.startswith("file://")
        or not deps._is_bilibili_video_url(video_url)
    ):
        return task

    if task.get("author_name") and task.get("author_url"):
        return task

    await deps._try_resolve_and_persist_author(task_id, video_url)
    return db.get_task(task_id)


@router.post("/resolve-author/backfill")
async def backfill_task_authors(limit: int = 50):
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit 必须大于 0")

    tasks = db.list_tasks()
    scanned = 0
    updated = 0
    skipped = 0

    for task in tasks:
        if scanned >= limit:
            break
        scanned += 1

        task_id = str(task.get("id") or "")
        video_url = str(task.get("video_url") or "")
        status = str(task.get("status") or "")
        has_author = bool(task.get("author_name")) and bool(task.get("author_url"))

        if (
            not task_id
            or not video_url
            or video_url.startswith("file://")
            or not deps._is_bilibili_video_url(video_url)
            or status != TaskStatus.COMPLETED
            or has_author
        ):
            skipped += 1
            continue

        if await deps._try_resolve_and_persist_author(task_id, video_url):
            updated += 1

    return {
        "scanned": scanned,
        "updated": updated,
        "skipped": skipped,
        "limit": limit,
    }


@router.post("/{task_id}/re-transcribe", response_model=Task)
async def re_transcribe_task(
    task_id: str, request: Request, payload: ReTranscribeRequest | None = None
):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    requested_mode = payload.summary_mode if payload else None
    resolved_summary_mode = deps._normalize_summary_mode(
        requested_mode,
        fallback=str(task.get("summary_mode") or ""),
    )

    local_media_file = deps._resolve_local_media_file(task_id, task)
    video_url = str(task.get("video_url") or "")
    can_redownload = bool(video_url) and not video_url.startswith("file://")
    if not local_media_file and not can_redownload:
        raise HTTPException(
            status_code=400,
            detail="找不到可用的本地媒体文件，且原任务不是可重下载的在线 URL。",
        )

    reset_data = {
        "progress": 0.0,
        "transcript": "",
        "summary": "",
        "error_message": None,
        "topic": None,
        "status": TaskStatus.PENDING,
        "summary_mode": resolved_summary_mode,
        "summary_chunk_total": None,
        "summary_chunk_done": None,
        "summary_meta": None,
    }
    from src.main.python.sheng_wen.task_updater import update_and_notify

    await update_and_notify(task_id, reset_data)

    if local_media_file:
        worker_factory = deps.get_worker_factory(request, "get_transcriber_worker")
        transcriber_w = await deps._resolve_worker_or_raise(
            worker_factory, task_id=task_id
        )
        await update_and_notify(task_id, {"status": TaskStatus.TRANSCRIBING})
        await transcriber_w.add_task(
            build_transcriber_payload(
                task_id=task_id,
                media_path=local_media_file,
                output_dir="temp",
                summary_mode=resolved_summary_mode,
            )
        )
        return db.get_task(task_id)

    worker_factory = deps.get_worker_factory(request, "get_downloader_worker")
    downloader_w = await deps._resolve_worker_or_raise(worker_factory, task_id=task_id)
    await update_and_notify(task_id, {"status": TaskStatus.DOWNLOADING})
    await downloader_w.add_task(
        {
            "task_id": task_id,
            "video_url": video_url,
            "quality": "audio_only",
            "summary_mode": resolved_summary_mode,
        }
    )
    return db.get_task(task_id)


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: str, request: Request):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    cancellation_reports: list[str] = []
    workers = [
        getattr(request.app.state, "downloader_worker", None),
        getattr(request.app.state, "file_upload_worker", None),
        getattr(request.app.state, "transcriber_worker", None),
        getattr(request.app.state, "llm_worker", None),
    ]
    for worker in workers:
        if worker is None:
            continue
        cancel_fn = getattr(worker, "cancel_task", None)
        if not callable(cancel_fn):
            continue
        try:
            result = cancel_fn(task_id)
            if isinstance(result, dict):
                cancellation_reports.append(
                    f"{worker.name}(removed={result.get('removed_from_queue', 0)},"
                    f" running={bool(result.get('cancelled_running', False))})"
                )
            else:
                cancellation_reports.append(f"{worker.name}(cancelled)")
        except Exception as e:
            logger.warning(f"[delete_task] 通知 {worker.name} 取消任务失败: {e}")

    if cancellation_reports:
        logger.info(
            f"[delete_task] 任务 {task_id} 取消结果: " + ", ".join(cancellation_reports)
        )

    db.delete_task(task_id)
    return None
