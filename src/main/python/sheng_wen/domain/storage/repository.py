from __future__ import annotations

import os
import re
import time
from typing import Iterable

from loguru import logger

from src.main.python.sheng_wen.domain.storage.type import TempFileInfo
from src.main.python.sheng_wen.utils.media import SUPPORTED_MEDIA_EXTENSIONS

# B 站媒体文件扩展名（第一档：可回收媒体）
BILIBILI_MEDIA_EXTENSIONS = {".mp4", ".mp3", ".webm", ".mkv", ".part"}

# yt-dlp 临时下载文件扩展名（孤儿清扫）
YTDLP_STALE_EXTENSIONS = {".part", ".ytdl"}

_BV_PATTERN = re.compile(r"BV[0-9A-Za-z]+")
_UPLOAD_TEMP_PATTERN = re.compile(r"^(?P<task_id>.+)_temp\.[^.]+$")
_MULTIPART_MEDIA_PATTERN = re.compile(r"^(?P<task_id>.+)_p\d+\.[^.]+$")
_MULTIPART_SUMMARY_PATTERN = re.compile(r"^(?P<task_id>.+)_p\d+_summary\.(md|txt)$")


def extract_bvid(video_url: str) -> str | None:
    """从视频链接中离线提取 BV 号（不访问网络）。"""
    if not video_url:
        return None
    match = _BV_PATTERN.search(video_url)
    if not match:
        return None
    return match.group(0)


def _is_media_ext(ext: str) -> bool:
    return ext in SUPPORTED_MEDIA_EXTENSIONS


