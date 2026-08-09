import os
import re
import ffmpeg
import time
from threading import Lock, Thread
from collections import deque
from typing import Any, Dict, TYPE_CHECKING

from loguru import logger

from ..worker import Worker, TaskCancelledError
from .transcriber import Transcriber, TranscriptionResult, TranscriptionCancelled
from ..api import notify_task_update
from ..utils.ffmpeg_helper import FFmpegHelper
from ..config import config
from .audio_chunker import cleanup_chunks, get_audio_duration, split_audio_into_chunks

# 使用 TYPE_CHECKING 来避免运行时循环导入
if TYPE_CHECKING:
    from ..llm.llm_worker import LLMWorker

_CUDA_DLL_PATTERN = re.compile(
    r"(cublas(?:Lt)?64_(\d+)\.dll|cudart64_(\d+)\.dll|cudnn64(?:_\d+)?\.dll)",
    re.IGNORECASE,
)


def _extract_missing_cuda_runtime_dll(error_text: str) -> tuple[str | None, str | None]:
    match = _CUDA_DLL_PATTERN.search(error_text or "")
    if not match:
        return (None, None)
    dll_name = match.group(1)
    cuda_major = match.group(2) or match.group(3)
    return (dll_name, cuda_major)


def _build_actionable_transcription_error(error_text: str) -> str:
    text = str(error_text or "").strip()
    if not text:
        return "转录失败（无错误详情）。"

    lowered = text.lower()
    missing_dll, cuda_major = _extract_missing_cuda_runtime_dll(text)
    maybe_cuda_runtime_error = bool(missing_dll) or "cuda" in lowered or "cublas" in lowered
    if not maybe_cuda_runtime_error:
        return text

    lines = [text, "", "CUDA 修复建议："]
    lines.append("1) 先切换到 CPU 转录，保证任务可继续。")
    if missing_dll:
        if cuda_major:
            lines.append(f"2) 当前缺失 {missing_dll}（对应 CUDA {cuda_major} 运行库），请安装对应 CUDA Runtime。")
        else:
            lines.append(f"2) 当前缺失 {missing_dll}，请安装对应 CUDA Runtime。")
    else:
        lines.append("2) 检查 CUDA Runtime 与显卡驱动是否安装完整。")
    lines.extend(
        [
            "3) 确认 PATH 包含 CUDA bin 目录（例如 C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12.x\\bin）。",
            "4) 重启 ShengWen 后重新选择 CUDA。",
        ]
    )
    return "\n".join(lines)


