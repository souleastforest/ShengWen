from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from src.main.python.sheng_wen.application.events.topics import TASK_CREATED
from src.main.python.sheng_wen.config.settings import config
from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.infra.api.routes import deps
from src.main.python.sheng_wen.infra.api.routes.schemas import LocalPathTaskCreate, Task
from src.main.python.sheng_wen.infra.api.routes.websocket import notify_task_update
from src.main.python.sheng_wen.utils.media import (
    SUPPORTED_MEDIA_EXTENSIONS,
    unsupported_format_message,
)


router = APIRouter(prefix="")

# multipart 请求体的 boundary 等表单开销容忍（Content-Length 含表单开销，比文件本身略大；
# 实际开销仅几 KB~几 MB，8MB 已覆盖且避免超限请求先写满再拒绝）
_UPLOAD_PREFLIGHT_TOLERANCE_BYTES = 8 * 1024 * 1024
# 断连核对容差（I-3）：与预检容差解耦——表单开销实际仅几百 B~几 KB，
# 固定 1MB 即可容纳，同时能拦截 <8MB 的真实断连截断
_UPLOAD_CONTENT_LENGTH_MISMATCH_TOLERANCE_BYTES = 1 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024  # 流式写盘分块大小（1MB）


@router.get("/upload/config")
async def get_upload_config():
    """上传配置下发：前端大小预检与后端上限同源（避免客户端硬编码漂移）。"""
    return {"max_upload_mb": config.storage.max_upload_mb}


@router.post("/upload", response_model=Task, status_code=201)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    summary_mode: Optional[str] = Form(default=None),
    generate_topic: Optional[bool] = Form(
        default=True,
        description="仅转录（summary_mode=none）时，转录完成后是否对全文生成标题（默认开启）",
    ),
):
    file_ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""

    if file_ext not in SUPPORTED_MEDIA_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=unsupported_format_message(file_ext),
        )

    max_upload_bytes = int(config.storage.max_upload_mb * 1024 * 1024)
    # L4: 预检容差与上限联动——multipart 表单开销实际仅几 KB~几 MB，
    # 上限极小时容差收缩（min(8MB, max(1MB, 上限/10))），避免容差反噬预检
    tolerance_bytes = min(
        _UPLOAD_PREFLIGHT_TOLERANCE_BYTES,
        max(1024 * 1024, max_upload_bytes // 10),
    )

    # 预检：Content-Length 明确超限（含表单开销容忍）时直接拒绝，不开始写盘
    content_length = request.headers.get("content-length")
    if (
        content_length
        and content_length.isdigit()
        and int(content_length) > max_upload_bytes + tolerance_bytes
    ):
        raise HTTPException(
            status_code=413,
            detail=f"文件过大，最大支持 {max_upload_bytes / 1024 / 1024:.0f}MB",
        )

    task_id = str(uuid.uuid4())
    temp_dir = config.storage.resolved_base_dir
    os.makedirs(temp_dir, exist_ok=True)
    temp_file_path = os.path.join(temp_dir, f"{task_id}_temp{file_ext}")

    try:
        # 流式分块写盘，先判限再写：超限即清理并 413，文案统计实际落盘字节
        # （防大文件整体读入内存 OOM，防 Content-Length 缺失/谎报绕过预检）
        total_written = 0
        truncated = False
        with open(temp_file_path, "wb") as buffer:
            while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
                if total_written + len(chunk) > max_upload_bytes:
                    truncated = True
                    break
                total_written += len(chunk)
                buffer.write(chunk)
        if truncated:
            os.remove(temp_file_path)
            raise HTTPException(
                status_code=413,
                detail=f"文件过大 ({total_written / 1024 / 1024:.1f}MB)，最大支持 {max_upload_bytes / 1024 / 1024:.0f}MB",
            )

        # M3: 断连核对——优雅断连（代理/取消）时 read 正常结束但字节数不足，
        # 与 Content-Length 交叉核对（差 = 表单开销 + 文件缺口，独立小容差，
        # 与预检容差解耦以免 <8MB 的截断静默通过）
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) - total_written
            > _UPLOAD_CONTENT_LENGTH_MISMATCH_TOLERANCE_BYTES
        ):
            try:
                os.remove(temp_file_path)
            except OSError as e:
                from loguru import logger

                logger.warning(f"[upload] 中断清理失败: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"上传中断：实际接收 {total_written} 字节，与声明大小不符，请重新上传",
            )

        resolved_summary_mode = deps._normalize_summary_mode(summary_mode)
        source_name = os.path.basename(file.filename or "").strip() or None
        if source_name:
            source_name = source_name[:255]
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
            "generate_topic": bool(generate_topic),
            "source_name": source_name,
        }
        db.save_task(task_id, task_data)

        await request.app.state.event_bus.publish(
            TASK_CREATED,
            {
                "task_id": task_id,
                "video_url": f"file://{temp_file_path}",
                "file_path": temp_file_path,
                "filename": file.filename or "uploaded_file",
                "source_name": source_name,
                "summary_mode": resolved_summary_mode,
                "generate_topic": bool(generate_topic),
            },
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
            detail=unsupported_format_message(file_ext),
        )

    # M1: 与 multipart 通道同源的上限检查（loopback 直读也不豁免），
    # 避免超长音频/视频无上限进入转录管线
    file_size = os.path.getsize(local_path)
    max_upload_bytes = int(config.storage.max_upload_mb * 1024 * 1024)
    if file_size > max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大 ({file_size / 1024 / 1024:.1f}MB)，最大支持 {max_upload_bytes / 1024 / 1024:.0f}MB",
        )

    task_id = str(uuid.uuid4())
    title = os.path.splitext(os.path.basename(local_path))[0] or "Local File"
    resolved_summary_mode = deps._normalize_summary_mode(payload.summary_mode)
    os.makedirs(config.storage.resolved_base_dir, exist_ok=True)
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
        "generate_topic": payload.generate_topic,
        "source_name": os.path.basename(local_path).strip()[:255] or None,
    }
    db.save_task(task_id, task_data)

    await request.app.state.event_bus.publish(
        TASK_CREATED,
        {
            "task_id": task_id,
            "video_url": f"file://{local_path}",
            "file_path": local_path,
            "filename": os.path.basename(local_path) or "uploaded_file",
            "source_name": os.path.basename(local_path).strip()[:255] or None,
            "summary_mode": resolved_summary_mode,
            "generate_topic": payload.generate_topic,
        },
    )
    await notify_task_update(task_id)
    return task_data
