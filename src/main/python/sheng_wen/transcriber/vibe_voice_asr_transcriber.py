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
    _CHUNK_DURATION_SEC: float = 180.0
    _CHUNK_THRESHOLD_SEC: float = 300.0

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
            "max_new_tokens": runtime_state.get("vibevoice_max_new_tokens", 2048),
            "dtype": runtime_state.get("vibevoice_dtype", "bfloat16"),
        }

    def __init__(
        self,
        model_path: str,
        device: str = "cuda",
        max_new_tokens: int = 2048,
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

            # Detect INT4 quantized model and configure accordingly
            model_config_path = os.path.join(self.model_path, "config.json")
            model_kwargs: dict[str, Any] = {
                "torch_dtype": resolved_dtype,
                "device_map": self.device,
                "trust_remote_code": True,
                "attn_implementation": "sdpa",
            }
            if os.path.exists(model_config_path):
                import json as _json

                with open(model_config_path) as _f:
                    _cfg = _json.load(_f)
                if _cfg.get("quantization_config", {}).get("load_in_4bit"):
                    from transformers import BitsAndBytesConfig

                    quant_cfg = _cfg["quantization_config"]
                    model_kwargs["quantization_config"] = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type=quant_cfg.get("bnb_4bit_quant_type", "nf4"),
                        bnb_4bit_compute_dtype=getattr(
                            torch,
                            quant_cfg.get("bnb_4bit_compute_dtype", "bfloat16"),
                            torch.bfloat16,
                        ),
                        bnb_4bit_use_double_quant=quant_cfg.get(
                            "bnb_4bit_use_double_quant", True
                        ),
                        llm_int8_enable_fp32_cpu_offload=True,
                    )
                    # Quantized models require device_map="auto"
                    model_kwargs["device_map"] = "auto"
                    model_kwargs.pop("torch_dtype", None)
                    logger.info(
                        "[VibeVoiceAsrTranscriber] Detected INT4 quantized model, loading with BitsAndBytesConfig"
                    )

            self.model = VibeVoiceASRForConditionalGeneration.from_pretrained(
                self.model_path,
                **model_kwargs,
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

    @classmethod
    def _needs_chunking(cls, audio_duration: float) -> bool:
        return audio_duration > cls._CHUNK_THRESHOLD_SEC

    @staticmethod
    def _merge_chunk_results(
        chunk_results: list[tuple[TranscriptionResult, float]],
        model_load_time: float,
    ) -> TranscriptionResult:
        merged_segments: list[dict[str, Any]] = []
        transcription_time = 0.0
        max_end = 0.0

        for chunk_result, offset in chunk_results:
            transcription_time += chunk_result.transcription_time
            max_end = max(max_end, chunk_result.audio_duration + offset)
            for segment in chunk_result.segments:
                start = float(segment.get("start", 0.0)) + offset
                end = float(segment.get("end", 0.0)) + offset
                max_end = max(max_end, end)
                merged_segments.append(
                    {
                        "start": start,
                        "end": end,
                        "text": segment.get("text", ""),
                        "speaker_id": segment.get("speaker_id", ""),
                    }
                )

        real_time_factor = transcription_time / max_end if max_end > 0 else 0.0
        return TranscriptionResult(
            segments=merged_segments,
            transcription_time=transcription_time,
            real_time_factor=real_time_factor,
            total_time=transcription_time + model_load_time,
            model_load_time=model_load_time,
            audio_duration=max_end,
            language="unknown",
            language_probability=0.0,
        )

    def _transcribe_single_chunk(
        self,
        file_path: str,
        progress_callback: Any = None,
        cancel_check: Any = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> TranscriptionResult:
        def report_progress(progress: float):
            if progress_callback is None:
                return
            bounded_progress = min(max(progress, 0.0), 1.0)
            mapped_progress = progress_start + (
                (progress_end - progress_start) * bounded_progress
            )
            progress_callback(mapped_progress)

        if cancel_check and cancel_check():
            raise TranscriptionCancelled("任务已取消，停止转录。")

        self._ensure_loaded()

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

        report_progress(0.3)

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

        report_progress(0.7)

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

        report_progress(1.0)

        transcription_time = time.time() - transcribe_start_time
        real_time_factor = (
            transcription_time / audio_duration if audio_duration > 0 else 0.0
        )

        return TranscriptionResult(
            segments=result_segments,
            transcription_time=transcription_time,
            real_time_factor=real_time_factor,
            total_time=transcription_time,
            model_load_time=0.0,
            audio_duration=audio_duration,
            language="unknown",
            language_probability=0.0,
        )

    def _transcribe_chunked(
        self,
        file_path: str,
        progress_callback: Any = None,
        cancel_check: Any = None,
    ) -> TranscriptionResult:
        from .audio_chunker import (
            cleanup_chunks,
            get_audio_duration,
            split_audio_into_chunks,
        )

        total_start_time = time.time()
        duration = get_audio_duration(file_path)
        if duration <= 0:
            result = self._transcribe_single_chunk(
                file_path,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                progress_start=0.05,
                progress_end=1.0,
            )
            result.model_load_time = self.model_load_time
            result.total_time = result.transcription_time + self.model_load_time
            return result

        chunks = split_audio_into_chunks(file_path, self._CHUNK_DURATION_SEC)
        if len(chunks) == 1 and chunks[0][0] == file_path:
            result = self._transcribe_single_chunk(
                file_path,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                progress_start=0.05,
                progress_end=1.0,
            )
            result.model_load_time = self.model_load_time
            result.total_time = result.transcription_time + self.model_load_time
            return result

        chunk_results: list[tuple[TranscriptionResult, float]] = []

        try:
            chunk_count = len(chunks)
            for index, (chunk_path, chunk_offset) in enumerate(chunks):
                if cancel_check and cancel_check():
                    raise TranscriptionCancelled("任务已取消，停止转录。")

                progress_start = 0.05 + (index / chunk_count) * 0.90
                progress_end = 0.05 + ((index + 1) / chunk_count) * 0.90
                chunk_result = self._transcribe_single_chunk(
                    chunk_path,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                    progress_start=progress_start,
                    progress_end=progress_end,
                )
                chunk_results.append((chunk_result, chunk_offset))

                if (
                    self._torch is not None
                    and hasattr(self._torch, "cuda")
                    and hasattr(self._torch.cuda, "empty_cache")
                ):
                    self._torch.cuda.empty_cache()
        finally:
            cleanup_chunks(chunks)

        merged_result = self._merge_chunk_results(chunk_results, self.model_load_time)
        merged_result.total_time = time.time() - total_start_time
        if progress_callback:
            progress_callback(1.0)
        return merged_result

    def transcribe(
        self,
        file_path: str,
        progress_callback: Any = None,
        cancel_check: Any = None,
    ) -> TranscriptionResult:
        try:
            if cancel_check and cancel_check():
                raise TranscriptionCancelled("任务已取消，停止转录。")

            self._ensure_loaded()

            if progress_callback:
                progress_callback(0.1)

            from .audio_chunker import get_audio_duration

            duration = get_audio_duration(file_path)
            if self._needs_chunking(duration):
                return self._transcribe_chunked(
                    file_path, progress_callback, cancel_check
                )

            result = self._transcribe_single_chunk(
                file_path,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                progress_start=0.1,
                progress_end=1.0,
            )
            result.model_load_time = self.model_load_time
            result.total_time = result.transcription_time + self.model_load_time
            return result
        except TranscriptionCancelled:
            raise
        except Exception as e:
            raise TranscriptionError(f"转录文件 '{file_path}' 时发生错误: {e}") from e