def _format_duration(seconds: float) -> str:
    """将秒数格式化为 HHMMSS 格式的字符串。"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours:02d}{minutes:02d}{seconds:02d}"


class TranscriberWorker(Worker):
    """
    一个工作单元，可以从视频中提取音频，然后转录音频文件，
    并将详细的转录结果保存到中间文件，最后将文件路径传递给下一个工作单元。
    """
    def __init__(self, name: str, transcriber: Transcriber, next_worker: 'LLMWorker'):
        """
        初始化 TranscriberWorker。
        """
        super().__init__(name)
        self._transcriber = transcriber
        self._transcriber_lock = Lock()
        self._next_worker = next_worker

    def update_transcriber(self, transcriber: Transcriber):
        """在运行时替换转录器实例。"""
        with self._transcriber_lock:
            self._transcriber = transcriber
        logger.info(f"[{self.name}] 转录器实例已更新。")

    def _extract_audio(self, video_path: str, audio_path: str, task_id: str | None = None) -> bool:
        """
        使用 ffmpeg 从视频文件中提取音频。

        返回:
            如果提取成功，则返回 True，否则返回 False。
        """
        # 使用 FFmpegHelper 配置 ffmpeg 路径
        if not FFmpegHelper.configure_ffmpeg_python():
            logger.error(f"[{self.name}] 错误: FFmpeg 配置失败。")
            logger.error(f"[{self.name}] 请确保已安装 imageio-ffmpeg: pip install imageio-ffmpeg")
            return False

        # 若目标音频已存在且不早于视频文件，优先复用，避免重复提取。
        try:
            if os.path.exists(audio_path):
                audio_size = os.path.getsize(audio_path)
                if audio_size > 0:
                    audio_mtime = os.path.getmtime(audio_path)
                    video_mtime = os.path.getmtime(video_path) if os.path.exists(video_path) else 0.0
                    if audio_mtime >= video_mtime:
                        logger.info(
                            f"[{self.name}] 复用已存在音频文件，跳过提取: {audio_path} "
                            f"(size={audio_size / (1024 * 1024):.1f}MB)"
                        )
                        return True
        except Exception as e:
            logger.debug(f"[{self.name}] 复用音频文件检查失败，回退常规提取: {e}")

        logger.info(f"[{self.name}] 正在从视频 '{video_path}' 中提取音频到 '{audio_path}'...")
        try:
            process = (
                ffmpeg
                .input(video_path)
                .output(audio_path, acodec='mp3', audio_bitrate='192k')
                .overwrite_output()
                .run_async(pipe_stdout=False, pipe_stderr=True)
            )

            stderr_tail: deque[str] = deque(maxlen=120)

            def _drain_stderr():
                if not process.stderr:
                    return
                try:
                    while True:
                        raw = process.stderr.readline()
                        if not raw:
                            break
                        try:
                            line = raw.decode(errors="replace").strip()
                        except Exception:
                            line = str(raw).strip()
                        if line:
                            stderr_tail.append(line)
                except Exception:
                    # 排障信息读取失败不应影响主流程
                    pass

            stderr_thread = Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            heartbeat_start = time.time()
            next_heartbeat_ts = heartbeat_start + 10.0
            while process.poll() is None:
                if task_id and self.is_task_cancelled(task_id):
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except Exception:
                        process.kill()
                    raise TaskCancelledError("任务已取消，停止音频提取。")

                now = time.time()
                if now >= next_heartbeat_ts:
                    next_heartbeat_ts = now + 10.0
                    audio_size_mb = 0.0
                    try:
                        if os.path.exists(audio_path):
                            audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
                    except Exception:
                        pass
                    logger.info(
                        f"[{self.name}] 音频提取进行中: elapsed={int(now - heartbeat_start)}s, "
                        f"audio_size={audio_size_mb:.1f}MB, task={task_id or '<unknown>'}"
                    )
                # 同步 worker 线程里轮询取消信号，尽量快速中断。
                time.sleep(0.2)

            if stderr_thread.is_alive():
                stderr_thread.join(timeout=0.5)

            if process.returncode != 0:
                stderr_text = "\n".join(stderr_tail).strip()
                if not stderr_text:
                    stderr_text = f"ffmpeg exited with code {process.returncode}"
                raise ffmpeg.Error("ffmpeg", b"", stderr_text.encode("utf-8", errors="replace"))

            logger.info(f"[{self.name}] 音频提取成功。")
            return True
        except ffmpeg.Error as e:
            stderr_text = ""
            if getattr(e, "stderr", None):
                try:
                    stderr_text = e.stderr.decode(errors="replace")
                except Exception:
                    stderr_text = str(e.stderr)
            logger.error(f"[{self.name}] 使用 ffmpeg 提取音频时出错: {stderr_text}")
            return False
        except TaskCancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.name}] 提取音频时发生未知错误: {e}", exc_info=True)
            return False

    def _save_transcription_to_file(self, result: TranscriptionResult, output_path: str):
        """
        将转录结果以特定格式保存到文件。
        """
        logger.info(f"[{self.name}] 正在将转录结果保存到: {output_path}")
        try:
            with open(output_path, "w", encoding="utf-8", errors='replace') as f:
                for seg in result.segments:
                    clean_text = seg['text'].strip()
                    line = f"{_format_duration(seg['start'])}{clean_text}\n"
                    f.write(line)
            logger.info(f"[{self.name}] 成功保存转录文件。")
        except Exception as e:
            logger.error(f"[{self.name}] 保存转录文件时出错: {e}", exc_info=True)

    @staticmethod
    def _is_cuda_oom(error: BaseException) -> bool:
        text = str(error or "").lower()
        if "out of memory" not in text:
            return False
        return "cuda" in text or "cublas" in text or "torch" in text

    @staticmethod
    def _clear_cuda_cache() -> None:
        try:
            import torch
        except Exception:
            return
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                ipc_collect = getattr(torch.cuda, "ipc_collect", None)
                if ipc_collect:
                    ipc_collect()
        except Exception as exc:
            logger.debug(f"清理 CUDA 缓存失败: {exc}")

    @staticmethod
    def _merge_chunk_results(
        results: list[tuple[TranscriptionResult, float]],
        audio_duration: float,
    ) -> TranscriptionResult:
        segments: list[dict[str, Any]] = []
        transcription_time = 0.0
        model_load_time = 0.0
        total_time = 0.0
        language = ""
        language_probability = 0.0

        for index, (result, offset) in enumerate(results):
            if index == 0:
                model_load_time = float(result.model_load_time or 0.0)
                language = result.language or ""
                language_probability = float(result.language_probability or 0.0)
            transcription_time += float(result.transcription_time or 0.0)
            total_time += float(result.total_time or 0.0)
            for segment in result.segments or []:
                adjusted = dict(segment)
                adjusted["start"] = max(0.0, float(segment.get("start", 0.0) or 0.0) + offset)
                adjusted["end"] = max(
                    adjusted["start"],
                    float(segment.get("end", segment.get("start", 0.0)) or 0.0) + offset,
                )
                segments.append(adjusted)

        segments.sort(key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0))))
        duration = max(0.0, float(audio_duration or 0.0))
        if duration <= 0.0:
            duration = sum(float(result.audio_duration or 0.0) for result, _ in results)
        real_time_factor = transcription_time / duration if duration > 0 else 0.0
        return TranscriptionResult(
            segments=segments,
            transcription_time=transcription_time,
            real_time_factor=real_time_factor,
            total_time=total_time,
            model_load_time=model_load_time,
            audio_duration=duration,
            language=language,
            language_probability=language_probability,
        )

    @staticmethod
    def _release_transcriber_resources(transcriber: Transcriber, reset_model: bool = False) -> None:
        try:
            if reset_model and hasattr(transcriber, "reset_model"):
                transcriber.reset_model()
            elif hasattr(transcriber, "release_inference_resources"):
                transcriber.release_inference_resources()
            else:
                TranscriberWorker._clear_cuda_cache()
        except Exception as exc:
            logger.debug(f"[TranscriberWorker] 释放转录资源失败: {exc}")

    @staticmethod
    def _log_cuda_memory(stage: str) -> None:
        try:
            import torch

            if torch.cuda.is_available():
                allocated = torch.cuda.memory_allocated() / (1024**3)
                reserved = torch.cuda.memory_reserved() / (1024**3)
                logger.info(
                    f"[TranscriberWorker] CUDA memory {stage}: "
                    f"allocated={allocated:.2f}GiB reserved={reserved:.2f}GiB"
                )
        except Exception:
            return

    def _transcribe_audio_with_chunking(
        self,
        audio_file: str,
        progress_callback,
        cancel_check,
    ) -> TranscriptionResult:
        whisper_config = config.whisper
        audio_duration = get_audio_duration(audio_file)
        threshold = float(whisper_config.asr_chunk_threshold_sec)
        chunk_duration = float(whisper_config.asr_chunk_duration_sec)
        fallback_chunk_duration = float(
            getattr(
                whisper_config,
                "asr_chunk_fallback_duration_sec",
                max(30.0, chunk_duration / 2.0),
            )
        )

        with self._transcriber_lock:
            transcriber = self._transcriber

        if audio_duration <= 0.0:
            raise RuntimeError(
                f"无法探测音频时长，已停止整段 CUDA 推理: {audio_file}"
            )

        def transcribe_one(path: str, callback):
            if cancel_check():
                raise TaskCancelledError("任务已取消，停止转录。")
            return transcriber.transcribe(
                path,
                progress_callback=callback,
                cancel_check=cancel_check,
            )

        def run_chunks(duration: float, current_chunk_duration: float, reason: str):
            chunks = split_audio_into_chunks(audio_file, current_chunk_duration)
            source_path = os.path.abspath(audio_file)
            if len(chunks) == 1 and os.path.abspath(chunks[0][0]) == source_path:
                raise RuntimeError(
                    f"无法为 {duration:.1f} 秒音频创建 {current_chunk_duration:.0f} 秒分片"
                )

            logger.info(
                f"[{self.name}] 启用 ASR 分片: duration={duration:.1f}s, "
                f"chunk_duration={current_chunk_duration:.1f}s, "
                f"chunks={len(chunks)}, reason={reason}"
            )
            results: list[tuple[TranscriptionResult, float]] = []
            try:
                for index, (chunk_path, offset) in enumerate(chunks):
                    expected_duration = min(
                        current_chunk_duration, max(0.0, duration - offset)
                    )
                    if expected_duration <= 0:
                        continue
                    self._release_transcriber_resources(transcriber)
                    self._log_cuda_memory(
                        f"before chunk {index + 1}/{len(chunks)}"
                    )
                    logger.info(
                        f"[{self.name}] 开始 ASR 分片 {index + 1}/{len(chunks)}: "
                        f"{offset:.1f}s-{offset + expected_duration:.1f}s"
                    )

                    def chunk_progress(
                        progress: float,
                        start=offset,
                        span=expected_duration,
                    ):
                        clamped = max(0.0, min(float(progress), 1.0))
                        overall = (
                            (start + clamped * span) / duration
                            if duration > 0
                            else clamped
                        )
                        progress_callback(max(0.0, min(overall, 1.0)))

                    try:
                        result = transcribe_one(chunk_path, chunk_progress)
                    except (TaskCancelledError, TranscriptionCancelled):
                        raise
                    except Exception as exc:
                        end = offset + expected_duration
                        raise RuntimeError(
                            f"ASR 分片 {index + 1}/{len(chunks)} "
                            f"({offset:.1f}s-{end:.1f}s) 失败: {exc}"
                        ) from exc
                    results.append((result, offset))
                    completed = min(duration, offset + expected_duration)
                    progress_callback(completed / duration if duration > 0 else 1.0)
                    self._release_transcriber_resources(transcriber)
                    self._log_cuda_memory(
                        f"after chunk {index + 1}/{len(chunks)}"
                    )
                    logger.info(
                        f"[{self.name}] ASR 分片完成 {index + 1}/{len(chunks)}: "
                        f"elapsed={result.transcription_time:.2f}s"
                    )
                if not results:
                    raise RuntimeError("ASR 分片未产生任何结果")
                return self._merge_chunk_results(results, duration)
            finally:
                cleanup_chunks(chunks)
                self._release_transcriber_resources(transcriber)

        def run_with_adaptive_chunks(duration: float, reason: str):
            try:
                return run_chunks(duration, chunk_duration, reason)
            except Exception as exc:
                is_oom = self._is_cuda_oom(exc)
                if not is_oom:
                    raise
                error_text = str(exc)
                del exc
                logger.warning(
                    f"[{self.name}] {reason} 的 6 分钟分片发生 CUDA OOM，"
                    f"释放模型并降级到 {fallback_chunk_duration:.0f} 秒分片: {error_text}"
                )
                self._release_transcriber_resources(transcriber, reset_model=True)
                return run_chunks(
                    duration,
                    fallback_chunk_duration,
                    f"{reason}; 6分钟分片CUDA OOM",
                )

        if audio_duration >= threshold:
            return run_with_adaptive_chunks(audio_duration, "超过长音频阈值")

        try:
            return transcribe_one(audio_file, progress_callback)
        except Exception as exc:
            is_oom = self._is_cuda_oom(exc)
            if not whisper_config.asr_chunk_oom_fallback or not is_oom:
                raise
            error_text = str(exc)
            del exc

        logger.warning(
            f"[{self.name}] 整段 ASR 发生 CUDA OOM，释放模型后切换到 "
            f"{chunk_duration:.0f} 秒分片重试: {error_text}"
        )
        self._release_transcriber_resources(transcriber, reset_model=True)
        return run_with_adaptive_chunks(audio_duration, "整段推理 CUDA OOM")

    def process_task(self, payload: Dict[str, Any]):
        """
        处理一个转录任务。
        如果提供了 'video_file'，则先提取音频。
        然后，转录 'audio_file' 并将结果传递下去。
        """
        video_file = payload.get("video_file")
        audio_file = payload.get("audio_file")
        output_file = payload.get("output_file")
        task_id = payload.get("task_id")
        multipart_part = payload.get("multipart_part")

        if not audio_file or not output_file:
            error_msg = "payload 中缺少 'audio_file' 或 'output_file'"
            logger.error(f"[{self.name}] 错误: {error_msg}")
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.FAILED, "error_message": error_msg}))
            return

        # 如果提供了视频文件，则先提取音频
        if video_file:
            try:
                if not self._extract_audio(video_file, audio_file, task_id=task_id):
                    # 如果提取失败，则终止该任务
                    if task_id:
                        from ..db import TaskStatus
                        from ..task_updater import update_and_notify
                        self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.FAILED, "error_message": "音频提取失败"}))
                    return
            except TaskCancelledError:
                logger.info(f"[{self.name}] 任务已取消，停止音频提取: {task_id}")
                return
        
        # 检查音频文件是否存在
        if not os.path.exists(audio_file):
            error_msg = f"找不到要转录的音频文件: {audio_file}"
            logger.error(f"[{self.name}] 错误: {error_msg}")
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.FAILED, "error_message": error_msg}))
            return

        try:
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                self._submit_coro(update_and_notify(task_id, {"status": TaskStatus.TRANSCRIBING}))
                self._submit_coro(notify_task_update(task_id))

            logger.info(f"[{self.name}] 开始转录: {audio_file}")
            logger.info(f"[{self.name}] 已注册转录进度回调，等待进度上报: task={task_id or '<unknown>'}")

            last_progress_percent = -1
            last_logged_bucket = -1

            def cancel_check() -> bool:
                return self.is_task_cancelled(task_id)

            def progress_callback(progress: float):
                nonlocal last_progress_percent, last_logged_bucket
                if cancel_check():
                    raise TaskCancelledError("任务已取消，停止转录。")
                if task_id:
                    # 将进度转换为百分比整数 (0-100)
                    clamped_progress = max(0.0, min(float(progress), 1.0))
                    progress_percent = int(clamped_progress * 100)
                    if progress_percent < last_progress_percent:
                        progress_percent = last_progress_percent
                    if progress_percent == last_progress_percent:
                        return
                    last_progress_percent = progress_percent
                    task_progress = float(progress_percent)
                    if multipart_part:
                        from ..task_parts import get_task_parts, update_task_part
                        part_index = int(multipart_part["index"])
                        update_task_part(str(task_id), part_index, {
                            "status": "TRANSCRIBING", "progress": progress_percent
                        })
                        parts = get_task_parts(str(task_id), include_text=False)
                        weighted_total = sum(
                            max(0.0, float(part.get("duration") or part.get("audio_duration") or 0.0))
                            for part in parts
                        )
                        if weighted_total > 0:
                            weighted_done = sum(
                                max(0.0, float(part.get("duration") or part.get("audio_duration") or 0.0))
                                * max(0.0, min(float(part.get("progress") or 0.0), 100.0))
                                / 100.0
                                for part in parts
                            )
                            task_progress = weighted_done / weighted_total * 100.0
                        elif parts:
                            task_progress = sum(
                                max(0.0, min(float(part.get("progress") or 0.0), 100.0))
                                for part in parts
                            ) / len(parts)
                    from ..task_updater import update_and_notify
                    self._submit_coro(update_and_notify(task_id, {"progress": task_progress}))

                    # 每 10% 打点一次，便于快速判断是后端卡住还是前端未刷新。
                    progress_bucket = progress_percent // 10
                    if progress_bucket > last_logged_bucket or progress_percent >= 100:
                        last_logged_bucket = progress_bucket
                        logger.info(f"[{self.name}] 转录进度 task={task_id}: {progress_percent}%")

            result = self._transcribe_audio_with_chunking(
                audio_file,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
            logger.info(f"[{self.name}] 转录完成。")

            # 打印性能指标
            logger.info(f"[{self.name}] --- 性能指标 ---")
            logger.info(f"[{self.name}] 模型加载耗时: {result.model_load_time:.2f}s")
            logger.info(f"[{self.name}] 音频时长: {result.audio_duration:.2f}s")
            logger.info(f"[{self.name}] 转录耗时: {result.transcription_time:.2f}s")
            logger.info(f"[{self.name}] 实时率 (RTF): {result.real_time_factor:.2f}")
            logger.info(f"[{self.name}] 检测到的语言: {result.language} (置信度: {result.language_probability:.2f})")

            intermediate_file_path = os.path.splitext(output_file)[0] + ".txt"
            self._save_transcription_to_file(result, intermediate_file_path)

            summary_mode = str(payload.get("summary_mode") or "").strip().lower()
            # 仅转录模式：转录完成后直接终态，不派发 AI 总结
            skip_summarization = summary_mode == "none"

            if multipart_part:
                from ..task_parts import update_task_part
                with open(intermediate_file_path, "r", encoding="utf-8") as f:
                    part_transcript = f.read()
                update_task_part(str(task_id), int(multipart_part["index"]), {
                    "status": "COMPLETED" if skip_summarization else "SUMMARIZING",
                    "progress": 100,
                    "transcript": part_transcript,
                    "audio_duration": result.audio_duration,
                    "transcription_time": result.transcription_time,
                })

            if task_id:
                from ..db import TaskStatus
                # 保存转录文本到数据库供前端查看
                with open(intermediate_file_path, "r", encoding="utf-8") as f:
                    transcript = f.read()

                update_data = {
                    "status": TaskStatus.SUMMARIZING,
                    "progress": 0.0,
                    "transcript": transcript,
                    "transcription_time": result.transcription_time,
                    "audio_duration": result.audio_duration,
                    "summary_chunk_total": None,
                    "summary_chunk_done": None,
                    "summary_meta": None,
                }
                if summary_mode in {"auto", "standard", "agent", "none"}:
                    update_data["summary_mode"] = summary_mode
                if skip_summarization:
                    update_data["status"] = TaskStatus.COMPLETED
                    update_data["progress"] = 100
                    logger.info(
                        f"[TranscriberWorker] 仅转录模式，跳过 AI 总结: task_id={task_id}, "
                        f"status: TRANSCRIBING→COMPLETED, "
                        f"duration={result.transcription_time:.2f}s, audio_duration={result.audio_duration:.2f}s"
                    )
                else:
                    # 转录完成，准备进入总结阶段
                    logger.info(
                        f"[TranscriberWorker] Transcription completed: task_id={task_id}, "
                        f"status: TRANSCRIBING→SUMMARIZING, "
                        f"duration={result.transcription_time:.2f}s, audio_duration={result.audio_duration:.2f}s"
                    )
                from ..task_updater import update_and_notify
                # 先广播/持久化终态，再异步生成标题（保证 COMPLETED 不晚于标题写入）
                self._submit_coro(update_and_notify(task_id, update_data))

                # “总结标题”开关（默认开启）：置 COMPLETED 后异步对全文生成标题。
                # 复用 LLMWorker.generate_topic（api同款单次调用），失败静默降级，
                # 不阻塞任务终态。multipart 分P子任务跳过，由 merge 父任务统一生成一次。
                # self._loop 守卫：未启动的事件循环（如同步单测）下不创建未 await 的协程。
                if (
                    skip_summarization
                    and self._loop
                    and bool(payload.get("generate_topic", True))
                    and not multipart_part
                ):
                    from ..llm.llm_worker import generate_topic_for_task
                    self._submit_coro(
                        generate_topic_for_task(
                            self._next_worker, task_id, transcript
                        )
                    )

            next_payload = {
                "intermediate_file_path": intermediate_file_path,
                **payload
            }

            if not multipart_part and not skip_summarization:
                self._submit_coro(self._next_worker.add_task(next_payload))
            return intermediate_file_path

        except (TaskCancelledError, TranscriptionCancelled):
            logger.info(f"[{self.name}] 任务已取消，停止后续转录流程: {task_id}")
        except Exception as e:
            logger.error(f"[{self.name}] 转录过程中发生错误: {e}", exc_info=True)
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify
                self._submit_coro(update_and_notify(
                    task_id,
                    {
                        "status": TaskStatus.FAILED,
                        "error_message": _build_actionable_transcription_error(str(e)),
                    },
                ))
