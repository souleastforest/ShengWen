from __future__ import annotations

import os
import importlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional

from loguru import logger

# --- 自定义异常 ---


class TranscriberError(Exception):
    """转录器模块的通用基础异常。"""

    pass


class ModelLoadError(TranscriberError):
    """在加载或初始化模型时发生错误的异常。"""

    pass


class TranscriptionError(TranscriberError):
    """在文件转录过程中发生错误的异常。"""

    pass


class TranscriptionCancelled(TranscriberError):
    """转录任务被外部取消（例如任务删除）。"""

    pass


# --- 数据类 ---


@dataclass
class TranscriptionResult:
    """
    一个用于保存转录结果的数据类，包含性能指标。
    """

    segments: list[dict[str, Any]]  # 转录出的文本片段列表
    transcription_time: float  # 转录耗时（秒）
    real_time_factor: float  # 实时率 (RTF)，即处理时间 / 音频时长
    total_time: float  # 总耗时（秒），包括转录和其他开销
    model_load_time: float  # 模型加载耗时（秒）
    audio_duration: float  # 音频总时长（秒）
    language: str  # 检测到的语言代码 (例如, "zh")
    language_probability: float  # 语言检测的置信度 (0-1)


# --- 注册表 ---

_TRANSCRIBER_REGISTRY: dict[str, type[Transcriber]] = {}


@dataclass
class ModelPathValidationResult:
    """模型路径验证结果。"""

    valid: bool
    message: str
    resolved_path: str
    missing_files: list[str]


# --- 抽象基类 ---


class Transcriber(ABC):
    """
    语音转文本转录器的抽象基类。
    """

    transcriber_name: str = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        name = getattr(cls, "transcriber_name", "")
        if not name and cls is not Transcriber:
            base_name = cls.__name__.removesuffix("Transcriber")
            name = re.sub(r"(?<!^)(?=[A-Z])", "_", base_name).lower()
            cls.transcriber_name = name
        if name:
            _TRANSCRIBER_REGISTRY[name] = cls

    def __init__(self, **kwargs):
        pass

    @staticmethod
    def get_class(name: str) -> type[Transcriber]:
        """获取已注册的转录器类。如果模块未加载，尝试延迟导入。

        注意："未注册"并不一定是名字拼错——很可能是依赖缺失导致模块
        导入失败（如 vibe_voice_asr 依赖 torch/vibevoice，环境未安装时
        顶层 `import torch` 抛 ModuleNotFoundError）。此时抛出的
        ValueError 通过 `from exc` 保留原始导入异常为 __cause__，
        便于上层日志/错误排查定位真实原因。
        """
        if name not in _TRANSCRIBER_REGISTRY:
            module_name = f"src.main.python.sheng_wen.transcriber.{name}_transcriber"
            try:
                importlib.import_module(module_name)
            except (ImportError, ModuleNotFoundError) as exc:
                logger.error(
                    "[Transcriber] 懒加载转录器模块失败: module={}, name={}, error={}",
                    module_name,
                    name,
                    exc,
                )
                raise ValueError(
                    f"未注册的转录器类型: {name}（模块 {module_name} 导入失败，可能缺失依赖）"
                ) from exc
        if name not in _TRANSCRIBER_REGISTRY:
            raise ValueError(f"转录器模块已加载但未注册: {name}")
        return _TRANSCRIBER_REGISTRY[name]

    @classmethod
    def validate_model_path(cls, path: str) -> ModelPathValidationResult:
        """验证模型路径。默认实现只检查路径是否为存在的目录。"""
        abs_path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(abs_path):
            return ModelPathValidationResult(True, "路径有效。", abs_path, [])
        return ModelPathValidationResult(
            False, f"路径不存在或不是目录: {abs_path}", abs_path, []
        )

    @classmethod
    def required_model_files(cls) -> list[str]:
        """返回该转录器要求的模型文件列表。"""
        return []

    @classmethod
    def build_runtime_kwargs(cls, runtime_state: dict) -> dict:
        """根据运行时状态构建实例化参数。"""
        return {}

    @abstractmethod
    def transcribe(
        self,
        file_path: str,
        progress_callback: Optional[Callable[[float], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> TranscriptionResult:
        """
        转录一个音频或视频文件。

        参数:
            file_path: 媒体文件的路径。
            progress_callback: 一个可选的回调函数，接收一个 0.0 到 1.0 之间的浮点数表示进度。
            cancel_check: 一个可选回调，返回 True 表示应立即中止当前任务。

        返回:
            一个包含文本片段和性能指标的 TranscriptionResult 对象。
        """
        pass


# --- 工厂函数 ---


def get_transcriber(name: str, **kwargs) -> Transcriber:
    """
    一个工厂函数，用于获取指定名称的转录器实例。

    这允许我们在不直接依赖具体实现的情况下创建转录器。

    参数:
        name: 转录器的名称 (例如, "fast_whisper")。
        **kwargs: 传递给转录器构造函数的参数。

    返回:
        一个 Transcriber 的实例。

    异常:
        ValueError: 如果找不到指定名称的转录器模块。
        ModelLoadError: 如果在初始化模型时发生错误。
    """
    try:
        transcriber_class = Transcriber.get_class(name)

        # 在工厂函数中捕获模型加载错误
        return transcriber_class(**kwargs)
    except TranscriberError:  # 重新抛出我们自定义的异常
        raise