class TempFileRepository:
    """temp 目录的文件系统访问层（纯 I/O + 文件名模式匹配）。"""

    async def scan(self, base_dir: str) -> list[TempFileInfo]:
        """扫描 base_dir 下的普通文件，返回快照列表（mtime 升序）。"""
        files: list[TempFileInfo] = []
        if not base_dir or not os.path.isdir(base_dir):
            return files
        try:
            entries = os.scandir(base_dir)
        except OSError as e:
            logger.warning(f"[TempFileRepository] 扫描目录失败 {base_dir}: {e}")
            return files
        with entries:
            for entry in entries:
                try:
                    if not entry.is_file():
                        continue
                    stat = entry.stat()
                    files.append(
                        TempFileInfo(
                            path=entry.path,
                            size_bytes=int(stat.st_size),
                            mtime_ts=float(stat.st_mtime),
                        )
                    )
                except OSError:
                    continue
        files.sort(key=lambda f: f.mtime_ts)
        return files

    async def total_usage_bytes(self, base_dir: str) -> int:
        files = await self.scan(base_dir)
        return sum(f.size_bytes for f in files)

    async def delete_file(self, path: str) -> bool:
        """删除单个文件；不存在或删除失败返回 False。"""
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except OSError as e:
            logger.warning(f"[TempFileRepository] 删除文件失败 {path}: {e}")
        return False

    async def list_orphans(
        self,
        base_dir: str,
        tasks: list[dict],
        terminal_task_ids: set[str],
        retention_failed_sec: int,
        part_stale_sec: int = 3600,
    ) -> list[TempFileInfo]:
        """
        列出孤儿文件：
        - temp/{task_id}_temp.{ext}：无对应任务，或任务已终态，且 mtime 超 retention_failed_sec
          （任务存在但非终态时视为上传中，不回收）
        - temp/*.part / *.ytdl：mtime 超 part_stale_sec（默认 1h）
        - 无任何任务引用的媒体文件（孤儿 BV*.mp4/.mp3、任务已删除的 {task_id}.ext、
          BV 命名的分P文件）：mtime 超 retention_failed_sec 可回收
        """
        all_task_ids = {str(t.get("id") or "") for t in tasks}
        bv_usage: dict[str, int] = {}
        for t in tasks:
            bvid = extract_bvid(str(t.get("video_url") or ""))
            if bvid:
                bv_usage[bvid] = bv_usage.get(bvid, 0) + 1

        now_ts = time.time()
        orphans: list[TempFileInfo] = []
        for info in await self.scan(base_dir):
            name = os.path.basename(info.path)
            ext = os.path.splitext(name)[1].lower()
            age = now_ts - info.mtime_ts

            if ext in YTDLP_STALE_EXTENSIONS:
                if age >= part_stale_sec:
                    orphans.append(info)
                continue

            match = _UPLOAD_TEMP_PATTERN.match(name)
            if match:
                task_id = match.group("task_id")
                if task_id in all_task_ids and task_id not in terminal_task_ids:
                    # 任务存在但尚未终态（上传/转录进行中），保留
                    continue
                if age >= retention_failed_sec:
                    orphans.append(info)
                continue

            # 媒体文件孤儿：任务已删除的 {task_id}.ext / BV 文件无任何任务引用 / BV 命名的分P
            if not _is_media_ext(ext):
                continue
            if age < retention_failed_sec:
                continue

            part_match = _MULTIPART_MEDIA_PATTERN.match(name)
            if part_match:
                base_part = part_match.group("task_id")
                if base_part in all_task_ids:
                    continue  # 有对应任务，由任务组回收逻辑处理
                bvid = extract_bvid(base_part)
                if bvid and bv_usage.get(bvid, 0) > 0:
                    continue  # BV 仍被任务引用，由任务组逻辑处理
                orphans.append(info)
                continue

            bvid = extract_bvid(name)
            if bvid:
                if bv_usage.get(bvid, 0) > 0:
                    continue
                orphans.append(info)
                continue

            base_id = os.path.splitext(name)[0]
            if base_id in all_task_ids:
                continue
            if base_id.count("-") >= 4:  # UUID 格式任务 ID，任务已删除
                orphans.append(info)
        return orphans

    async def resolve_task_media_files(
        self,
        task_id: str,
        video_url: str,
        file_index: Iterable[str],
        base_dir: str,
    ) -> list[str]:
        """
        解析任务的第一档媒体文件：
        - B 站：temp/{BVid}.mp4/.mp3/.webm/.mkv/.part
        - 上传：temp/{task_id}{ext} + temp/{task_id}.mp3 + temp/{task_id}_temp{ext}
        - multipart：temp/{task_id}_p{N}.{媒体扩展名}
        """
        index = set(file_index)
        base = base_dir or "."
        candidates: list[str] = []
        bvid = extract_bvid(video_url)
        if bvid:
            for ext in BILIBILI_MEDIA_EXTENSIONS:
                candidates.append(os.path.join(base, f"{bvid}{ext}"))
        else:
            for ext in SUPPORTED_MEDIA_EXTENSIONS:
                candidates.append(os.path.join(base, f"{task_id}{ext}"))
            candidates.append(os.path.join(base, f"{task_id}.mp3"))
            for ext in SUPPORTED_MEDIA_EXTENSIONS:
                candidates.append(os.path.join(base, f"{task_id}_temp{ext}"))
        # multipart 分P媒体（B站与上传任务均适用，不依赖 video_url 分支）
        for path in index:
            name = os.path.basename(path)
            if not _MULTIPART_MEDIA_PATTERN.match(name):
                continue
            if _is_media_ext(os.path.splitext(name)[1].lower()):
                if name.startswith(f"{task_id}_p") or (
                    bvid and name.startswith(f"{bvid}_p")
                ):
                    candidates.append(path)
        return sorted({path for path in candidates if path in index})

    async def resolve_task_intermediate_files(
        self,
        task_id: str,
        video_url: str,
        file_index: Iterable[str],
        base_dir: str,
    ) -> list[str]:
        """
        解析任务的第二档中间产物：
        {task_id}_subtitle.txt、{task_id}_summary.md/.txt、{task_id}_re.*、
        {task_id}_multipart*.txt、{task_id}_overview*.txt、
        {task_id}_p{N}_summary.md/.txt、{BVid}_summary.md/.txt
        """
        index = set(file_index)
        base = base_dir or "."
        candidates: list[str] = []
        bvid = extract_bvid(video_url)

        fixed = [
            f"{task_id}_subtitle.txt",
            f"{task_id}_summary.md",
            f"{task_id}_summary.txt",
        ]
        if bvid:
            fixed.extend([f"{bvid}_summary.md", f"{bvid}_summary.txt"])
        for name in fixed:
            candidates.append(os.path.join(base, name))

        for path in index:
            name = os.path.basename(path)
            if name.startswith(f"{task_id}_re."):
                candidates.append(path)
            elif name.startswith(f"{task_id}_multipart") and name.endswith(".txt"):
                candidates.append(path)
            elif name.startswith(f"{task_id}_overview") and name.endswith(".txt"):
                candidates.append(path)
            elif _MULTIPART_SUMMARY_PATTERN.match(name):
                candidates.append(path)
        return sorted({path for path in candidates if path in index})
