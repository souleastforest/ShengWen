from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

try:
    from loguru import logger
except ImportError:  # pragma: no cover - fallback when loguru is unavailable
    logger = logging.getLogger(__name__)


_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")


def _get_ffmpeg_path() -> str | None:
    from ..utils.ffmpeg_helper import FFmpegHelper

    return FFmpegHelper.get_ffmpeg_path()


def get_audio_duration(path: str) -> float:
    ffmpeg_path = _get_ffmpeg_path()
    if not ffmpeg_path or not path:
        return 0.0

    try:
        result = subprocess.run(
            [ffmpeg_path, "-i", path],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        logger.warning(f"Failed to inspect audio duration for {path}: {exc}")
        return 0.0

    match = _DURATION_PATTERN.search(result.stderr or "")
    if not match:
        return 0.0

    try:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
    except (TypeError, ValueError):
        return 0.0

    return hours * 3600 + minutes * 60 + seconds


def split_audio_into_chunks(
    path: str, chunk_duration: float, output_dir: str | None = None
) -> list[tuple[str, float]]:
    if chunk_duration <= 0:
        logger.warning("chunk_duration must be positive; returning original audio path")
        return [(path, 0.0)]

    duration = get_audio_duration(path)
    if duration <= 0.0 or duration <= chunk_duration:
        return [(path, 0.0)]

    ffmpeg_path = _get_ffmpeg_path()
    if not ffmpeg_path:
        logger.warning("ffmpeg is unavailable; returning original audio path")
        return [(path, 0.0)]

    target_dir = output_dir or tempfile.mkdtemp(prefix="shengwen_chunks_")
    os.makedirs(target_dir, exist_ok=True)

    source_path = Path(path)
    basename = source_path.stem
    chunks: list[tuple[str, float]] = []
    offset = 0.0
    index = 0
    expected_chunks = max(1, int((duration + chunk_duration - 1e-6) // chunk_duration))

    while offset < duration:
        remaining = duration - offset
        current_duration = min(chunk_duration, remaining)
        chunk_path = os.path.join(target_dir, f"{basename}_chunk_{index:04d}.wav")
        command = [
            ffmpeg_path,
            "-y",
            "-i",
            path,
            "-ss",
            str(offset),
            "-t",
            str(current_duration),
            "-acodec",
            "pcm_s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            chunk_path,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            logger.warning(f"Failed to create audio chunk at offset {offset}: {exc}")
            cleanup_chunks(chunks)
            raise RuntimeError(
                f"无法创建音频分片（offset={offset:.1f}s）: {exc}"
            ) from exc

        if result.returncode != 0 or not os.path.exists(chunk_path):
            logger.warning(
                f"ffmpeg failed to create chunk for {path} at offset {offset}: "
                f"{(result.stderr or '').strip()}"
            )
            cleanup_chunks(chunks)
            raise RuntimeError(
                f"无法创建音频分片（offset={offset:.1f}s）: "
                f"{(result.stderr or '').strip()}"
            )

        chunks.append((chunk_path, offset))
        offset += chunk_duration
        index += 1

    if len(chunks) != expected_chunks:
        cleanup_chunks(chunks)
        raise RuntimeError(
            f"音频分片不完整：期望 {expected_chunks} 片，实际生成 {len(chunks)} 片"
        )

    return chunks


def cleanup_chunks(chunks: list[tuple[str, float]]) -> None:
    directories: set[str] = set()
    for chunk_path, _ in chunks:
        if not chunk_path:
            continue
        parent = os.path.dirname(os.path.abspath(chunk_path))
        if not os.path.basename(parent).startswith("shengwen_chunks_"):
            continue
        directories.add(parent)
        try:
            os.remove(chunk_path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning(f"Failed to remove chunk file {chunk_path}: {exc}")

    for directory in directories:
        try:
            if os.path.isdir(directory) and not os.listdir(directory):
                os.rmdir(directory)
        except OSError as exc:
            logger.warning(f"Failed to remove chunk directory {directory}: {exc}")
