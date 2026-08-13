"""VibeVoice 模型加载噪声的定向日志过滤。

背景（调研结论 2026-08-13，详见 docs/version/fix-vibevoice-load-warnings/changelog.md）：
vibevoice 1.0.0 + transformers 4.57 组合在本地模型加载时打出三条装饰性警告：

1. ``transformers.tokenization_utils_base`` —— Qwen2.5-7B checkpoint 的
   tokenizer_config.json 声明慢版 ``Qwen2Tokenizer``，vibevoice 以
   ``VibeVoiceASRTextTokenizerFast``（Qwen2TokenizerFast 子类）加载，
   transformers 仅按类名字符串比较即打印 mismatch 警告。
   快/慢版同词表，分词结果等价，无功能影响。
2. ``transformers.configuration_utils`` —— transformers 4.57 中
   ``PretrainedConfig.torch_dtype`` 已弃用（建议 ``dtype``），vibevoice 1.0.0
   仍访问该 property。我方调用链已正确使用 ``dtype=`` 传参，dtype 实际生效值正确。
3. ``vibevoice.processor.vibevoice_asr_processor`` —— checkpoint 缺
   ``preprocessor_config.json`` 时打印 "Using default configuration"。
   信息量更大的前一行 "Could not load preprocessor_config.json: ..." 予以保留；
   回退默认值（24000/3200）与我方 transcribe 的取值一致（见
   ``VibeVoiceAsrTranscriber`` 中的 effective-params INFO 日志）。

三条警告每次加载刷屏、易掩盖真实告警，故在对应 logger 上做消息级精确过滤：
仅丢弃这些 logger 上的已知消息；同名消息来自其他 logger、同 logger 的
其他消息均不受影响。安装幂等。
"""

from __future__ import annotations

import logging
from typing import Final

# logger 名 → 需要丢弃的消息子串（仅在该 logger 内匹配）
_NOISE_PATTERNS: Final[dict[str, tuple[str, ...]]] = {
    "transformers.tokenization_utils_base": (
        "The tokenizer class you load from this checkpoint is not the same type",
    ),
    "transformers.configuration_utils": (
        "`torch_dtype` is deprecated! Use `dtype` instead!",
    ),
    "vibevoice.processor.vibevoice_asr_processor": ("Using default configuration",),
}


class _VibeVoiceNoiseFilter(logging.Filter):
    """丢弃单个 logger 上匹配已知噪声模式的日志记录。"""

    def __init__(self, patterns: tuple[str, ...]) -> None:
        super().__init__()
        self._patterns = patterns

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # 消息格式化失败时绝不因过滤打断日志链路——放行
            return True
        return not any(pattern in message for pattern in self._patterns)


def install_vibevoice_log_noise_filters() -> None:
    """在已知噪声 logger 上安装消息级过滤（幂等，重复调用不叠加）。"""
    for logger_name, patterns in _NOISE_PATTERNS.items():
        logger = logging.getLogger(logger_name)
        if any(isinstance(f, _VibeVoiceNoiseFilter) for f in logger.filters):
            continue
        logger.addFilter(_VibeVoiceNoiseFilter(patterns))
