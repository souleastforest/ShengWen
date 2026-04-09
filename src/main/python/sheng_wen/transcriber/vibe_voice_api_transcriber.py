"""VibeVoice ASR transcriber using remote vLLM API inference."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from typing import Any

import httpx
from loguru import logger

from .transcriber import (
    Transcriber,
    TranscriptionCancelled,
    TranscriptionError,
    TranscriptionResult,
)


_MIME_MAP: dict[str, str] = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
}

_VIBEVOICE_SYSTEM_PROMPT = (
    "You are a helpful assistant that transcribes audio input into text output in JSON format."
)

_VIBEVOICE_SHOW_KEYS = ["Start time", "End time", "Speaker ID", "Content"]


def _guess_mime_type(path: str) -> str:
    """Guess MIME type from file extension."""
    ext = os.path.splitext(path)[1].lower()
    return _MIME_MAP.get(ext, "application/octet-stream")


def _get_audio_duration_ffprobe(path: str) -> float:
    """Get audio/video duration using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def _parse_timestamp(timestamp_str: str) -> float:
    """Parse timestamp string (HH:MM:SS.mmm or MM:SS.mmm) to seconds."""
    if not timestamp_str:
        return 0.0
    try:
        parts = timestamp_str.strip().split(":")
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return 0.0
    except (ValueError, TypeError):
        return 0.0


def _parse_transcription_text(raw_text: str) -> list[dict[str, Any]]:
    """Parse VibeVoice JSON transcription output into segments.

    The model outputs JSON segments with keys:
    "Start time", "End time", "Speaker ID", "Content"
    """
    text = raw_text.strip()
    if not text:
        return []

    json_start = text.find("[")
    json_end = text.rfind("]")
    if json_start == -1 or json_end == -1:
        logger.warning("[VibeVoiceApiTranscriber] No JSON array found in response")
        return []

    json_text = text[json_start : json_end + 1]

    try:
        segments = json.loads(json_text)
    except json.JSONDecodeError:
        logger.warning("[VibeVoiceApiTranscriber] Failed to parse JSON from response")
        return []

    if not isinstance(segments, list):
        return []

    result = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        result.append(
            {
                "start_time": seg.get("Start time", seg.get("start_time", "0:00.000")),
                "end_time": seg.get("End time", seg.get("end_time", "0:00.000")),
                "text": seg.get("Content", seg.get("text", "")).strip(),
                "speaker_id": seg.get("Speaker ID", seg.get("speaker_id", "")),
            }
        )
    return result


class VibeVoiceApiTranscriber(Transcriber):
    """Transcriber that calls VibeVoice via vLLM OpenAI-compatible API."""

    def __init__(
        self,
        api_url: str,
        max_new_tokens: int = 8192,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.api_url = api_url.rstrip("/")
        self.max_new_tokens = max_new_tokens

    def transcribe(
        self,
        file_path: str,
        progress_callback: Any = None,
        cancel_check: Any = None,
    ) -> TranscriptionResult:
        total_start_time = time.time()

        try:
            if cancel_check and cancel_check():
                raise TranscriptionCancelled("任务已取消，停止转录。")

            if progress_callback:
                progress_callback(0.05)

            with open(file_path, "rb") as f:
                audio_bytes = f.read()

            mime = _guess_mime_type(file_path)
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            data_url = f"data:{mime};base64,{audio_b64}"

            if progress_callback:
                progress_callback(0.1)

            duration = _get_audio_duration_ffprobe(file_path)
            if duration > 0:
                prompt_text = (
                    f"This is a {duration:.2f} seconds audio, please transcribe it with these keys: "
                    + ", ".join(_VIBEVOICE_SHOW_KEYS)
                )
            else:
                prompt_text = "Please transcribe this audio with these keys: " + ", ".join(
                    _VIBEVOICE_SHOW_KEYS
                )

            url = f"{self.api_url}/v1/chat/completions"
            payload = {
                "model": "vibevoice",
                "messages": [
                    {
                        "role": "system",
                        "content": _VIBEVOICE_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "audio_url", "audio_url": {"url": data_url}},
                            {"type": "text", "text": prompt_text},
                        ],
                    },
                ],
                "max_tokens": self.max_new_tokens,
                "temperature": 0.0,
                "stream": True,
                "top_p": 1.0,
            }

            if cancel_check and cancel_check():
                raise TranscriptionCancelled("任务已取消，停止转录。")

            if progress_callback:
                progress_callback(0.15)

            transcribe_start_time = time.time()
            logger.info(f"[VibeVoiceApiTranscriber] Sending request to {url}")

            collected_content = ""
            with httpx.stream(
                "POST",
                url,
                json=payload,
                timeout=httpx.Timeout(600.0, connect=30.0),
            ) as response:
                response.raise_for_status()

                for line in response.iter_lines():
                    if cancel_check and cancel_check():
                        raise TranscriptionCancelled("任务已取消，停止转录。")

                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0]["delta"]
                        content = delta.get("content", "")
                        if content:
                            collected_content += content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

            if progress_callback:
                progress_callback(0.7)

            raw_segments = _parse_transcription_text(collected_content)

            result_segments = []
            audio_duration = 0.0
            for segment in raw_segments:
                start = _parse_timestamp(segment.get("start_time", "0:00.000"))
                end = _parse_timestamp(segment.get("end_time", "0:00.000"))
                audio_duration = max(audio_duration, end)

                result_segments.append(
                    {
                        "start": start,
                        "end": end,
                        "text": segment.get("text", ""),
                        "speaker_id": segment.get("speaker_id", ""),
                    }
                )

            if progress_callback:
                progress_callback(1.0)

            transcription_time = time.time() - transcribe_start_time
            total_time = time.time() - total_start_time
            real_time_factor = (
                transcription_time / audio_duration if audio_duration > 0 else 0.0
            )

            logger.info(
                f"[VibeVoiceApiTranscriber] Transcription complete: "
                f"{len(result_segments)} segments, {transcription_time:.2f}s"
            )

            return TranscriptionResult(
                segments=result_segments,
                transcription_time=transcription_time,
                real_time_factor=real_time_factor,
                total_time=total_time,
                model_load_time=0.0,
                audio_duration=audio_duration,
                language="unknown",
                language_probability=0.0,
            )
        except TranscriptionCancelled:
            raise
        except httpx.HTTPStatusError as e:
            raise TranscriptionError(
                f"VibeVoice API 返回错误 ({e.response.status_code}): {e.response.text[:500]}"
            ) from e
        except httpx.ConnectError as e:
            raise TranscriptionError(
                f"无法连接 VibeVoice 推理服务 ({self.api_url}): {e}"
            ) from e
        except Exception as e:
            raise TranscriptionError(f"API 转录文件 '{file_path}' 时发生错误: {e}") from e
