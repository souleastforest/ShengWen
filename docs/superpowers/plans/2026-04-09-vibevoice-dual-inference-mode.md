# VibeVoice Dual Inference Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add dual inference mode (local + API) for VibeVoice ASR and fix sharded model path validation bug.

**Architecture:** VibeVoiceApiTranscriber implements the same `Transcriber` interface as VibeVoiceAsrTranscriber but sends audio via HTTP to a vLLM API endpoint. VibeVoiceServiceManager manages the vLLM subprocess lifecycle (start/stop/health-check/port-scan). The existing factory dispatch in `get_transcriber_worker` branches on `vibevoice_inference_mode` setting. Frontend gets an inference mode toggle in SettingsModal.

**Tech Stack:** Python 3 (httpx, subprocess, asyncio), Vue 3 + TypeScript, vitest, pytest

---

## File Structure

```
# Backend — Modified
src/main/python/sheng_wen/transcriber/vibevoice_model_validator.py  # Bug fix: add sharded index files
src/main/python/sheng_wen/config/settings.py                         # WhisperConfig + persistence: new fields
src/main/python/sheng_wen/transcriber/settings_manager.py            # TranscriptionSettingsManager: new fields
src/main/python/sheng_wen/infra/api/routes/schemas.py                # Pydantic models: new fields
src/main/python/sheng_wen/infra/api/routes/settings.py               # New API endpoints
src/main/python/sheng_wen/api.py                                     # Factory dispatch + service manager init + shutdown

# Backend — New
src/main/python/sheng_wen/transcriber/vibe_voice_api_transcriber.py  # API inference transcriber
src/main/python/sheng_wen/transcriber/vibevoice_service_manager.py   # vLLM subprocess manager

# Backend — Tests (Modified/New)
src/test/python/test_vibevoice_model_validator.py                    # Add sharded format tests

# Frontend — Modified
frontend/src/types.ts                                                # New types/fields
frontend/src/composables/useTaskViewModel.ts                         # New API functions
frontend/src/components/SettingsModal.vue                            # Inference mode UI

# Frontend — Tests
frontend/src/__tests__/SettingsModal.vibevoice.test.ts               # Update existing tests
```

---

## Task 1: Bug Fix — Sharded Model File Detection

**Files:**
- Modify: `src/main/python/sheng_wen/transcriber/vibevoice_model_validator.py:17-21`
- Modify: `src/test/python/test_vibevoice_model_validator.py`

- [ ] **Step 1: Add sharded index files to MODEL_WEIGHT_FILES**

In `vibevoice_model_validator.py`, expand the `MODEL_WEIGHT_FILES` set:

```python
# Model weight files (at least one required)
MODEL_WEIGHT_FILES = {
    "pytorch_model.bin",
    "model.safetensors",
    "model.safetensors.index.json",   # sharded safetensors index
    "pytorch_model.bin.index.json",   # sharded pytorch index
}
```

- [ ] **Step 2: Add test for sharded safetensors format**

In `test_vibevoice_model_validator.py`, add to `TestValidateVibeVoiceModelPath`:

```python
def test_valid_model_with_sharded_safetensors(self):
    """Sharded models have model.safetensors.index.json instead of model.safetensors."""
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = _create_model_dir(
            Path(tmp),
            config_json=True,
            model_weights=None,  # no single weight file
        )
        # Create sharded index file
        (model_dir / "model.safetensors.index.json").write_text(
            '{"metadata": {"total_size": 12345}}', encoding="utf-8"
        )
        # Create a shard file (not required for validation, but realistic)
        (model_dir / "model-00001-of-00008.safetensors").write_bytes(b"\x00" * 16)

        result = validate_vibevoice_model_path(str(model_dir))
        self.assertTrue(result.valid)
        self.assertEqual(result.missing_files, [])
        self.assertIn("校验通过", result.message)

def test_valid_model_with_sharded_pytorch(self):
    """Sharded pytorch models have pytorch_model.bin.index.json."""
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = _create_model_dir(
            Path(tmp),
            config_json=True,
            model_weights=None,
        )
        (model_dir / "pytorch_model.bin.index.json").write_text(
            '{"metadata": {"total_size": 12345}}', encoding="utf-8"
        )

        result = validate_vibevoice_model_path(str(model_dir))
        self.assertTrue(result.valid)
```

Also add to `TestModuleConstants`:

```python
def test_model_weight_files_includes_sharded_index(self):
    self.assertIn("model.safetensors.index.json", MODEL_WEIGHT_FILES)
    self.assertIn("pytorch_model.bin.index.json", MODEL_WEIGHT_FILES)
```

Also update `TestPerformLightweightLoadTest` to add:

```python
def test_sharded_safetensors_passes_load_test(self):
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = _create_model_dir(
            Path(tmp),
            config_json=True,
            model_weights=None,
        )
        (model_dir / "model.safetensors.index.json").write_text(
            '{"metadata": {"total_size": 12345}}', encoding="utf-8"
        )
        result = perform_lightweight_load_test(str(model_dir))
        self.assertTrue(result.valid)
        self.assertTrue(result.details.get("config_loaded"))
```

- [ ] **Step 3: Run tests to verify**

Run: `cd /home/admin/projects/ShengWen && uv run python -m pytest src/test/python/test_vibevoice_model_validator.py -v`
Expected: All tests PASS, including new sharded tests.

- [ ] **Step 4: Commit**

```bash
git add src/main/python/sheng_wen/transcriber/vibevoice_model_validator.py src/test/python/test_vibevoice_model_validator.py
git commit -m "fix: support sharded safetensors/pytorch model file detection in VibeVoice validator"
```

