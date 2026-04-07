from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.infra.api.routes import deps
from src.main.python.sheng_wen.infra.api.routes.schemas import LocalPathTaskCreate, Task
from src.main.python.sheng_wen.infra.api.routes.websocket import notify_task_update
from src.main.python.sheng_wen.utils.media import (
    SUPPORTED_MEDIA_EXTENSIONS,
    build_transcriber_payload,
)


router = APIRouter(prefix="")


@router.post("/upload", response_model=Task, status_code=201)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    summary_mode: Optional[str] = Form(default=None),
):
    file_ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""

    if file_ext not in SUPPORTED_MEDIA_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {file_ext}。支持的格式: {', '.join(sorted(SUPPORTED_MEDIA_EXTENSIONS))}",
        )

    task_id = str(uuid.uuid4())
    temp_dir = "temp"
    os.makedirs(temp_dir, exist_ok=True)
    temp_file_path = os.path.join(temp_dir, f"{task_id}_temp{file_ext}")

    try:
        with open(temp_file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)

        file_size = len(content)
        file_size_mb = file_size / 1024 / 1024
        max_size = 500 * 1024 * 1024
        if file_size > max_size:
            os.remove(temp_file_path)
            raise HTTPException(
                status_code=400,
                detail=f"文件过大 ({file_size_mb:.1f}MB)，最大支持 {max_size / 1024 / 1024:.0f}MB",
            )

        resolved_summary_mode = deps._normalize_summary_mode(summary_mode)
        task_data = {
            "id": task_id,
            "video_url": f"file://{temp_file_path}",
            "status": TaskStatus.UPLOADING,
            "created_at": datetime.now(timezone.utc),
            "latest_modified_at": datetime.now(timezone.utc),
            "progress": 0.0,
            "title": os.path.splitext(file.filename)[0]
            if file.filename
            else "Uploaded File",
            "author_name": None,
            "author_url": None,
            "summary_mode": resolved_summary_mode,
            "summary_chunk_total": None,
            "summary_chunk_done": None,
            "summary_meta": None,
        }
        db.save_task(task_id, task_data)

        worker_factory = deps.get_worker_factory(request, "get_file_upload_worker")
        worker = await deps._resolve_worker_or_raise(worker_factory, task_id=task_id)
        await worker.add_task(
            {
                "task_id": task_id,
                "file_path": temp_file_path,
                "filename": file.filename or "uploaded_file",
                "summary_mode": resolved_summary_mode,
            }
        )

        await notify_task_update(task_id)
        return task_data

    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        from loguru import logger

        logger.error(f"文件上传失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)}")


@router.get("/local-path/check")
async def check_local_path(file_path: str, request: Request):
    if not deps._is_loopback_client(request):
        raise HTTPException(status_code=403, detail="仅允许本机 localhost 请求。")

    raw_path = (file_path or "").strip().strip('"').strip("'")
    if not raw_path:
        return {"type": "not_found", "path": ""}

    local_path = os.path.abspath(os.path.expandvars(os.path.expanduser(raw_path)))

    if not os.path.exists(local_path):
        return {"type": "not_found", "path": local_path}
    if os.path.isfile(local_path):
        return {"type": "file", "path": local_path}
    if os.path.isdir(local_path):
        return {"type": "folder", "path": local_path}
    return {"type": "not_found", "path": local_path}


@router.get("/local-folder/scan")
async def scan_local_folder(folder_path: str, request: Request):
    if not deps._is_loopback_client(request):
        raise HTTPException(status_code=403, detail="仅允许本机 localhost 请求。")

    raw_path = (folder_path or "").strip().strip('"').strip("'")
    if not raw_path:
        raise HTTPException(status_code=400, detail="folder_path 不能为空")

    local_path = os.path.abspath(os.path.expandvars(os.path.expanduser(raw_path)))
    if not os.path.exists(local_path) or not os.path.isdir(local_path):
        raise HTTPException(status_code=400, detail=f"文件夹不存在: {local_path}")

    files = []
    try:
        for entry in os.scandir(local_path):
            if not entry.is_file():
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            if ext not in SUPPORTED_MEDIA_EXTENSIONS:
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            files.append({"name": entry.name, "path": entry.path, "size": size})
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"扫描文件夹失败: {e}")

    files.sort(key=lambda x: x["name"].lower())
    return {"folder_path": local_path, "files": files, "total": len(files)}


@router.post("/upload/local-path", response_model=Task, status_code=201)
async def upload_local_path(payload: LocalPathTaskCreate, request: Request):
    if not deps._is_loopback_client(request):
        raise HTTPException(
            status_code=403,
            detail="仅允许本机 localhost 请求使用本地路径直读。",
        )

    raw_path = (payload.file_path or "").strip().strip('"')
    if not raw_path:
        raise HTTPException(status_code=400, detail="file_path 不能为空")

    local_path = os.path.abspath(os.path.expandvars(os.path.expanduser(raw_path)))
    if not os.path.exists(local_path) or not os.path.isfile(local_path):
        raise HTTPException(
            status_code=400, detail=f"文件不存在或不可访问: {local_path}"
        )

    file_ext = os.path.splitext(local_path)[1].lower()
    if file_ext not in SUPPORTED_MEDIA_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {file_ext}。支持的格式: {', '.join(sorted(SUPPORTED_MEDIA_EXTENSIONS))}",
        )

    task_id = str(uuid.uuid4())
    title = os.path.splitext(os.path.basename(local_path))[0] or "Local File"
    resolved_summary_mode = deps._normalize_summary_mode(payload.summary_mode)
    os.makedirs("temp", exist_ok=True)
    task_data = {
        "id": task_id,
        "video_url": f"file://{local_path}",
        "status": TaskStatus.TRANSCRIBING,
        "created_at": datetime.now(timezone.utc),
        "latest_modified_at": datetime.now(timezone.utc),
        "progress": 0.0,
        "title": title,
        "author_name": None,
        "author_url": None,
        "summary_mode": resolved_summary_mode,
        "summary_chunk_total": None,
        "summary_chunk_done": None,
        "summary_meta": None,
    }
    db.save_task(task_id, task_data)

    payload_data = build_transcriber_payload(
        task_id=task_id,
        media_path=local_path,
        output_dir="temp",
        summary_mode=resolved_summary_mode,
    )

    worker_factory = deps.get_worker_factory(request, "get_transcriber_worker")
    worker = await deps._resolve_worker_or_raise(worker_factory, task_id=task_id)
    await worker.add_task(payload_data)
    await notify_task_update(task_id)
    return task_data
