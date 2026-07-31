from __future__ import annotations

import time
from typing import Any

import torch
from loguru import logger

from vibevoice.modular.modeling_vibevoice_asr import (
    VibeVoiceASRForConditionalGeneration,
)
from vibevoice.processor.vibevoice_asr_processor import VibeVoiceASRProcessor

from .transcriber import (
    ModelLoadError,
    Transcriber,
    TranscriptionCancelled,
    TranscriptionError,
    TranscriptionResult,
)


class VibeVoiceAsrTranscriber(Transcriber):
    """基于 VibeVoice-ASR 的转录器实现。"""

    REQUIRED_FILES = ("config.json", "model.safetensors|pytorch_model.bin")

    @classmethod
    def validate_model_path(cls, path: str):
        from .transcriber import ModelPathValidationResult
        from .vibevoice_model_validator import validate_vibevoice_model_path

        result = validate_vibevoice_model_path(path)
        return ModelPathValidationResult(
            result.valid, result.message, result.resolved_path, result.missing_files
        )

    @classmethod
    def required_model_files(cls) -> list[str]:
        return list(cls.REQUIRED_FILES)

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
        language_model_pretrained_name: str = "Qwen/Qwen2.5-7B",
        max_new_tokens: int = 8192,
        dtype: str = "bfloat16",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.model_path = model_path
        self.device = device
        self.language_model_pretrained_name = language_model_pretrained_name
        self.max_new_tokens = max_new_tokens
        self.dtype = dtype

        self.processor: VibeVoiceASRProcessor | None = None
        self.model: VibeVoiceASRForConditionalGeneration | None = None
        self.model_load_time = 0.0

    def _ensure_loaded(self):
        if self.processor is not None and self.model is not None:
            return

        if not str(self.device).startswith("cuda"):
            raise ModelLoadError("VibeVoice-ASR requires CUDA and bfloat16 inference.")

        start_time = time.time()
        try:
            torch_dtype = getattr(torch, self.dtype, None)
            if torch_dtype is None:
                raise ValueError(f"不支持的数据类型: {self.dtype}")
            logger.info(
                f"[VibeVoiceAsrTranscriber] Loading model from {self.model_path} "
                f"(device={self.device}, dtype={self.dtype}, "
                f"language_model={self.language_model_pretrained_name})"
            )
            self.processor = VibeVoiceASRProcessor.from_pretrained(
                self.model_path,
                language_model_pretrained_name=self.language_model_pretrained_name,
            )
            self.model = VibeVoiceASRForConditionalGeneration.from_pretrained(
                self.model_path,
                dtype=torch_dtype,
                trust_remote_code=True,
            )
            self.model.to(self.device)
            self.model.eval()
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
            parts = timestamp_str.strip().split(":")
            if len(parts) == 3:
                hours = float(parts[0])
                minutes = float(parts[1])
                seconds = float(parts[2])
                return hours * 3600.0 + minutes * 60.0 + seconds

            if len(parts) == 2:
                minutes = float(parts[0])
                seconds = float(parts[1])
                return minutes * 60.0 + seconds
        except (TypeError, ValueError):
            return 0.0

        return 0.0

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
            return output_ids.sequences
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

            input_length = inputs["input_ids"].shape[1]
            generation_config = {
                "max_new_tokens": self.max_new_tokens,
                "do_sample": False,
                "pad_token_id": self.processor.pad_id,
                "eos_token_id": self.processor.tokenizer.eos_token_id,
            }

            with torch.inference_mode():
                output_ids = self.model.generate(
                    **inputs,
                    **generation_config,
                )

            if progress_callback:
                progress_callback(0.7)

            if cancel_check and cancel_check():
                raise TranscriptionCancelled("任务已取消，停止转录。")

            generated_ids = self._extract_generated_ids(output_ids)
            generated_ids = generated_ids[0, input_length:]
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