---

## Task 2: Config Layer — New Inference Mode Fields

**Files:**
- Modify: `src/main/python/sheng_wen/config/settings.py` (WhisperConfig + persistence)
- Modify: `src/main/python/sheng_wen/infra/api/routes/schemas.py` (Pydantic models)
- Modify: `src/main/python/sheng_wen/transcriber/settings_manager.py` (settings manager)
- Modify: `src/main/python/sheng_wen/api.py` (init params)

- [ ] **Step 1: Update WhisperConfig dataclass**

In `settings.py`, add fields to `WhisperConfig` (around line 68, after `vibevoice_dtype`):

```python
    vibevoice_inference_mode: Literal["local", "api"] = "local"
    vibevoice_api_url: str = ""
```

No changes to `__post_init__` needed — defaults are sufficient.

- [ ] **Step 2: Update JSONConfigManager.save_transcription_config**

In `settings.py` `save_transcription_config` method, add after the existing `vibevoice_dtype` block (around line 396):

```python
        if "vibevoice_inference_mode" in payload:
            mode = str(payload.get("vibevoice_inference_mode") or "local").strip().lower()
            whisper_patch["vibevoice_inference_mode"] = (
                mode if mode in {"local", "api"} else "local"
            )
        if "vibevoice_api_url" in payload:
            whisper_patch["vibevoice_api_url"] = str(
                payload.get("vibevoice_api_url") or ""
            )
```

- [ ] **Step 3: Update JSONConfigManager._load_whisper_config**

In `_load_whisper_config` method, add parsing for the new fields (after existing vibevoice_dtype parsing, around line 506):

```python
        vibevoice_inference_mode = str(
            raw.get("vibevoice_inference_mode", defaults.get("vibevoice_inference_mode", "local"))
        ).strip().lower()
        if vibevoice_inference_mode not in {"local", "api"}:
            vibevoice_inference_mode = "local"
        vibevoice_api_url = str(
            raw.get("vibevoice_api_url", defaults.get("vibevoice_api_url", ""))
        )
```

And include them in the WhisperConfig constructor call (around line 509):

```python
            vibevoice_inference_mode=vibevoice_inference_mode,  # type: ignore[arg-type]
            vibevoice_api_url=vibevoice_api_url,
```

- [ ] **Step 4: Update Pydantic schemas**

In `schemas.py`, add to `TranscriptionSettings` (after `vibevoice_dtype` line ~114):

```python
    vibevoice_inference_mode: str = "local"
    vibevoice_api_url: str = ""
```

Add to `TranscriptionSettingsUpdate` (after `vibevoice_dtype` line ~150):

```python
    vibevoice_inference_mode: Optional[str] = Field(
        default=None, description="VibeVoice 推理模式: local 或 api"
    )
    vibevoice_api_url: Optional[str] = Field(
        default=None, description="VibeVoice 推理服务地址"
    )
```

Add a new Pydantic model for service management responses (at end of file, before existing utility models):

```python
class VibeVoiceServiceScanResult(BaseModel):
    url: str
    status: str  # "available" | "unreachable"

class VibeVoiceServiceStatus(BaseModel):
    running: bool
    pid: Optional[int] = None
    api_url: str = ""
    api_healthy: bool = False
```

- [ ] **Step 5: Update TranscriptionSettingsManager**

In `settings_manager.py`:

5a. Add `__init__` params (around line 297, after `vibevoice_dtype`):

```python
        vibevoice_inference_mode: str = "local",
        vibevoice_api_url: str = "",
```

And store them:

```python
        self._vibevoice_inference_mode = (
            vibevoice_inference_mode if vibevoice_inference_mode in {"local", "api"} else "local"
        )
        self._vibevoice_api_url = str(vibevoice_api_url or "")
```

5b. Add to `get_settings` return dict (after `vibevoice_dtype`):

```python
            "vibevoice_inference_mode": vibevoice_inference_mode,
            "vibevoice_api_url": vibevoice_api_url,
```

And add reads from lock:

```python
            vibevoice_inference_mode = self._vibevoice_inference_mode
            vibevoice_api_url = self._vibevoice_api_url
```

5c. Add to `update_settings` method signature and body (after `vibevoice_dtype` param):

```python
        vibevoice_inference_mode: str | None = None,
        vibevoice_api_url: str | None = None,
```

Add the "at least one param" check:

```python
            and vibevoice_inference_mode is None
            and vibevoice_api_url is None
```

Add update logic (inside the `with self._lock:` block, after vibevoice_dtype update):

```python
            if vibevoice_inference_mode is not None:
                mode = str(vibevoice_inference_mode or "local").strip().lower()
                self._vibevoice_inference_mode = mode if mode in {"local", "api"} else "local"
                logger.info(
                    f"[TranscriptionSettingsManager] 已更新 VibeVoice 推理模式: {self._vibevoice_inference_mode}"
                )

            if vibevoice_api_url is not None:
                self._vibevoice_api_url = str(vibevoice_api_url or "").strip()
                logger.info(
                    f"[TranscriptionSettingsManager] 已更新 VibeVoice API URL: {self._vibevoice_api_url}"
                )
```

5d. Add to `get_runtime_state` return dict:

```python
                "vibevoice_inference_mode": self._vibevoice_inference_mode,
                "vibevoice_api_url": self._vibevoice_api_url,
```

- [ ] **Step 6: Update settings.py route handler**

In `settings.py` routes file, update `update_transcription_settings` to pass new params:

