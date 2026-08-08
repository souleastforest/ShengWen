from __future__ import annotations

import os
import re
import time
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
from src.main.python.sheng_wen.task_parts import (
    delete_task_parts,
    get_task_part,
    get_task_parts,
    get_task_part_stats,
    init_task_parts,
    reset_failed_parts,
)


router = APIRouter(prefix="/tasks")


# ---- 幂等去重（内存 TTL）：5 秒内同一 video_url 重复提交直接返回已创建任务 ----
_TASK_SUBMIT_DEDUP_TTL_SECONDS = 5.0
_task_submit_dedup: dict[str, tuple[str, float]] = {}


def _dedup_now() -> float:
    """去重时钟（独立函数便于测试打桩）。"""
    return time.time()


def _check_task_submit_dedup(video_url: str) -> dict | None:
    """5 秒内同一 video_url 已创建且任务仍存在时，返回该任务；否则 None。"""
    if not video_url:
        return None
    entry = _task_submit_dedup.get(video_url)
    if entry is None:
        return None
    task_id, created_ts = entry
    if _dedup_now() - created_ts >= _TASK_SUBMIT_DEDUP_TTL_SECONDS:
        _task_submit_dedup.pop(video_url, None)
        return None
    task = db.get_task(task_id)
    if task is None:
        # 任务已被删除，窗口提前失效
        _task_submit_dedup.pop(video_url, None)
        return None
    return task


def _record_task_submit(video_url: str, task_id: str) -> None:
    """记录最近一次任务提交，用于 5 秒内去重。"""
    if not video_url:
        return
    _task_submit_dedup[video_url] = (task_id, _dedup_now())
    # 顺带清理过期条目，避免字典无限增长
    if len(_task_submit_dedup) > 1024:
        expired = [
            url
            for url, (_, ts) in _task_submit_dedup.items()
            if _dedup_now() - ts >= _TASK_SUBMIT_DEDUP_TTL_SECONDS
        ]
        for url in expired:
            _task_submit_dedup.pop(url, None)


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
                    "cid": int(page.get("cid", 0)),
                    "title": str(page.get("part") or ""),
                    "duration": int(page.get("duration") or 0),
                }
            )
    return (title, parts)


def _with_part_stats(task_data: dict):
    stats = get_task_part_stats(str(task_data.get("id") or ""))
    if stats.get("has_parts"):
        task_data = dict(task_data)
        task_data.update(stats)
    return task_data


@router.post("/", response_model=Task, status_code=201)
async def create_task(task_in: TaskCreate, request: Request):
    is_separate_parts = bool(
        task_in.bilibili_parts and task_in.bilibili_parts.mode == "separate"
    )
    # 幂等去重：5 秒内同一 URL 重复提交直接返回已创建任务（多分P separate 模式跳过）。
    if not is_separate_parts:
        existing_task = _check_task_submit_dedup(str(task_in.video_url))
        if existing_task is not None:
            logger.info(
                f"检测到 5 秒内重复提交同一 URL，返回已创建任务: "
                f"{existing_task.get('id')} ({str(task_in.video_url)})"
            )
            return _with_part_stats(existing_task)

    if not task_in.bilibili_parts and deps._is_bilibili_video_url(
        str(task_in.video_url)
    ):
        try:
            _, parts_info = await _get_bilibili_video_title_and_parts(
                str(task_in.video_url)
            )
        except Exception as e:
            logger.warning(f"获取 B 站分P信息失败: {e}")
            raise HTTPException(
                status_code=422,
                detail="无法确认 B 站分P信息，请先在分P选择器中选择要处理的内容。",
            ) from e
        if len(parts_info) > 1:
            raise HTTPException(
                status_code=422,
                detail="检测到多分P视频，请先选择要处理的分P和处理方式。",
            )

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

    merge_parts_info = []
    if task_in.bilibili_parts and task_in.bilibili_parts.mode == "merge":
        try:
            _, merge_parts_info = await _get_bilibili_video_title_and_parts(
                str(task_in.video_url)
            )
        except Exception as e:
            logger.warning(f"获取合并模式分P信息失败: {e}")

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
    _record_task_submit(str(task_in.video_url), task_id)

    if task_in.bilibili_parts and task_in.bilibili_parts.mode == "merge":
        part_map = {int(part.get("index", -1)): part for part in merge_parts_info}
        init_task_parts(
            task_id,
            [
                {
                    "index": index,
                    "cid": part_map.get(index, {}).get("cid"),
                    "title": part_map.get(index, {}).get("title") or f"P{index + 1}",
                    "duration": part_map.get(index, {}).get("duration", 0),
                }
                for index in task_in.bilibili_parts.indices
            ],
        )

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
        if task_in.bilibili_parts.mode == "merge":
            task_payload["multipart_batch"] = True

    await request.app.state.event_bus.publish(TASK_CREATED, task_payload)
    await notify_task_update(task_id)
    return _with_part_stats(task_data)


