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
        except Exception as e:
            # 附带项 7：短链解析失败 → 分P信息缺失，任务将按单P语义处理，
            # 由下载器多P预检测兜底（DNS 恢复后拒绝多P并给指引）。
            logger.warning(
                "b23.tv 短链解析失败，分P信息缺失: {} (url={})", e, video_url
            )

    match = re.search(r"/video/(BV[0-9A-Za-z]+)", candidate)
    if not match:
        fallback = re.search(r"(BV[0-9A-Za-z]+)", candidate)
        if fallback:
            bvid = fallback.group(1)
        else:
            logger.warning("无法从链接中提取 BV 号，分P信息未知: {}", video_url)
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


async def _is_bilibili_multipart_url(video_url: str) -> bool:
    """确认 B 站 URL 是否为多分P视频（探针）。

    用途（P1-C）：无 task_parts 记录的 B 站任务（separate 拆分子任务，
    bilibili_parts 未持久化到任务行）执行 re-transcribe/re-download 前识别
    多分P URL 并显式拒绝，避免静默退化为单P或整P下载后失败。
    探针自身异常（网络/确定性错误）→ 按非多P放行，由下载器多P预检测兜底。
    """
    try:
        _, parts_info = await _get_bilibili_video_title_and_parts(video_url)
    except Exception as e:
        logger.warning("确认 B 站分P信息失败，按非多P放行: {} (url={})", e, video_url)
        return False
    return len(parts_info) > 1


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

    probe_degraded = False
    if not task_in.bilibili_parts and deps._is_bilibili_video_url(
        str(task_in.video_url)
    ):
        try:
            _, parts_info = await _get_bilibili_video_title_and_parts(
                str(task_in.video_url)
            )
        except Exception as e:
            if deps._is_bilibili_probe_network_error(e):
                # 网络类失败（DNS/连接/超时等瞬时故障）：探针本就不确定分P数，
                # 降级为整视频单P语义继续创建任务，DNS 恢复后由下载器重试；
                # parts_probe_failed 标记留给可观测性/下游防御（下载器多P预检测
                # 仍会按实际分P数拒绝多P）。
                logger.warning(
                    "获取 B 站分P信息失败（已降级为整视频）: {} (url={})",
                    e,
                    task_in.video_url,
                )
                probe_degraded = True
                parts_info = []
            else:
                logger.warning("获取 B 站分P信息失败: {}", e)
                raise HTTPException(
                    status_code=422,
                    detail="无法确认 B 站分P信息，请检查链接是否正确后重试。",
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
                "generate_topic": task_in.generate_topic,
            }
            db.save_task(task_id, task_data)

            task_payload = {
                "task_id": task_id,
                "video_url": str(task_in.video_url),
                "quality": task_in.quality,
                "summary_mode": resolved_summary_mode,
                "generate_topic": task_in.generate_topic,
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
        "generate_topic": task_in.generate_topic,
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
        "generate_topic": task_in.generate_topic,
    }
    task_cookie = deps._sanitize_cookie_value(task_in.bilibili_sessdata)
    if task_cookie:
        task_payload["bilibili_sessdata"] = task_cookie

    if probe_degraded:
        # 附带项 7：探针网络降级标记（观测性；下载器预检测为真正防御层）
        task_payload["parts_probe_failed"] = True

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
            # 重跑按原开关状态执行（老任务无该字段时默认 True）
            "generate_topic": bool(task.get("generate_topic", True)),
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
    # 广播/持久化的 summary_mode：resolved 为 'none'（对仅转录任务补总结）时改存
    # 'auto'，消除"none + SUMMARIZING"的瞬时误导——实际总结模式由
    # llm_worker._resolve_effective_mode 按 auto 判定兜底（standard/agent）。
    # 注意：派发给 worker 的 payload 仍沿用 resolved_summary_mode（'none' 时
    # LLMWorker 内部同样按 auto 兜底，见 llm_worker._resolve_requested_mode）。
    broadcast_summary_mode = (
        "auto" if resolved_summary_mode == "none" else resolved_summary_mode
    )
    await update_and_notify(
        task_id,
        {
            "status": TaskStatus.SUMMARIZING,
            "summary": "",
            "progress": 0.0,
            "summary_mode": broadcast_summary_mode,
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

    from src.main.python.sheng_wen.config.settings import config

    storage_dir = config.storage.resolved_base_dir
    temp_file = os.path.join(storage_dir, f"{task_id}_re.txt")
    os.makedirs(storage_dir, exist_ok=True)
    with open(temp_file, "w", encoding="utf-8") as f:
        f.write(task["transcript"])

    await worker.add_task(
        {
            "task_id": task_id,
            "intermediate_file_path": temp_file,
            "output_file": os.path.join(storage_dir, f"{task_id}_re_summary.md"),
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

    # “总结标题”开关恢复：payload 显式携带用显式值；缺省时沿用任务已存值
    # （重跑按原开关状态执行；老任务无该字段时默认 True），与 summary_mode
    # 的 fallback 模式一致。
    if payload is not None and payload.generate_topic is not None:
        resolved_generate_topic = payload.generate_topic
    else:
        resolved_generate_topic = bool(task.get("generate_topic", True))

    local_media_file = deps._resolve_local_media_file(task_id, task)
    video_url = str(task.get("video_url") or "")
    can_redownload = bool(video_url) and not video_url.startswith("file://")
    if not local_media_file and not can_redownload:
        raise HTTPException(
            status_code=400,
            detail="找不到可用的本地媒体文件，且原任务不是可重下载的在线 URL。",
        )

    # P1-C：separate 拆分子任务（无 task_parts 行、bilibili_parts 未持久化）
    # 的多分P URL 单独重转录会退化为单P或整P下载后失败——在 API 层显式拒绝
    # 并给可行动指引（严格版"创建时持久化 bilibili_parts 到任务行"需 DB
    # schema 变更，进 backlog）。单P URL 不受影响。
    replay_parts = get_task_parts(task_id)
    if not replay_parts and deps._is_bilibili_video_url(video_url):
        if await _is_bilibili_multipart_url(video_url):
            raise HTTPException(
                status_code=409,
                detail=(
                    "该任务是多分P视频的拆分子任务，未保存分P处理信息，无法单独"
                    "重新转录。请对父任务执行重新转录，或删除该任务后使用完整 BV"
                    "链接重新提交并在分P选择器中选择拆分或合并。"
                ),
            )

    reset_data = {
        "progress": 0.0,
        "transcript": "",
        "summary": "",
        "error_message": None,
        "topic": None,
        "status": TaskStatus.PENDING,
        "summary_mode": resolved_summary_mode,
        "generate_topic": resolved_generate_topic,
        "summary_chunk_total": None,
        "summary_chunk_done": None,
        "summary_meta": None,
        # ASR 分片字段重转录时清空：非分片路径不再写回，避免残留陈旧计数
        "asr_chunk_total": None,
        "asr_chunk_done": None,
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
                summary_mode=resolved_summary_mode,
                generate_topic=resolved_generate_topic,
            )
        )
        return db.get_task(task_id)

    worker_factory = deps.get_worker_factory(request, "get_downloader_worker")
    downloader_w = await deps._resolve_worker_or_raise(worker_factory, task_id=task_id)
    await update_and_notify(task_id, {"status": TaskStatus.DOWNLOADING})

    # 分P回放（附带项 6）：多P任务（task_parts 非空）重转录时恢复原
    # bilibili_parts + multipart_batch 派发，完整重跑分P流水线；分P先重置为
    # PENDING（否则 _process_bilibili_multipart 会跳过 COMPLETED 分P，
    # 重转录将退化为空跑）。无 parts 记录保持原单P payload（回归不误伤）。
    retranscribe_payload = {
        "task_id": task_id,
        "video_url": video_url,
        "quality": "audio_only",
        "summary_mode": resolved_summary_mode,
        "generate_topic": resolved_generate_topic,
    }
    if replay_parts:
        reset_failed_parts(
            task_id, [int(part.get("part_index", 0)) for part in replay_parts]
        )
        retranscribe_payload["bilibili_parts"] = {
            "mode": "merge",
            "indices": [int(part.get("part_index", 0)) for part in replay_parts],
        }
        retranscribe_payload["multipart_batch"] = True
    await downloader_w.add_task(retranscribe_payload)
    return db.get_task(task_id)


@router.post("/{task_id}/re-download", response_model=Task)
async def re_download_task(task_id: str, request: Request):
    """
    重新下载音频（媒体文件已被回收清理等场景）。

    校验矩阵：404 任务不存在 / 400 file:// 本地任务 / 409 无转录引导 re-transcribe /
    409 本地已有媒体文件；合法时置为 DOWNLOADING 并派发 re_download_only 下载任务。
    """
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    video_url = str(task.get("video_url") or "")
    if not video_url or video_url.startswith("file://"):
        raise HTTPException(status_code=400, detail="本地文件任务无需重新下载。")

    if not task.get("transcript"):
        raise HTTPException(
            status_code=409, detail="任务没有转录内容，请使用重新转录。"
        )

    if deps._resolve_local_media_file(task_id, task):
        raise HTTPException(
            status_code=409, detail="本地已有可用的媒体文件，请使用重新转录。"
        )

    # P1-C：同 re-transcribe——separate 拆分子任务（无 task_parts 行）的
    # 多分P URL 单独重下载会退化为单P或整P下载后失败，在 API 层显式拒绝。
    replay_parts = get_task_parts(task_id)
    if not replay_parts and deps._is_bilibili_video_url(video_url):
        if await _is_bilibili_multipart_url(video_url):
            raise HTTPException(
                status_code=409,
                detail=(
                    "该任务是多分P视频的拆分子任务，未保存分P处理信息，无法单独"
                    "重新下载。请对父任务执行重新下载，或删除该任务后使用完整 BV"
                    "链接重新提交并在分P选择器中选择拆分或合并。"
                ),
            )

    prev_status = str(task.get("status") or TaskStatus.PENDING.value)
    from src.main.python.sheng_wen.task_updater import update_and_notify

    await update_and_notify(
        task_id,
        {
            "status": TaskStatus.DOWNLOADING,
            "audio_downloaded": False,
            "audio_missing_reason": None,
            "error_message": None,
        },
    )

    worker_factory = deps.get_worker_factory(request, "get_downloader_worker")
    downloader_w = await deps._resolve_worker_or_raise(worker_factory, task_id=task_id)

    # 分P回放（附带项 6）：多P任务（task_parts 非空）重下载按分P逐个派发
    # （multipart_part + bilibili_parts 单分P + re_download_only），不退化
    # 只下载第一P；下载完成后 worker 将分P恢复原状态。
    # 注意：不派发 multipart_batch——父任务 _process_bilibili_multipart 会等待
    # 分P COMPLETED 后重跑合并/总结流水线，而 re-download 只恢复音频不重转录，
    # 父任务将被误判"所有选中的分P均处理失败"。
    if replay_parts:
        for part in replay_parts:
            part_index = int(part.get("part_index", 0))
            await downloader_w.add_task(
                {
                    "task_id": task_id,
                    "video_url": video_url,
                    "quality": "audio_only",
                    "summary_mode": task.get("summary_mode") or "auto",
                    "re_download_only": True,
                    "restore_status": prev_status,
                    "multipart_part": {
                        "index": part_index,
                        "title": part.get("title") or f"P{part_index + 1}",
                        "duration": part.get("duration") or 0,
                        "restore_status": str(
                            part.get("status") or "COMPLETED"
                        ).upper(),
                    },
                    "bilibili_parts": {"mode": "merge", "indices": [part_index]},
                }
            )
        return db.get_task(task_id)

    await downloader_w.add_task(
        {
            "task_id": task_id,
            "video_url": video_url,
            "quality": "audio_only",
            "summary_mode": task.get("summary_mode") or "auto",
            "re_download_only": True,
            "restore_status": prev_status,
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

    # H1: file:// 任务（上传/local-path）删除时同步清理其存储目录内私有文件。
    # 否则 2GB 级文件滞留最长 2h（retention_failed_sec），占用 10G cap
    # 并可能先于孤儿清扫触发其他 COMPLETED 任务媒体的误回收。
    # 说明：local-path 直读任务的文件经 FileUploadWorker 处理时会被 move 进
    # 存储目录（既有行为）——因此统一按 task_id 前缀清理存储目录内文件即可；
    # 文件尚未 move（如 UPLOADING 阶段删除）时存储目录内无该任务文件，无害。
    # 安全性：task_id 为 uuid4 且限定 storage 目录 + 前缀，不会触及其他任务文件；
    # 用户在存储目录外的原始文件不在清理范围。
    # 残余竞态：清理与 FileUploadWorker 的 move 并发时可能残留孤儿文件，
    # 由 reclaimer 兜底。
    try:
        from src.main.python.sheng_wen.config.settings import config

        storage_dir = config.storage.resolved_base_dir
        url = str(task.get("video_url") or "")
        if url.startswith("file://"):
            import glob

            for pattern in (f"{task_id}.*", f"{task_id}_*"):
                for path in glob.glob(os.path.join(storage_dir, pattern)):
                    try:
                        os.remove(path)
                        logger.info(f"[delete_task] 清理上传任务文件: {path}")
                    except OSError as e:
                        logger.warning(f"[delete_task] 清理文件失败: {path}: {e}")
    except Exception as e:
        logger.warning(f"[delete_task] 清理任务 {task_id} 存储文件失败: {e}")

    delete_task_parts(task_id)
    db.delete_task(task_id)
    return None