```python
        settings = request.app.state.transcription_settings_manager.update_settings(
            device=payload.device,
            model_source=payload.model_source,
            model_size=payload.model_size,
            model_path=payload.model_path,
            enable_bilibili_subtitle_fetch=payload.enable_bilibili_subtitle_fetch,
            bilibili_sessdata=payload.bilibili_sessdata,
            clear_bilibili_sessdata=payload.clear_bilibili_sessdata,
            transcriber_type=payload.transcriber_type,
            vibevoice_language_model=payload.vibevoice_language_model,
            vibevoice_max_new_tokens=payload.vibevoice_max_new_tokens,
            vibevoice_dtype=payload.vibevoice_dtype,
            vibevoice_inference_mode=payload.vibevoice_inference_mode,
            vibevoice_api_url=payload.vibevoice_api_url,
        )
```

- [ ] **Step 7: Update api.py init**

In `api.py`, add the new params to `TranscriptionSettingsManager` constructor (around line 62):

```python
transcription_settings_manager = TranscriptionSettingsManager(
    ...,
    vibevoice_inference_mode=whisper_cfg.vibevoice_inference_mode,
    vibevoice_api_url=whisper_cfg.vibevoice_api_url,
)
```

- [ ] **Step 8: Run existing tests + lint**

Run: `cd /home/admin/projects/ShengWen && uv run python -m pytest src/test/python/test_vibevoice_model_validator.py -v`
Run: `./scripts/lint.sh src/main/python/sheng_wen/config/settings.py src/main/python/sheng_wen/infra/api/routes/schemas.py src/main/python/sheng_wen/transcriber/settings_manager.py src/main/python/sheng_wen/infra/api/routes/settings.py src/main/python/sheng_wen/api.py`
Expected: No lint errors, tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/main/python/sheng_wen/config/settings.py src/main/python/sheng_wen/infra/api/routes/schemas.py src/main/python/sheng_wen/transcriber/settings_manager.py src/main/python/sheng_wen/infra/api/routes/settings.py src/main/python/sheng_wen/api.py
git commit -m "feat(config): add VibeVoice inference mode and API URL settings"
```

---

## Task 3: VibeVoiceApiTranscriber — API Inference Client

**Files:**
- Create: `src/main/python/sheng_wen/transcriber/vibe_voice_api_transcriber.py`

- [ ] **Step 1: Add httpx dependency**

```bash
cd /home/admin/projects/ShengWen && uv add httpx
```

- [ ] **Step 2: Create VibeVoiceApiTranscriber**

Create `src/main/python/sheng_wen/transcriber/vibe_voice_api_transcriber.py`:

```python
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


# MIME type mapping for audio files
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
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
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

    # Try to extract JSON array from the text
    # The model may wrap output in markdown code blocks
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
        result.append({
            "start_time": seg.get("Start time", seg.get("start_time", "0:00.000")),
            "end_time": seg.get("End time", seg.get("end_time", "0:00.000")),
            "text": seg.get("Content", seg.get("text", "")).strip(),
            "speaker_id": seg.get("Speaker ID", seg.get("speaker_id", "")),
        })
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

            # Read and encode audio
            with open(file_path, "rb") as f:
                audio_bytes = f.read()

            mime = _guess_mime_type(file_path)
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            data_url = f"data:{mime};base64,{audio_b64}"

            if progress_callback:
                progress_callback(0.1)

            # Get duration for prompt
            duration = _get_audio_duration_ffprobe(file_path)
            if duration > 0:
                prompt_text = (
                    f"This is a {duration:.2f} seconds audio, please transcribe it with these keys: "
                    + ", ".join(_VIBEVOICE_SHOW_KEYS)
                )
            else:
                prompt_text = (
                    "Please transcribe this audio with these keys: "
                    + ", ".join(_VIBEVOICE_SHOW_KEYS)
                )

            # Build OpenAI chat completions request
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

            # Send request with streaming
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

            # Parse transcription output
            raw_segments = _parse_transcription_text(collected_content)

            result_segments = []
            audio_duration = 0.0
            for segment in raw_segments:
                start = _parse_timestamp(segment.get("start_time", "0:00.000"))
                end = _parse_timestamp(segment.get("end_time", "0:00.000"))
                audio_duration = max(audio_duration, end)

                result_segments.append({
                    "start": start,
                    "end": end,
                    "text": segment.get("text", ""),
                    "speaker_id": segment.get("speaker_id", ""),
                })

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
                model_load_time=0.0,  # no local model loading
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
```

- [ ] **Step 3: Lint**

Run: `./scripts/lint.sh src/main/python/sheng_wen/transcriber/vibe_voice_api_transcriber.py`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock src/main/python/sheng_wen/transcriber/vibe_voice_api_transcriber.py
git commit -m "feat(transcriber): add VibeVoiceApiTranscriber for remote vLLM API inference"
```

---

## Task 4: VibeVoiceServiceManager — vLLM Subprocess Management

**Files:**
- Create: `src/main/python/sheng_wen/transcriber/vibevoice_service_manager.py`

- [ ] **Step 1: Create VibeVoiceServiceManager**

Create `src/main/python/sheng_wen/transcriber/vibevoice_service_manager.py`:

```python
"""VibeVoice vLLM service manager for local subprocess lifecycle management."""

from __future__ import annotations

import asyncio
import subprocess
import urllib.request
import urllib.error
from typing import Any

from loguru import logger


class VibeVoiceServiceManager:
    """Manages a local vLLM subprocess for VibeVoice ASR inference."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._port: int = 0

    @property
    def is_running(self) -> bool:
        """Check if the subprocess is still alive."""
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        """Get the subprocess PID, or None if not running."""
        if self.is_running:
            return self._process.pid
        return None

    @property
    def api_url(self) -> str:
        """Get the API URL for the running service."""
        if self._port:
            return f"http://localhost:{self._port}"
        return ""

    def start_service(
        self,
        model_path: str,
        port: int = 8000,
        dtype: str = "bfloat16",
    ) -> dict[str, Any]:
        """Start a vLLM subprocess serving the VibeVoice model.

        Args:
            model_path: Path to the VibeVoice model directory.
            port: Port to serve on.
            dtype: Model data type (bfloat16 or float16).

        Returns:
            dict with success status and message.
        """
        if self.is_running:
            return {
                "success": False,
                "message": f"服务已在运行中 (PID: {self.pid}, port: {self._port})",
            }

        cmd = [
            "vllm", "serve", model_path,
            "--served-model-name", "vibevoice",
            "--trust-remote-code",
            "--dtype", dtype,
            "--max-num-seqs", "8",
            "--max-model-len", "65536",
            "--port", str(port),
        ]

        logger.info(f"[VibeVoiceServiceManager] Starting vLLM: {' '.join(cmd)}")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._port = port
            logger.info(
                f"[VibeVoiceServiceManager] vLLM subprocess started (PID: {self._process.pid})"
            )
            return {
                "success": True,
                "message": f"vLLM 服务已启动 (PID: {self._process.pid}, port: {port})",
                "pid": self._process.pid,
            }
        except FileNotFoundError:
            return {
                "success": False,
                "message": "未找到 vllm 命令，请先安装 vLLM: pip install vllm",
            }
        except Exception as e:
            logger.error(f"[VibeVoiceServiceManager] Failed to start vLLM: {e}")
            return {"success": False, "message": f"启动失败: {e}"}

    def stop_service(self) -> dict[str, Any]:
        """Stop the vLLM subprocess."""
        if not self.is_running:
            # Clean up stale process reference
            if self._process is not None:
                self._process = None
                self._port = 0
            return {"success": True, "message": "服务未在运行"}

        pid = self._process.pid
        logger.info(f"[VibeVoiceServiceManager] Stopping vLLM (PID: {pid})")

        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"[VibeVoiceServiceManager] Process {pid} did not terminate, killing..."
                )
                self._process.kill()
                self._process.wait(timeout=5)

            self._process = None
            self._port = 0
            logger.info(f"[VibeVoiceServiceManager] vLLM process {pid} stopped")
            return {"success": True, "message": f"服务已停止 (PID: {pid})"}
        except Exception as e:
            logger.error(f"[VibeVoiceServiceManager] Error stopping process: {e}")
            self._process = None
            self._port = 0
            return {"success": False, "message": f"停止失败: {e}"}

    async def health_check(self, url: str | None = None) -> dict[str, Any]:
        """Check if a VibeVoice vLLM service is healthy.

        Args:
            url: Service URL to check. Defaults to the managed service URL.
        """
        check_url = url or self.api_url
        if not check_url:
            return {"healthy": False, "message": "未指定服务地址"}

        try:
            req = urllib.request.Request(f"{check_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return {"healthy": True, "message": "服务正常运行"}
                return {"healthy": False, "message": f"服务返回状态码: {resp.status}"}
        except urllib.error.URLError:
            return {"healthy": False, "message": "服务不可达"}
        except Exception as e:
            return {"healthy": False, "message": f"健康检查失败: {e}"}

    async def scan_local_ports(self) -> list[dict[str, str]]:
        """Scan localhost ports 8000-8010 for vLLM services.

        Returns:
            List of dicts with url and status for each port.
        """
        results: list[dict[str, str]] = []

        async def _check_port(port: int) -> dict[str, str]:
            url = f"http://localhost:{port}"
            try:
                req = urllib.request.Request(
                    f"{url}/v1/models", method="GET"
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        data = resp.read().decode("utf-8")
                        # Check if "vibevoice" is in the model list
                        if "vibevoice" in data.lower():
                            return {"url": url, "status": "available"}
                        return {"url": url, "status": "available"}
                    return {"url": url, "status": "unreachable"}
            except Exception:
                return {"url": url, "status": "unreachable"}

        tasks = [_check_port(port) for port in range(8000, 8011)]
        results = await asyncio.gather(*tasks)

        # Filter to only show reachable services
        return [r for r in results if r["status"] == "available"] or results

    async def shutdown(self) -> None:
        """Called during application shutdown to clean up."""
        if self.is_running:
            logger.info("[VibeVoiceServiceManager] Shutdown: stopping vLLM service")
            self.stop_service()
```

- [ ] **Step 2: Lint**

Run: `./scripts/lint.sh src/main/python/sheng_wen/transcriber/vibevoice_service_manager.py`

- [ ] **Step 3: Commit**

```bash
git add src/main/python/sheng_wen/transcriber/vibevoice_service_manager.py
git commit -m "feat(transcriber): add VibeVoiceServiceManager for vLLM subprocess management"
```

---

## Task 5: Backend — Factory Dispatch + API Endpoints + Shutdown

**Files:**
- Modify: `src/main/python/sheng_wen/api.py` (factory dispatch + service manager init + shutdown)
- Modify: `src/main/python/sheng_wen/infra/api/routes/settings.py` (new endpoints)

- [ ] **Step 1: Update factory dispatch in api.py**

In `get_transcriber_worker` (around line 154), update the `vibe_voice_asr` branch:

