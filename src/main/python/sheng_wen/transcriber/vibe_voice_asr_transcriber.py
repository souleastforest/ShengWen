from __future__ import annotations

import gc
import math
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
        if int(max_new_tokens) < 2048:
            logger.warning(
                f"[VibeVoiceAsrTranscriber] max_new_tokens={max_new_tokens} 过小，"
                "语音 token（约 7.5/s）+ 文本 JSON 开销下必然截断，转录将显式失败"
            )
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
    def _parse_timestamp(timestamp_str: str | float | int | None) -> float:
        if timestamp_str is None or timestamp_str == "":
            return 0.0

        if isinstance(timestamp_str, (int, float)):
            return float(timestamp_str)

        if not isinstance(timestamp_str, str):
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

    @staticmethod
    def _needs_chunking(audio_duration: float, threshold: float = 600.0) -> bool:
        return float(audio_duration or 0.0) >= threshold

    @staticmethod
    def _merge_chunk_results(
        chunk_results: list[tuple[TranscriptionResult, float]],
        model_load_time: float = 0.0,
    ) -> TranscriptionResult:
        merged_segments: list[dict[str, Any]] = []
        transcription_time = 0.0
        max_end = 0.0
        for result, offset in chunk_results:
            transcription_time += float(result.transcription_time or 0.0)
            max_end = max(max_end, float(result.audio_duration or 0.0) + offset)
            for segment in result.segments or []:
                item = dict(segment)
                item["start"] = float(item.get("start", 0.0) or 0.0) + offset
                item["end"] = max(
                    item["start"],
                    float(item.get("end", item["start"]) or 0.0) + offset,
                )
                merged_segments.append(item)
        merged_segments.sort(key=lambda item: (item["start"], item["end"]))
        return TranscriptionResult(
            segments=merged_segments,
            transcription_time=transcription_time,
            real_time_factor=(transcription_time / max_end if max_end > 0.0 else 0.0),
            total_time=transcription_time + float(model_load_time or 0.0),
            model_load_time=float(model_load_time or 0.0),
            audio_duration=max_end,
            language="unknown",
            language_probability=0.0,
        )

    @staticmethod
    def _clear_cuda_cache() -> None:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                ipc_collect = getattr(torch.cuda, "ipc_collect", None)
                if ipc_collect:
                    ipc_collect()
        except Exception as exc:
            logger.debug(f"[VibeVoiceAsrTranscriber] CUDA 缓存清理失败: {exc}")

    def release_inference_resources(self) -> None:
        """释放一次推理产生的临时张量，但保留已加载模型供下一片复用。"""
        gc.collect()
        self._clear_cuda_cache()

    def reset_model(self) -> None:
        """在 OOM 后彻底释放模型，下一次推理时懒加载。"""
        self.model = None
        self.processor = None
        self.model_load_time = 0.0
        self.release_inference_resources()
        logger.warning("[VibeVoiceAsrTranscriber] OOM 后已释放模型，下一片将重新加载。")

    def _generation_budget(self, audio_duration: float) -> int:
        # 预算 = 语音 token + 文本 JSON 分段开销（事故修复 vibevoice-empty-transcript）：
        # 语音 token 率 ≈ 7.5 tokens/s（24000Hz / speech_tok_compress_ratio=3200）；
        # 文本为每段时间戳+字段名的 JSON 输出，实测开销约为语音的 0.5-1 倍。
        # 旧公式仅按 8 tokens/s 封顶 → 长音频输出在 JSON 中途被硬截断 →
        # 库解析失败静默返回空 → 空转录 COMPLETED（生产事故 89e565dd）。
        speech_budget = int(math.ceil(max(0.0, audio_duration) * 7.5))
        text_budget = max(1536, int(math.ceil(max(0.0, audio_duration) * 5.0)))
        budget = max(2048, speech_budget + text_budget)
        return min(max(1, int(self.max_new_tokens)), budget)

    def transcribe(
        self,
        file_path: str,
        progress_callback: Any = None,
        cancel_check: Any = None,
    ) -> TranscriptionResult:
        total_start_time = time.time()
        inputs = None
        output_ids = None
        generated_ids = None

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
            speech_tensors = inputs.get("speech_tensors")
            audio_duration = (
                float(speech_tensors.shape[-1]) / 24000.0
                if speech_tensors is not None and hasattr(speech_tensors, "shape")
                else 0.0
            )
            effective_max_new_tokens = self._generation_budget(audio_duration)
            logger.info(
                "[VibeVoiceAsrTranscriber] generation budget: "
                f"audio={audio_duration:.1f}s max_new_tokens={effective_max_new_tokens} "
                f"global_max={self.max_new_tokens}"
            )
            # 截断时自动放大预算重试（do_sample=False 下同一前缀确定性续写可补全
            # 被截断的 JSON 输出）；达到全局上限仍截断则显式失败——禁止静默返回
            # 空转录（生产事故 vibevoice-empty-transcript：截断→JSON 解析失败→空 COMPLETED）。
            while True:
                generation_config = {
                    "max_new_tokens": effective_max_new_tokens,
                    "do_sample": False,
                    "pad_token_id": self.processor.pad_id,
                    "eos_token_id": self.processor.tokenizer.eos_token_id,
                }

                with torch.inference_mode():
                    output_ids = self.model.generate(
                        **inputs,
                        **generation_config,
                    )

                generated_ids = self._extract_generated_ids(output_ids)
                generated_ids = generated_ids[0, input_length:]
                if generated_ids.shape[0] < effective_max_new_tokens:
                    break

                logger.warning(
                    "[VibeVoiceAsrTranscriber] 输出达到 max_new_tokens，可能发生截断: "
                    f"{effective_max_new_tokens}"
                )
                if effective_max_new_tokens >= self.max_new_tokens:
                    raise TranscriptionError(
                        f"输出在 max_new_tokens={self.max_new_tokens} 下仍被截断，转录不完整"
                    )
                if cancel_check and cancel_check():
                    raise TranscriptionCancelled("任务已取消，停止转录。")
                effective_max_new_tokens = min(
                    self.max_new_tokens, effective_max_new_tokens * 2
                )
                logger.warning(
                    "[VibeVoiceAsrTranscriber] 已放大预算重试: "
                    f"{effective_max_new_tokens}"
                )
                if progress_callback:
                    progress_callback(0.5)

            if progress_callback:
                progress_callback(0.7)

            if cancel_check and cancel_check():
                raise TranscriptionCancelled("任务已取消，停止转录。")

            text = self.processor.decode(generated_ids, skip_special_tokens=True)
            raw_segments = self.processor.post_process_transcription(text)

            result_segments = []
            transcribed_duration = 0.0

            for segment in raw_segments:
                start = self._parse_timestamp(segment.get("start_time", "0:00.000"))
                end = self._parse_timestamp(segment.get("end_time", "0:00.000"))
                transcribed_duration = max(transcribed_duration, end)

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
                transcription_time / transcribed_duration
                if transcribed_duration > 0
                else 0.0
            )

            return TranscriptionResult(
                segments=result_segments,
                transcription_time=transcription_time,
                real_time_factor=real_time_factor,
                total_time=total_time,
                model_load_time=self.model_load_time,
                audio_duration=transcribed_duration,
                language="unknown",
                language_probability=0.0,
            )
        except TranscriptionCancelled:
            raise
        except Exception as e:
            raise TranscriptionError(f"转录文件 '{file_path}' 时发生错误: {e}") from e
        finally:
            inputs = None
            output_ids = None
            generated_ids = None
            self.release_inference_resources()
