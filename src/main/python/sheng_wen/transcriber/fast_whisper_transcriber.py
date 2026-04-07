import time
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
import math
from urllib.parse import urlsplit

from faster_whisper import WhisperModel
from faster_whisper.utils import download_model
from loguru import logger

from .transcriber import (
    Transcriber,
    TranscriptionResult,
    ModelLoadError,
    TranscriptionError,
    TranscriptionCancelled,
)


_PROXY_ENV_VAR_NAMES = (
    "ALL_PROXY",
    "all_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
)


class FastWhisperTranscriber(Transcriber):
    """
    一个使用 fast-whisper 库的转录器实现。
    """

    @staticmethod
    def _emit_model_loading_heartbeat(model_identifier: str, stop_event: Event, start_time: float):
        """在模型加载期间周期性输出日志，避免用户误认为程序卡死。"""
        while not stop_event.wait(10):
            elapsed = int(time.time() - start_time)
            logger.info(
                f"[FastWhisperTranscriber] 模型 '{model_identifier}' 正在加载中（可能在下载/解压），已等待 {elapsed}s..."
            )

    @staticmethod
    def _probe_model_source(model_identifier: str) -> tuple[str, str]:
        """
        预探测模型来源，提升启动日志可读性。
        返回:
            (source, detail)
            source ∈ {"local_path", "hf_cache_hit", "hf_cache_miss", "unknown"}
        """
        path = Path(model_identifier)
        if path.exists():
            return ("local_path", str(path.resolve()))

        try:
            snapshot_path = download_model(model_identifier, local_files_only=True)
            return ("hf_cache_hit", snapshot_path)
        except Exception as e:
            err_name = e.__class__.__name__
            err_msg = str(e).lower()
            if err_name == "LocalEntryNotFoundError" or "cached snapshot" in err_msg:
                return ("hf_cache_miss", "")
            return ("unknown", f"{e.__class__.__name__}: {e}")

    @staticmethod
    def _resolve_hf_cache_root() -> tuple[str, str]:
        """
        解析 Hugging Face Hub 缓存根目录。
        返回:
            (cache_root, source)
        """
        hf_hub_cache = os.environ.get("HF_HUB_CACHE")
        if hf_hub_cache:
            return (hf_hub_cache, "HF_HUB_CACHE")

        huggingface_hub_cache = os.environ.get("HUGGINGFACE_HUB_CACHE")
        if huggingface_hub_cache:
            return (huggingface_hub_cache, "HUGGINGFACE_HUB_CACHE")

        hf_home = os.environ.get("HF_HOME")
        if hf_home:
            return (str(Path(hf_home) / "hub"), "HF_HOME")

        try:
            from huggingface_hub import constants as hf_constants

            cache_root = getattr(hf_constants, "HUGGINGFACE_HUB_CACHE", None)
            if cache_root:
                return (str(cache_root), "huggingface_hub.constants.HUGGINGFACE_HUB_CACHE")
        except Exception:
            pass

        return (str(Path.home() / ".cache" / "huggingface" / "hub"), "default")

    @staticmethod
    def _normalize_proxy_url(proxy_url: str) -> tuple[str, bool]:
        normalized = str(proxy_url or "").strip()
        if normalized.lower().startswith("socks://"):
            return (f"socks5://{normalized[len('socks://'):]}", True)
        return (normalized, False)

    @staticmethod
    def _mask_proxy_for_log(proxy_url: str) -> str:
        raw = str(proxy_url or "").strip()
        if not raw:
            return ""
        try:
            parts = urlsplit(raw)
            scheme = parts.scheme
            host = parts.hostname or ""
            port = f":{parts.port}" if parts.port else ""
            has_auth = "yes" if parts.username or parts.password else "no"
            if scheme:
                return f"{scheme}://{host}{port} (auth={has_auth})"
            return raw
        except Exception:
            return raw

    @classmethod
    def _build_proxy_env_snapshot(cls) -> str:
        items: list[str] = []
        for env_name in _PROXY_ENV_VAR_NAMES:
            value = os.environ.get(env_name)
            if value:
                items.append(f"{env_name}={cls._mask_proxy_for_log(value)}")
            else:
                items.append(f"{env_name}=<empty>")

        no_proxy_value = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
        if no_proxy_value:
            rules = [rule.strip() for rule in no_proxy_value.split(",") if rule.strip()]
            preview = ", ".join(rules[:8])
            if len(rules) > 8:
                preview += f", ... (+{len(rules) - 8})"
            items.append(f"NO_PROXY={preview}")
        else:
            items.append("NO_PROXY=<empty>")

        return " | ".join(items)

    @classmethod
    def _log_proxy_env_snapshot(cls):
        logger.info(
            "[FastWhisperTranscriber] 模型加载前代理环境快照: "
            f"{cls._build_proxy_env_snapshot()}"
        )

    @classmethod
    @contextmanager
    def _patched_proxy_env_for_httpx(cls):
        """
        临时修正 httpx 不接受的代理 scheme。
        当前仅兼容 Clash 等工具常见导出的 socks:// -> socks5://。

        额外处理：重置 huggingface_hub 的全局 HTTP session，
        防止其在旧代理环境下缓存了不可用的 client。
        """
        patched_values: dict[str, str] = {}
        for env_name in _PROXY_ENV_VAR_NAMES:
            raw_value = os.environ.get(env_name)
            if not raw_value:
                continue
            normalized_value, changed = cls._normalize_proxy_url(raw_value)
            if not changed:
                continue
            patched_values[env_name] = raw_value
            os.environ[env_name] = normalized_value
            logger.warning(
                "[FastWhisperTranscriber] 检测到不兼容代理配置，"
                f"已临时将 {env_name}=socks://... 修正为 socks5://... 以兼容 httpx。"
            )

        try:
            try:
                from huggingface_hub.utils._http import close_session

                close_session()
            except Exception:
                pass
            yield
        finally:
            try:
                from huggingface_hub.utils._http import close_session

                close_session()
            except Exception:
                pass
            for env_name, raw_value in patched_values.items():
                os.environ[env_name] = raw_value

    @staticmethod
    def _build_model_load_error_message(model_identifier: str, error: Exception) -> str:
        text = str(error or "").strip()
        lowered = text.lower()
        prefix = f"加载模型 '{model_identifier}' 失败。"

        if "unknown scheme for proxy url" in lowered and "socks://" in lowered:
            return (
                f"{prefix} 检测到代理地址使用了不兼容的 socks:// 格式。"
                "请将代理变量改为 socks5://127.0.0.1:端口，或改用 HTTP 代理后重试。"
            )

        if "using socks proxy" in lowered and "socksio" in lowered:
            return (
                f"{prefix} 当前环境配置了 SOCKS 代理，但缺少 httpx 的 SOCKS 依赖 socksio。"
                "请安装相关依赖，或改用 HTTP 代理后重试。"
            )

        if (
            error.__class__.__name__ == "LocalEntryNotFoundError"
            or "cannot find the appropriate snapshot folder" in lowered
            or "please check your internet connection and try again" in lowered
        ):
            return (
                f"{prefix} 本地未找到该模型缓存，且当前无法从 Hugging Face 下载。"
                "请检查网络或代理是否可访问 huggingface.co，"
                "或者先手动下载模型并切换到本地模型目录。"
            )

        if text:
            return f"{prefix} {text}"
        return prefix

    @staticmethod
    def _probe_media_duration(file_path: str) -> float:
        """
        使用 ffprobe 兜底探测媒体时长（秒）。
        仅用于 info.duration 不可用时，不影响主流程。
        """
        try:
            import ffmpeg

            probe = ffmpeg.probe(file_path)
            fmt = probe.get("format") or {}
            raw_duration = fmt.get("duration")
            if raw_duration is not None:
                duration = float(raw_duration)
                if math.isfinite(duration) and duration > 0:
                    return duration

            stream_durations = []
            for stream in probe.get("streams") or []:
                raw = stream.get("duration")
                if raw is None:
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and value > 0:
                    stream_durations.append(value)
            if stream_durations:
                return max(stream_durations)
        except Exception as e:
            logger.debug(f"[FastWhisperTranscriber] ffprobe 探测时长失败: file={file_path}, error={e}")
        return 0.0

    def __init__(self, model_size: str = "base", model_size_or_path: str = None, device: str = "cpu", compute_type: str = "int8", **kwargs):
        """
        初始化 FastWhisperTranscriber。

        参数:
            model_size: Whisper 模型的大小 (例如, "tiny", "base", "small", "medium", "large")。
                        当 model_size_or_path 为 None 时使用此参数。
            model_size_or_path: 模型大小或本地模型路径。如果提供，优先使用此参数。
            device: 运行模型的设备 ("cpu" 或 "cuda")。
            compute_type: 模型的计算类型 (例如, "int8", "float16")。
            
        异常:
            ModelLoadError: 如果模型加载失败。
        """
        super().__init__(**kwargs)
        start_time = time.time()
        heartbeat_stop = Event()
        heartbeat = None
        model_identifier = model_size_or_path if model_size_or_path else model_size
        try:
            cache_root, cache_source = self._resolve_hf_cache_root()
            self._log_proxy_env_snapshot()
            logger.info(
                f"[FastWhisperTranscriber] Hugging Face 缓存根目录: {cache_root} (source={cache_source})"
            )

            # 注意：huggingface_hub/httpx 可能会在预探测阶段初始化全局 client。
            # 因此代理修正要覆盖“预探测 + 实际加载”两个阶段，避免 socks:// 造成的全局污染。
            with self._patched_proxy_env_for_httpx():
                source, source_detail = self._probe_model_source(str(model_identifier))
                if source == "local_path":
                    logger.info(
                        f"[FastWhisperTranscriber] 检测结果: 使用本地模型目录（不需要下载）: {source_detail}"
                    )
                    logger.info(
                        f"[FastWhisperTranscriber] 正在加载本地模型: {model_identifier} (device={device}, compute_type={compute_type})"
                    )
                elif source == "hf_cache_hit":
                    logger.info(
                        f"[FastWhisperTranscriber] 检测结果: 命中 Hugging Face 本地缓存（不需要下载）: {source_detail}"
                    )
                    logger.info(
                        f"[FastWhisperTranscriber] 正在从缓存加载模型: {model_identifier} (device={device}, compute_type={compute_type})"
                    )
                elif source == "hf_cache_miss":
                    logger.warning(
                        "[FastWhisperTranscriber] 检测结果: 本地未找到该模型缓存，将尝试联网下载。"
                    )
                    logger.info(
                        f"[FastWhisperTranscriber] 正在加载模型: {model_identifier} (device={device}, compute_type={compute_type})"
                    )
                    logger.info(
                        "[FastWhisperTranscriber] 如果网络无法访问 Hugging Face，稍后会报加载失败。"
                    )
                else:
                    logger.info(
                        f"[FastWhisperTranscriber] 正在加载模型: {model_identifier} (device={device}, compute_type={compute_type})"
                    )
                    logger.info(
                        "[FastWhisperTranscriber] 无法预判是否需要下载；如本地无缓存将自动下载（首次可能较慢）。"
                    )
                    logger.debug(f"[FastWhisperTranscriber] 预探测细节: {source_detail}")

                heartbeat = Thread(
                    target=self._emit_model_loading_heartbeat,
                    args=(str(model_identifier), heartbeat_stop, start_time),
                    daemon=True,
                )
                heartbeat.start()
                self.model = WhisperModel(model_identifier, device=device, compute_type=compute_type)
        except Exception as e:
            message = self._build_model_load_error_message(str(model_identifier), e)
            logger.error(f"[FastWhisperTranscriber] {message}", exc_info=True)
            raise ModelLoadError(message) from e
        finally:
            heartbeat_stop.set()
            if heartbeat is not None and heartbeat.is_alive():
                heartbeat.join(timeout=0.2)
        self.model_load_time = time.time() - start_time
        logger.info(
            f"[FastWhisperTranscriber] 模型 '{model_identifier}' 加载完成，耗时 {self.model_load_time:.2f}s。"
        )

    def transcribe(self, file_path: str, progress_callback=None, cancel_check=None) -> TranscriptionResult:
        """
        使用 fast-whisper 转录一个音频或视频文件。

        参数:
            file_path: 媒体文件的路径。
            progress_callback: 一个可选的回调函数，接收一个 0.0 到 1.0 之间的浮点数表示进度。

        返回:
            一个包含文本片段和性能指标的 TranscriptionResult 对象。
            
        异常:
            TranscriptionError: 如果文件转录失败。
        """
        total_start_time = time.time()

        try:
            if cancel_check and cancel_check():
                raise TranscriptionCancelled("任务已取消，停止转录。")

            transcribe_start_time = time.time()
            logger.info(f"[FastWhisperTranscriber] 开始转录文件: {file_path}")
            result_segments = []
            progress_state = {"value": 0.0, "segments": 0, "phase": "preparing"}
            heartbeat_stop = Event()

            def heartbeat():
                while not heartbeat_stop.wait(10):
                    elapsed = int(time.time() - transcribe_start_time)
                    logger.info(
                        "[FastWhisperTranscriber] 转录进行中: "
                        f"phase={progress_state['phase']}, elapsed={elapsed}s, "
                        f"segments={progress_state['segments']}, "
                        f"progress={int(progress_state['value'] * 100)}%"
                    )

            heartbeat_thread = Thread(target=heartbeat, daemon=True)
            heartbeat_thread.start()

            progress_state["phase"] = "initializing"
            segments_generator, info = self.model.transcribe(file_path, word_timestamps=True)
            progress_state["phase"] = "streaming"
            logger.info("[FastWhisperTranscriber] 已创建分段生成器，开始拉取分段...")

            raw_duration = float(info.duration or 0.0) if info.duration is not None else 0.0
            duration = raw_duration if math.isfinite(raw_duration) and raw_duration > 0 else 0.0
            if duration <= 0:
                probed_duration = self._probe_media_duration(file_path)
                if probed_duration > 0:
                    duration = probed_duration
                    logger.warning(
                        "[FastWhisperTranscriber] 模型返回时长不可用，已回退 ffprobe 时长估算: "
                        f"{duration:.2f}s"
                    )
                else:
                    logger.warning(
                        "[FastWhisperTranscriber] 无法获得媒体总时长，将使用分段数量估算进度（仅用于 UI 反馈）。"
                    )

            try:
                for segment in segments_generator:
                    if cancel_check and cancel_check():
                        raise TranscriptionCancelled("任务已取消，停止转录。")

                    result_segments.append({
                        "start": segment.start,
                        "end": segment.end,
                        "text": segment.text
                    })

                    progress_state["segments"] += 1
                    if progress_state["segments"] == 1:
                        logger.info(
                            "[FastWhisperTranscriber] 已收到首个分段: "
                            f"start={segment.start:.2f}, end={segment.end:.2f}"
                        )

                    if progress_callback:
                        # 优先使用真实时长；不可用时退化为“分段数估算进度”。
                        if duration > 0:
                            progress = min(max(float(segment.end), 0.0) / duration, 1.0)
                        else:
                            # 分段数越多，进度越高，最高封顶 95%，最终由收尾回调置 100%。
                            progress = min(0.95, progress_state["segments"] / (progress_state["segments"] + 24.0))
                        if progress > progress_state["value"]:
                            progress_state["value"] = progress
                            progress_callback(progress_state["value"])
            finally:
                heartbeat_stop.set()
                if heartbeat_thread.is_alive():
                    heartbeat_thread.join(timeout=0.2)

            if progress_callback:
                progress_callback(1.0)
             
            transcription_time = time.time() - transcribe_start_time
            total_time = time.time() - total_start_time
            
            real_time_factor = 0.0
            if info.duration > 0:
                real_time_factor = transcription_time / info.duration

            return TranscriptionResult(
                segments=result_segments,
                transcription_time=transcription_time,
                real_time_factor=real_time_factor,
                total_time=total_time,
                model_load_time=self.model_load_time,
                audio_duration=info.duration,
                language=info.language,
                language_probability=info.language_probability
            )
        except TranscriptionCancelled:
            raise
        except Exception as e:
            # 捕获所有可能的转录时异常 (例如文件不存在, 文件格式错误)
            raise TranscriptionError(f"转录文件 '{file_path}' 时发生错误: {e}") from e