```python
    if transcriber_type == "vibe_voice_asr":
        inference_mode = str(
            runtime_transcription_state.get("vibevoice_inference_mode", "local")
        )

        if inference_mode == "api":
            api_url = str(
                runtime_transcription_state.get("vibevoice_api_url", "")
            ).strip()
            if not api_url:
                raise ValueError("API 推理模式需要填写推理服务地址")
            logger.info(
                f"[Transcriber] VibeVoice API 模式, URL: {api_url}"
            )
            transcriber_config = {
                "api_url": api_url,
                "max_new_tokens": runtime_transcription_state.get(
                    "vibevoice_max_new_tokens", 8192
                ),
            }
        else:
            # Local mode (existing logic)
            logger.info(
                f"[Transcriber] VibeVoice-ASR 本地模式, 路径: {transcriber_config.get('model_path')}"
            )
            import torch
            dtype_str = runtime_transcription_state.get("vibevoice_dtype", "bfloat16")
            dtype = torch.bfloat16 if dtype_str == "bfloat16" else torch.float16
            transcriber_config["language_model_pretrained_name"] = runtime_transcription_state.get(
                "vibevoice_language_model", "Qwen/Qwen2.5-7B"
            )
            transcriber_config["max_new_tokens"] = runtime_transcription_state.get(
                "vibevoice_max_new_tokens", 8192
            )
            transcriber_config["dtype"] = dtype
```

Note: The `api` mode skips `build_transcriber_kwargs()` entirely and builds its own config dict, since it doesn't need device/model_path/model_source.

Also update the `model_source` and `else` branches to preserve existing logic. The full `get_transcriber_worker` flow becomes:

```python
async def get_transcriber_worker():
    global transcriber_worker
    if transcriber_worker is not None:
        return transcriber_worker
    from .transcriber.transcriber import get_transcriber
    from .transcriber.transcriber_worker import TranscriberWorker

    runtime_transcription_state = transcription_settings_manager.get_runtime_state()
    transcriber_type = str(
        runtime_transcription_state.get("transcriber_type") or "fast_whisper"
    )

    if transcriber_type == "vibe_voice_asr":
        inference_mode = str(
            runtime_transcription_state.get("vibevoice_inference_mode", "local")
        )
        if inference_mode == "api":
            api_url = str(
                runtime_transcription_state.get("vibevoice_api_url", "")
            ).strip()
            if not api_url:
                raise ValueError("API 推理模式需要填写推理服务地址")
            logger.info(f"[Transcriber] VibeVoice API 模式, URL: {api_url}")
            transcriber_config = {
                "api_url": api_url,
                "max_new_tokens": runtime_transcription_state.get(
                    "vibevoice_max_new_tokens", 8192
                ),
            }
        else:
            model_source = str(
                runtime_transcription_state.get("model_source") or "auto_download"
            )
            model_path = str(
                runtime_transcription_state.get("model_path") or ""
            )
            import torch
            dtype_str = runtime_transcription_state.get("vibevoice_dtype", "bfloat16")
            dtype = torch.bfloat16 if dtype_str == "bfloat16" else torch.float16
            transcriber_config = {
                "model_path": model_path,
                "device": runtime_transcription_state.get("device", "cuda"),
                "language_model_pretrained_name": runtime_transcription_state.get(
                    "vibevoice_language_model", "Qwen/Qwen2.5-7B"
                ),
                "max_new_tokens": runtime_transcription_state.get(
                    "vibevoice_max_new_tokens", 8192
                ),
                "dtype": dtype,
            }
            logger.info(f"[Transcriber] VibeVoice-ASR 本地模式, 路径: {model_path}")
    else:
        transcriber_config = transcription_settings_manager.build_transcriber_kwargs()
        model_source = str(
            runtime_transcription_state.get("model_source") or "auto_download"
        )
        if model_source == "manual_path":
            logger.info(
                f"[Transcriber] 使用本地模型路径: {transcriber_config.get('model_size_or_path')}"
            )
        else:
            logger.info(
                f"[Transcriber] 使用模型大小: {transcriber_config.get('model_size')}"
            )

    transcriber = get_transcriber(transcriber_type, **transcriber_config)
    llm_w = await get_llm_worker()
    transcriber_worker = TranscriberWorker(
        name="TranscriberWorker", transcriber=transcriber, next_worker=llm_w
    )
    transcription_settings_manager.bind_transcriber_worker(transcriber_worker)
    transcriber_worker.start()
    _sync_worker_state()
    return transcriber_worker
```

- [ ] **Step 2: Initialize VibeVoiceServiceManager in api.py**

Add import and module-level init (near the top, after existing imports):

```python
from src.main.python.sheng_wen.transcriber.vibevoice_service_manager import (
    VibeVoiceServiceManager,
)

vibevoice_service_manager = VibeVoiceServiceManager()
```

Add to `app.state` initialization (around line 115):

```python
app.state.vibevoice_service_manager = vibevoice_service_manager
```

- [ ] **Step 3: Add shutdown cleanup to lifespan**

Update `lifespan` in `api.py`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await pipeline.start()
    yield
    await pipeline.stop()
    await stop_all_workers()
    await vibevoice_service_manager.shutdown()
```

- [ ] **Step 4: Add new API endpoints in settings.py**

Add to `src/main/python/sheng_wen/infra/api/routes/settings.py` (at end of file, before summarization routes):

```python
from src.main.python.sheng_wen.infra.api.routes.schemas import (
    # ... existing imports ...
    VibeVoiceServiceScanResult,
    VibeVoiceServiceStatus,
)