@router.get("/", response_model=list[Task])
async def list_tasks():
    tasks = db.list_tasks()
    lightweight_tasks = []
    for task in sorted(tasks, key=lambda x: x["created_at"], reverse=True):
        item = dict(_with_part_stats(task))
        # 侧栏只需要状态和摘要元数据；正文在选中任务后由 GET /tasks/{id} 按需加载。
        item.pop("transcript", None)
        item.pop("summary", None)
        item.pop("summary_meta", None)
        lightweight_tasks.append(item)
    return lightweight_tasks


@router.get("/queue")
async def get_task_queues():
    """返回 4 个固定 worker 的队列快照（排队可视化；纯新增，兼容旧客户端）。

    注意：必须注册在 /{task_id} 之前，否则 /queue 会被当作 task_id 匹配。
    """
    from src.main.python.sheng_wen.api import get_queue_snapshots

    queues = await get_queue_snapshots()
    return {
        "queues": queues,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _summary_overview(summary: str) -> str:
    marker = re.search(r"^#\s*分P总结.*$", summary, re.MULTILINE)
    if marker and marker.start() > 0:
        return summary[: marker.start()].strip()
    return summary[:12000].strip()


@router.get("/{task_id}", response_model=Task)
async def get_task(task_id: str, include_content: bool = True):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    deps._trigger_author_resolution_if_needed(task)
    result = dict(_with_part_stats(task))
    if not include_content:
        if result.get("summary"):
            result["summary"] = _summary_overview(str(result["summary"]))
        result.pop("transcript", None)
        result.pop("summary_meta", None)
    return result


@router.get("/{task_id}/parts")
async def get_task_parts_route(task_id: str):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # 初次加载只返回状态和元数据；完整转录/总结在展开单个分P时按需获取。
    return get_task_parts(task_id, include_text=False)


@router.get("/{task_id}/parts/{part_index}")
async def get_task_part_route(task_id: str, part_index: int):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    part = get_task_part(task_id, part_index)
    if not part:
        raise HTTPException(status_code=404, detail="Task part not found")
    return part


@router.post("/{task_id}/retry-failed-parts", response_model=Task)
async def retry_failed_parts(
    task_id: str, request: Request, payload: dict | None = None
):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    parts = get_task_parts(task_id)
    failed_indices = {
        part["part_index"] for part in parts if part["status"] == "FAILED"
    }
    requested_indices = (payload or {}).get("part_indices") or (payload or {}).get(
        "indices"
    )
    if requested_indices is None:
        indices = sorted(failed_indices)
    else:
        indices = sorted(
            {int(index) for index in requested_indices if int(index) in failed_indices}
        )
    if not parts or not indices:
        raise HTTPException(status_code=409, detail="当前没有失败的分P")
    reset_failed_parts(task_id, indices)
    from src.main.python.sheng_wen.task_updater import update_and_notify

    await update_and_notify(
        task_id, {"status": TaskStatus.PENDING, "progress": 0.0, "error_message": None}
    )
    worker_factory = deps.get_worker_factory(request, "get_downloader_worker")
    worker = await deps._resolve_worker_or_raise(worker_factory, task_id=task_id)
    await worker.add_task(
        {
            "task_id": task_id,
            "video_url": str(task.get("video_url") or ""),
            "quality": "audio_only",
            "summary_mode": task.get("summary_mode") or "auto",
            "bilibili_parts": {"mode": "merge", "indices": indices},
            "multipart_batch": True,
        }
    )
    return _with_part_stats(db.get_task(task_id))


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
            status_code=400, detail="任务没有可供重新总结的转录原文，请先重新转录。"
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

    multipart_parts = get_task_parts(task_id)
    if multipart_parts:
        await worker.add_task(
            {
                "task_id": task_id,
                "summary_mode": resolved_summary_mode,
                "multipart_resummarize": True,
            }
        )
        return _with_part_stats(db.get_task(task_id))

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

    delete_task_parts(task_id)
    db.delete_task(task_id)
    return None
