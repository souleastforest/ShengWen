from __future__ import annotations

import os
import time
import logging
from typing import TYPE_CHECKING, Any

try:
    from loguru import logger
except ImportError:
    logger = logging.getLogger(__name__)

from .transcriber import (
    ModelLoadError,
    Transcriber,
    TranscriptionCancelled,
    TranscriptionError,
    TranscriptionResult,
)
from .vibevoice_model_validator import (
    perform_lightweight_load_test,
)

if TYPE_CHECKING:
    from vibevoice.modular.modeling_vibevoice_asr import (
        VibeVoiceASRForConditionalGeneration,
    )
    from vibevoice.processor.vibevoice_asr_processor import VibeVoiceASRProcessor


class VibeVoiceAsrTranscriber(Transcriber):
    """基于 VibeVoice-ASR 的转录器实现。"""

    transcriber_name = "vibe_voice_asr"

    @classmethod
    def validate_model_path(cls, path: str):
        from .transcriber import ModelPathValidationResult

        result = perform_lightweight_load_test(path)
        return ModelPathValidationResult(
            valid=result.valid,
            message=result.message,
            resolved_path=result.resolved_path,
            missing_files=result.missing_files,
        )

    @classmethod
    def required_model_files(cls) -> list[str]:
        return ["config.json"]

    @classmethod
    def build_runtime_kwargs(cls, runtime_state: dict) -> dict:
        return {
            "model_path": runtime_state.get("model_path", ""),
            "device": runtime_state.get("device", "cuda"),
            "language_model_pretrained_name": runtime_state.get(
                "vibevoice_language_model", "Qwen/Qwen2.5-7B"
            ),
            "max_new_tokens": runtime_state.get("vibevoice_max_new_tokens", 8192),
            "dtype": runtime_state.get("vibevoice_dtype", "bfloat16"),
        }

    def __init__(
        self,
        model_path: str,
        device: str = "cuda",
        max_new_tokens: int = 8192,
        language_model_pretrained_name: str = "Qwen/Qwen2.5-7B",
        dtype: Any = "bfloat16",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.model_path = model_path
        self.device = device
        self.max_new_tokens = max_new_tokens
        self.language_model_pretrained_name = language_model_pretrained_name
        self.dtype = dtype

        self.processor: VibeVoiceASRProcessor | None = None
        self.model: VibeVoiceASRForConditionalGeneration | None = None
        self._torch: Any | None = None
        self.model_load_time = 0.0

    def _ensure_loaded(self):
        if self.processor is not None and self.model is not None:
            return

        try:
            import torch
            from vibevoice.modular.modeling_vibevoice_asr import (
                VibeVoiceASRForConditionalGeneration,
            )
            from vibevoice.processor.vibevoice_asr_processor import (
                VibeVoiceASRProcessor,
            )
        except ImportError as e:
            raise ModelLoadError(
                "加载 VibeVoice-ASR 依赖失败，请确认已安装 `torch` 和 `vibevoice`。"
                f" 原始错误: {e}"
            ) from e

        if not str(self.device).startswith("cuda"):
            raise ModelLoadError("VibeVoice-ASR requires CUDA and bfloat16 inference.")

        self._torch = torch
        resolved_dtype = self._resolve_dtype(torch)

        # Validate model path before attempting full load
        validation = perform_lightweight_load_test(self.model_path)
        if not validation.valid:
            logger.error(
                f"[VibeVoiceAsrTranscriber] Model validation failed: {validation.message}"
            )
            raise ModelLoadError(
                f"模型路径验证失败: {validation.message}\n"
                f"路径: {validation.resolved_path}"
            )

        start_time = time.time()
        try:
            logger.info(
                f"[VibeVoiceAsrTranscriber] Loading model from {self.model_path} "
                f"(device={self.device}, dtype={self.dtype})"
            )
            # Force offline mode for HuggingFace Hub to avoid spurious 404 errors
            # (e.g. Qwen2.5-7B missing `additional_chat_templates` in remote repo).
            # Must patch both huggingface_hub.constants and transformers.utils.hub
            # since both cache the offline flag at import time.
            import huggingface_hub.constants as _hf_const
            import transformers.utils.hub as _tf_hub

            prev_env = os.environ.get("HF_HUB_OFFLINE")
            prev_hf_const = getattr(_hf_const, "HF_HUB_OFFLINE", None)
            prev_tf_offline = getattr(_tf_hub, "_is_offline_mode", None)
            os.environ["HF_HUB_OFFLINE"] = "1"
            _hf_const.HF_HUB_OFFLINE = True
            _tf_hub._is_offline_mode = True
            try:
                self.processor = VibeVoiceASRProcessor.from_pretrained(
                    self.model_path,
                    language_model_pretrained_name=self.language_model_pretrained_name,
                )
            finally:
                if prev_env is None:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    os.environ["HF_HUB_OFFLINE"] = prev_env
                if prev_hf_const is not None:
                    _hf_const.HF_HUB_OFFLINE = prev_hf_const
                if prev_tf_offline is not None:
                    _tf_hub._is_offline_mode = prev_tf_offline

            self.model = VibeVoiceASRForConditionalGeneration.from_pretrained(
                self.model_path,
                torch_dtype=resolved_dtype,
                device_map=self.device,
                trust_remote_code=True,
            )
            self.model_load_time = time.time() - start_time
            logger.info(
                f"[VibeVoiceAsrTranscriber] Model loaded in {self.model_load_time:.2f}s"
            )
        except Exception as e:
            raise ModelLoadError(f"加载 VibeVoice-ASR 模型失败: {e}") from e

    @staticmethod
    def _parse_timestamp(timestamp_str: str) -> float:
        if not timestamp_str:
            return 0.0

        try:
            # Model may output numeric timestamps instead of strings
            if isinstance(timestamp_str, (int, float)):
                return float(timestamp_str)
            parts = str(timestamp_str).strip().split(":")
            if len(parts) == 3:
                hours = float(parts[0])
                minutes = float(parts[1])
                seconds = float(parts[2])
                return hours * 3600.0 + minutes * 60.0 + seconds

            if len(parts) == 2:
                minutes = float(parts[0])
                seconds = float(parts[1])
                return minutes * 60.0 + seconds

            return 0.0
        except (ValueError, TypeError):
            return 0.0

    def _resolve_dtype(self, torch_module: Any):
        if not isinstance(self.dtype, str):
            return self.dtype

        requested_name = self.dtype.strip().lower()
        if not requested_name:
            requested_name = "bfloat16"

        if hasattr(torch_module, requested_name):
            return getattr(torch_module, requested_name)

        raise ModelLoadError(
            f"不支持的 VibeVoice dtype: {self.dtype}. 可选值取决于当前 torch 安装。"
        )

    @staticmethod
    def _move_inputs_to_device(inputs: Any, device: str):
        if hasattr(inputs, "to"):
            try:
                return inputs.to(device)
            except TypeError:
                pass

        if isinstance(inputs, dict):
            moved = {}
            for key, value in inputs.items():
                if hasattr(value, "to"):
                    moved[key] = value.to(device)
                else:
                    moved[key] = value
            return moved

        return inputs

    @staticmethod
    def _extract_generated_ids(output_ids: Any):
        if hasattr(output_ids, "sequences"):
            output_ids = output_ids.sequences
        # model.generate() returns shape (batch_size, seq_len); take first batch
        if hasattr(output_ids, "shape") and len(output_ids.shape) == 2:
            output_ids = output_ids[0]
        return output_ids

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

            self._ensure_loaded()

            if progress_callback:
                progress_callback(0.1)

            if cancel_check and cancel_check():
                raise TranscriptionCancelled("任务已取消，停止转录。")

            transcribe_start_time = time.time()
            logger.info(f"[VibeVoiceAsrTranscriber] Start transcribing: {file_path}")

            inputs = self.processor(
                audio=file_path,
                return_tensors="pt",
                padding=True,
                add_generation_prompt=True,
            )
            inputs = self._move_inputs_to_device(inputs, self.device)

            if progress_callback:
                progress_callback(0.3)

            if cancel_check and cancel_check():
                raise TranscriptionCancelled("任务已取消，停止转录。")

            if self._torch is None:
                raise ModelLoadError("VibeVoice-ASR 运行时依赖尚未初始化。")

            with self._torch.inference_mode():
                output_ids = self.model.generate(
                    **inputs,
                    max_new_tokens=self.max_new_tokens,
                    temperature=0.0,
                )

            if progress_callback:
                progress_callback(0.7)

            if cancel_check and cancel_check():
                raise TranscriptionCancelled("任务已取消，停止转录。")

            generated_ids = self._extract_generated_ids(output_ids)
            text = self.processor.decode(generated_ids, skip_special_tokens=True)
            raw_segments = self.processor.post_process_transcription(text)

            result_segments = []
            audio_duration = 0.0

            for segment in raw_segments:
                start = self._parse_timestamp(segment.get("start_time", "0:00.000"))
                end = self._parse_timestamp(segment.get("end_time", "0:00.000"))
                audio_duration = max(audio_duration, end)

                result_segments.append(
                    {
                        "start": start,
                        "end": end,
                        "text": segment.get("text", "").strip(),
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

            return TranscriptionResult(
                segments=result_segments,
                transcription_time=transcription_time,
                real_time_factor=real_time_factor,
                total_time=total_time,
                model_load_time=self.model_load_time,
                audio_duration=audio_duration,
                language="unknown",
                language_probability=0.0,
            )
        except TranscriptionCancelled:
            raise
        except Exception as e:
            raise TranscriptionError(f"转录文件 '{file_path}' 时发生错误: {e}") from e