@router.post(
    "/transcription/settings/vibevoice-scan",
    response_model=list[VibeVoiceServiceScanResult],
)
async def vibevoice_scan_services(request: Request):
    """Scan localhost ports 8000-8010 for VibeVoice vLLM services."""
    mgr = request.app.state.vibevoice_service_manager
    results = await mgr.scan_local_ports()
    return [VibeVoiceServiceScanResult(**r) for r in results]


@router.post(
    "/transcription/settings/vibevoice-service/start",
)
async def vibevoice_service_start(request: Request):
    """Start a local vLLM subprocess for VibeVoice inference."""
    import json

    body = await request.json()
    model_path = str(body.get("model_path", "")).strip()
    port = int(body.get("port", 8000))
    dtype = str(body.get("dtype", "bfloat16"))

    if not model_path:
        raise HTTPException(status_code=400, detail="请填写模型目录")

    mgr = request.app.state.vibevoice_service_manager
    result = mgr.start_service(model_path=model_path, port=port, dtype=dtype)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "启动失败"))
    return result


@router.post(
    "/transcription/settings/vibevoice-service/stop",
)
async def vibevoice_service_stop(request: Request):
    """Stop the managed vLLM subprocess."""
    mgr = request.app.state.vibevoice_service_manager
    result = mgr.stop_service()
    return result


@router.get(
    "/transcription/settings/vibevoice-service/status",
    response_model=VibeVoiceServiceStatus,
)
async def vibevoice_service_status(request: Request):
    """Get the status of the managed vLLM subprocess and its API health."""
    mgr = request.app.state.vibevoice_service_manager
    health = await mgr.health_check()
    return VibeVoiceServiceStatus(
        running=mgr.is_running,
        pid=mgr.pid,
        api_url=mgr.api_url,
        api_healthy=health.get("healthy", False),
    )
```

- [ ] **Step 5: Lint**

Run: `./scripts/lint.sh src/main/python/sheng_wen/api.py src/main/python/sheng_wen/infra/api/routes/settings.py`

- [ ] **Step 6: Commit**

```bash
git add src/main/python/sheng_wen/api.py src/main/python/sheng_wen/infra/api/routes/settings.py
git commit -m "feat(api): add VibeVoice service endpoints and factory dispatch for API mode"
```

---

## Task 6: Frontend — Types + API Client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/composables/useTaskViewModel.ts`

- [ ] **Step 1: Update types.ts**

Add new fields to `TranscriptionSettings` interface (after `vibevoice_dtype`):

```typescript
  vibevoice_inference_mode: "local" | "api";
  vibevoice_api_url: string;
```

Add new fields to `UpdateTranscriptionSettingsRequest` interface (after `vibevoice_dtype`):

```typescript
  vibevoice_inference_mode?: "local" | "api";
  vibevoice_api_url?: string;
```

Add new types (after `ModelPathValidationResult`):

```typescript
export interface VibeVoiceServiceScanResult {
  url: string;
  status: "available" | "unreachable";
}

export interface VibeVoiceServiceStatus {
  running: boolean;
  pid: number | null;
  api_url: string;
  api_healthy: boolean;
}
```

- [ ] **Step 2: Update useTaskViewModel.ts**

Add new refs (after existing vibevoice refs):

```typescript
const vibevoiceInferenceMode = ref<"local" | "api">(
  transcriptionSettings.value?.vibevoice_inference_mode || "local"
)
const vibevoiceApiUrl = ref(
  transcriptionSettings.value?.vibevoice_api_url || ""
)
const vibevoiceServiceStatus = ref<VibeVoiceServiceStatus | null>(null)
const isScanningVibeVoice = ref(false)
const isStartingVibeVoice = ref(false)
const isStoppingVibeVoice = ref(false)
```

Add watcher for settings sync (inside existing `watch(transcriptionSettings, ...)`):

```typescript
    vibevoiceInferenceMode.value = settings.vibevoice_inference_mode || "local"
    vibevoiceApiUrl.value = settings.vibevoice_api_url || ""
```

Add new API functions:

```typescript
  const scanVibeVoiceServices = async (): Promise<VibeVoiceServiceScanResult[]> => {
    isScanningVibeVoice.value = true
    try {
      const response = await axios.post(`${apiBaseUrl}/transcription/settings/vibevoice-scan`)
      return response.data as VibeVoiceServiceScanResult[]
    } catch (err) {
      console.error('Failed to scan VibeVoice services:', err)
      return []
    } finally {
      isScanningVibeVoice.value = false
    }
  }

  const startVibeVoiceService = async (modelPath: string, port: number, dtype: string): Promise<void> => {
    isStartingVibeVoice.value = true
    try {
      await axios.post(`${apiBaseUrl}/transcription/settings/vibevoice-service/start`, {
        model_path: modelPath,
        port,
        dtype,
      })
    } finally {
      isStartingVibeVoice.value = false
    }
  }

  const stopVibeVoiceService = async (): Promise<void> => {
    isStoppingVibeVoice.value = true
    try {
      await axios.post(`${apiBaseUrl}/transcription/settings/vibevoice-service/stop`)
    } finally {
      isStoppingVibeVoice.value = false
    }
  }

  const fetchVibeVoiceServiceStatus = async (): Promise<VibeVoiceServiceStatus> => {
    const response = await axios.get(`${apiBaseUrl}/transcription/settings/vibevoice-service/status`)
    vibevoiceServiceStatus.value = response.data
    return response.data as VibeVoiceServiceStatus
  }
```

Export the new functions and refs (add to the return statement of the composable).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts frontend/src/composables/useTaskViewModel.ts
git commit -m "feat(frontend): add VibeVoice inference mode types and API client functions"
```

---

## Task 7: Frontend — SettingsModal UI

**Files:**
- Modify: `frontend/src/components/SettingsModal.vue`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Update App.vue to pass new props/events to SettingsModal**

Add new event handlers in App.vue's `handleUpdateTranscriptionSettings` payload type:

```typescript
  vibevoice_inference_mode?: "local" | "api"
  vibevoice_api_url?: string
```

Pass new state from useTaskViewModel to SettingsModal (add props and event bindings).

- [ ] **Step 2: Update SettingsModal.vue — Props, emits, and state**

Add new refs for inference mode:

```typescript
const vibevoiceInferenceMode = ref<"local" | "api">("local")
const vibevoiceApiUrl = ref("")
```

Add to the `watch(() => props.transcriptionSettings, ...)` block:

```typescript
    vibevoiceInferenceMode.value = settings.vibevoice_inference_mode || "local"
    vibevoiceApiUrl.value = settings.vibevoice_api_url || ""
```

Update `isVibeVoiceAvailable` computed:

```typescript
const isVibeVoiceAvailable = computed(() => {
  const cuda = props.transcriptionSettings?.cuda_available ?? false
  const apiMode = vibevoiceInferenceMode.value === "api"
  return cuda || apiMode
})
```

Update `handleSaveTranscriptionSettings` to include new fields in the `vibe_voice_asr` branch:

```typescript
    payload.vibevoice_inference_mode = vibevoiceInferenceMode.value
    payload.vibevoice_api_url = vibevoiceApiUrl.value.trim()
```

Update the `updateTranscriptionSettings` emit type to include new fields.

- [ ] **Step 3: Update SettingsModal.vue — Template for inference mode toggle**

Inside the VibeVoice config `<div v-if="transcriberType === 'vibe_voice_asr'">` block, add inference mode toggle after the model directory section and before the language model section. The toggle should look like:

```html
                <!-- 推理模式选择 -->
                <div>
                  <label class="block text-xs text-slate-600 mb-1.5">推理模式</label>
                  <div class="grid grid-cols-2 gap-3">
                    <button
                      @click="vibevoiceInferenceMode = 'local'"
                      :class="[
                        'px-4 py-3 rounded-xl border-2 text-left transition-all',
                        vibevoiceInferenceMode === 'local'
                          ? 'border-blue-500 bg-blue-50 text-blue-700'
                          : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
                      ]"
                    >
                      <div class="font-medium">本地加载</div>
                      <div class="text-xs text-slate-500 mt-0.5">需要 CUDA GPU</div>
                    </button>
                    <button
                      @click="vibevoiceInferenceMode = 'api'"
                      :class="[
                        'px-4 py-3 rounded-xl border-2 text-left transition-all',
                        vibevoiceInferenceMode === 'api'
                          ? 'border-blue-500 bg-blue-50 text-blue-700'
                          : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
                      ]"
                    >
                      <div class="font-medium">推理服务</div>
                      <div class="text-xs text-slate-500 mt-0.5">连接 vLLM API</div>
                    </button>
                  </div>
                </div>
```

- [ ] **Step 4: Add API mode UI (conditionally shown)**

When `vibevoiceInferenceMode === 'api'`, show:

```html
                <!-- API 模式配置 -->
                <div v-if="vibevoiceInferenceMode === 'api'" class="space-y-3">
                  <div>
                    <label class="block text-xs text-slate-600 mb-1.5">推理服务地址</label>
                    <input
                      v-model="vibevoiceApiUrl"
                      type="text"
                      placeholder="例如: http://localhost:8000"
                      class="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
                    >
                  </div>

                  <div class="flex items-center gap-2">
                    <button
                      @click="emit('scanVibeVoiceServices')"
                      :disabled="isScanningVibeVoice"
                      class="flex items-center gap-2 px-4 py-2 bg-white hover:bg-slate-50 text-slate-600 rounded-xl text-xs font-medium border border-slate-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <PhSpinner v-if="isScanningVibeVoice" :size="14" class="animate-spin" />
                      <span>{{ isScanningVibeVoice ? '扫描中...' : '扫描本地服务' }}</span>
                    </button>
                  </div>

                  <!-- 服务状态 -->
                  <div v-if="vibevoiceServiceStatus" class="flex items-center gap-2 text-xs">
                    <span :class="[
                      'px-2 py-0.5 rounded-full border',
                      vibevoiceServiceStatus.api_healthy
                        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                        : 'border-red-200 bg-red-50 text-red-700'
                    ]">
                      {{ vibevoiceServiceStatus.api_healthy ? '服务正常' : '服务不可达' }}
                    </span>
                    <span v-if="vibevoiceServiceStatus.running" class="text-slate-500">
                      PID: {{ vibevoiceServiceStatus.pid }}
                    </span>
                  </div>

                  <div class="text-xs text-slate-500">
                    输入 vLLM 服务地址，或点击"扫描"自动发现本地服务。
                  </div>
                </div>
```

- [ ] **Step 5: Conditionally hide local-mode-only fields**

Wrap the language model, max_new_tokens, dtype, and CUDA warning in `v-if="vibevoiceInferenceMode === 'local'"`. The model directory input should remain visible in both modes (it's needed for "启动本地服务" in API mode).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SettingsModal.vue frontend/src/App.vue
git commit -m "feat(frontend): add VibeVoice inference mode toggle and API service UI"
```

---

## Task 8: Update Frontend Tests

**Files:**
- Modify: `frontend/src/__tests__/SettingsModal.vibevoice.test.ts`

- [ ] **Step 1: Update existing tests for new default fields**

Update `createTranscriptionSettings` helper to include new fields:

```typescript
const createTranscriptionSettings = (
  overrides: Partial<TranscriptionSettings> = {},
): TranscriptionSettings => ({
  // ... existing fields ...
  vibevoice_inference_mode: "local",
  vibevoice_api_url: "",
  ...overrides,
})
```

- [ ] **Step 2: Add test for inference mode toggle**

```typescript
it('shows inference mode toggle when VibeVoice ASR is selected', async () => {
  const wrapper = createWrapper({
    transcriptionSettings: createTranscriptionSettings({
      cuda_available: true,
    }),
  })

  await openTranscriptionTab(wrapper)
  await switchToVibeVoice(wrapper)

  expect(wrapper.text()).toContain('本地加载')
  expect(wrapper.text()).toContain('推理服务')
})

it('shows API URL input when API mode is selected', async () => {
  const wrapper = createWrapper({
    transcriptionSettings: createTranscriptionSettings({
      cuda_available: true,
    }),
  })

  await openTranscriptionTab(wrapper)
  await switchToVibeVoice(wrapper)

  // Switch to API mode
  const apiModeBtn = wrapper.findAll('button').find(b => b.text().includes('推理服务'))
  expect(apiModeBtn).toBeTruthy()
  await apiModeBtn!.trigger('click')

  expect(wrapper.text()).toContain('推理服务地址')
  expect(wrapper.text()).toContain('扫描本地服务')
})

it('save emits correct payload with inference mode', async () => {
  const wrapper = createWrapper({
    transcriptionSettings: createTranscriptionSettings({
      cuda_available: true,
    }),
  })

  await openTranscriptionTab(wrapper)
  await switchToVibeVoice(wrapper)

  await findButtonByText(wrapper, '保存设置').trigger('click')

  const emitted = wrapper.emitted('updateTranscriptionSettings')
  expect(emitted).toBeTruthy()
  const payload = emitted![0][0] as Record<string, unknown>
  expect(payload.vibevoice_inference_mode).toBe('local')
  expect(payload.vibevoice_api_url).toBe('')
})

it('VibeVoice is available in API mode without CUDA', async () => {
  const wrapper = createWrapper({
    transcriptionSettings: createTranscriptionSettings({
      cuda_available: false,
      vibevoice_inference_mode: 'api',
    }),
  })

  await openTranscriptionTab(wrapper)

  const vibevoiceBtn = wrapper.findAll('button').find(b => b.text().includes('VibeVoice ASR'))
  expect(vibevoiceBtn).toBeTruthy()
  // In API mode, button should NOT be disabled
  expect(vibevoiceBtn!.element.hasAttribute('disabled')).toBe(false)
})
```

- [ ] **Step 3: Run frontend tests**

Run: `cd /home/admin/projects/ShengWen/frontend && npx vitest run src/__tests__/SettingsModal.vibevoice.test.ts`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/__tests__/SettingsModal.vibevoice.test.ts
git commit -m "test(frontend): add tests for VibeVoice inference mode UI"
```

---

## Task 9: Final Lint, Build Verification, Commit All

**Files:**
- All modified files

- [ ] **Step 1: Run full Python lint**

Run: `cd /home/admin/projects/ShengWen && ./scripts/lint.sh`

- [ ] **Step 2: Run full Python test suite**

Run: `cd /home/admin/projects/ShengWen && uv run python -m pytest src/test/python/ -v`

- [ ] **Step 3: Run frontend tests**

Run: `cd /home/admin/projects/ShengWen/frontend && npx vitest run`

- [ ] **Step 4: Verify frontend build**

Run: `cd /home/admin/projects/ShengWen/frontend && npm run build`

- [ ] **Step 5: Final commit with version doc**

Create `docs/version/feature-add-vibevoice-transcriber.md` change log entry:

```markdown
# VibeVoice 双模式推理支持

**分支**: feature/add-vibevoice-transcriber

## 变更记录

### 2026-04-09: VibeVoice 双模式推理 + 分片模型校验修复

- fix: 支持分片 safetensors/pytorch 格式的模型文件检测
- feat: 新增 VibeVoiceApiTranscriber（HTTP API 推理模式）
- feat: 新增 VibeVoiceServiceManager（vLLM 子进程管理）
- feat: 设置界面新增推理模式切换（本地加载 / 推理服务）
- feat: 新增 API 端点（服务扫描、启动、停止、状态查询）
```

```bash
git add docs/version/feature-add-vibevoice-transcriber.md
git commit -m "docs: add version change log for VibeVoice dual inference mode"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** Each section in the design spec has a corresponding task.
  - Bug fix → Task 1
  - Config changes → Task 2
  - VibeVoiceApiTranscriber → Task 3
  - VibeVoiceServiceManager → Task 4
  - Factory dispatch + API endpoints → Task 5
  - Frontend types + API client → Task 6
  - Frontend SettingsModal UI → Task 7
  - Frontend tests → Task 8
  - Final verification → Task 9
- [x] **Placeholder scan:** No TBD/TODO/placeholder patterns. All code is concrete.
- [x] **Type consistency:** Method names, field names, and types are consistent across all tasks (e.g., `vibevoice_inference_mode` is `"local" | "api"` throughout; `api_url` parameter matches across factory and transcriber).
