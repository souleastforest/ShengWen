"""红绿测试：VibeVoice 模型加载噪声的定向日志过滤与预处理参数一致性。

背景（调研结论 2026-08-13，详见 docs/version/fix-vibevoice-load-warnings/changelog.md）：
vibevoice 1.0.0 + transformers 4.57 组合在模型加载时打出三条装饰性警告：
  1. transformers.tokenization_utils_base —— Qwen2Tokenizer(慢) 与
     VibeVoiceASRTextTokenizerFast(快) 类名不一致（同词表、分词等价）；
  2. transformers.configuration_utils —— vibevoice 访问已弃用的
     config.torch_dtype property；
  3. vibevoice.processor.vibevoice_asr_processor —— checkpoint 缺
     preprocessor_config.json 时打印 "Using default configuration"。
三条均无功能影响，但每次加载刷屏、掩盖真实告警。

本测试验证：消息级过滤只压制这三条已知噪声；同名消息来自其他 logger、
同 logger 的其他消息（含信息量更大的 "Could not load ..." 前缀行）均不受影响。
"""

from __future__ import annotations

import contextlib
import logging
import sys

import pytest

import torch  # noqa: F401 —— 预导入，避免 patch.dict 退出时逐出 torch 引发二次导入 RuntimeError

from src.main.python.sheng_wen.transcriber.vibevoice_log_noise import (
    _NOISE_PATTERNS,
    install_vibevoice_log_noise_filters,
)

_TRANSCRIBER_MODULE = "src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber"


@contextlib.contextmanager
def mock_vibevoice_imports():
    """mock vibevoice 包并逐出已缓存的真实模块，保证被测 transcriber 走 mock 导入。

    全量测试中其他用例（如 test_transcriber_registry）会先导入真实 vibevoice，
    patch.dict("sys.modules") 不生效于已缓存模块——必须显式逐出后重新导入。
    patch.dict 退出时会恢复 sys.modules 原状（含被逐出的真实模块）。
    """
    from unittest.mock import MagicMock, patch

    mocked = {
        "vibevoice": MagicMock(),
        "vibevoice.modular": MagicMock(),
        "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
        "vibevoice.processor": MagicMock(),
        "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
    }
    with patch.dict("sys.modules", mocked):
        # 逐出已缓存的真实 transcriber 模块（mock 的 vibevoice 条目保留，
        # 供重新导入时使用）；patch.dict 退出时恢复 sys.modules 原状。
        sys.modules.pop(_TRANSCRIBER_MODULE, None)
        yield


TOKENIZER_LOGGER = "transformers.tokenization_utils_base"
CONFIG_LOGGER = "transformers.configuration_utils"
PROCESSOR_LOGGER = "vibevoice.processor.vibevoice_asr_processor"

MISMATCH_MSG = (
    "The tokenizer class you load from this checkpoint is not the same type as the"
    " class this function is called from. It may result in unexpected tokenization."
)
TORCH_DTYPE_MSG = "`torch_dtype` is deprecated! Use `dtype` instead!"
DEFAULT_CONFIG_MSG = "Using default configuration"


@pytest.fixture(autouse=True)
def _pristine_logger_filters():
    """保存/恢复被测 logger 的 filters，保证用例间隔离。"""
    saved = {name: list(logging.getLogger(name).filters) for name in _NOISE_PATTERNS}
    yield
    for name, filters in saved.items():
        logging.getLogger(name).filters = filters


def _noise_filter_of(logger: logging.Logger):
    from src.main.python.sheng_wen.transcriber.vibevoice_log_noise import (
        _VibeVoiceNoiseFilter,
    )

    filters = [f for f in logger.filters if isinstance(f, _VibeVoiceNoiseFilter)]
    assert len(filters) == 1, f"期望恰好 1 个噪声过滤器，实际 {len(filters)} 个"
    return filters[0]


def _make_record(logger_name: str, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=logger_name,
        level=logging.WARNING,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestInstallIdempotent:
    def test_install_is_idempotent(self):
        install_vibevoice_log_noise_filters()
        install_vibevoice_log_noise_filters()
        for name in _NOISE_PATTERNS:
            logger = logging.getLogger(name)
            from src.main.python.sheng_wen.transcriber.vibevoice_log_noise import (
                _VibeVoiceNoiseFilter,
            )

            installed = [
                f for f in logger.filters if isinstance(f, _VibeVoiceNoiseFilter)
            ]
            assert len(installed) == 1, f"{name} 上过滤器应恰好安装一次"


class TestKnownNoiseDropped:
    @pytest.mark.parametrize(
        "logger_name,message",
        [
            (TOKENIZER_LOGGER, MISMATCH_MSG),
            (CONFIG_LOGGER, TORCH_DTYPE_MSG),
            (PROCESSOR_LOGGER, DEFAULT_CONFIG_MSG),
        ],
    )
    def test_noise_record_is_filtered(self, logger_name, message):
        install_vibevoice_log_noise_filters()
        noise_filter = _noise_filter_of(logging.getLogger(logger_name))
        assert noise_filter.filter(_make_record(logger_name, message)) is False

    @pytest.mark.parametrize(
        "logger_name,message",
        [
            (TOKENIZER_LOGGER, MISMATCH_MSG),
            (CONFIG_LOGGER, TORCH_DTYPE_MSG),
            (PROCESSOR_LOGGER, DEFAULT_CONFIG_MSG),
        ],
    )
    def test_noise_warning_never_reaches_handlers(self, logger_name, message):
        install_vibevoice_log_noise_filters()
        logger = logging.getLogger(logger_name)
        with pytest.raises(AssertionError):
            # 记录被 logger filter 丢弃 → 录制 handler 收不到任何输出 → 抛 AssertionError
            with _AssertLogs(logger, "WARNING"):
                logger.warning(message)


class _AssertLogs:
    """python 3.12 下 assertLogs 的最小替身：断言上下文内产生过日志。"""

    def __init__(self, logger, level):
        self._logger = logger
        self._level = level
        self._handler = None
        self.records = []

    def __enter__(self):
        self._handler = _RecordingHandler(self.records)
        self._handler.setLevel(self._level)
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._logger.removeHandler(self._handler)
        finally:
            if not self.records:
                raise AssertionError("no logs of level WARNING or higher triggered")
        return False


class _RecordingHandler(logging.Handler):
    def __init__(self, records):
        super().__init__()
        self._records = records

    def emit(self, record):
        self._records.append(record)


class TestNoiseFilterDoesNotOverreach:
    """不误伤：其他消息、其他 logger 的同名消息、信息量更大的前缀行都必须保留。"""

    def test_other_messages_on_same_logger_pass(self):
        install_vibevoice_log_noise_filters()
        logger = logging.getLogger(PROCESSOR_LOGGER)
        noise_filter = _noise_filter_of(logger)
        informative = "Could not load preprocessor_config.json: [Errno 2] No such file"
        assert noise_filter.filter(_make_record(PROCESSOR_LOGGER, informative)) is True

    @pytest.mark.parametrize(
        "logger_name,message",
        [
            # 同名消息出现在未注册的 logger 上 → 不应被过滤
            ("transformers.models.auto.tokenization_auto", MISMATCH_MSG),
            ("vibevoice.processor.vibevoice_processor", DEFAULT_CONFIG_MSG),
            ("some.other.logger", TORCH_DTYPE_MSG),
        ],
    )
    def test_same_message_on_unregistered_logger_passes(self, logger_name, message):
        install_vibevoice_log_noise_filters()
        logger = logging.getLogger(logger_name)
        # 未注册 logger 不应存在我们的过滤器
        from src.main.python.sheng_wen.transcriber.vibevoice_log_noise import (
            _VibeVoiceNoiseFilter,
        )

        assert not any(isinstance(f, _VibeVoiceNoiseFilter) for f in logger.filters)

    def test_unrelated_warning_on_registered_logger_passes(self):
        install_vibevoice_log_noise_filters()
        logger = logging.getLogger(CONFIG_LOGGER)
        noise_filter = _noise_filter_of(logger)
        real = "Some genuinely important configuration warning"
        assert noise_filter.filter(_make_record(CONFIG_LOGGER, real)) is True

    def test_unformattable_record_is_passed_through_not_raised(self):
        """消息格式化失败时过滤器必须放行，绝不打断日志链路。"""
        install_vibevoice_log_noise_filters()
        logger = logging.getLogger(CONFIG_LOGGER)
        noise_filter = _noise_filter_of(logger)
        broken = logging.LogRecord(
            name=CONFIG_LOGGER,
            level=logging.WARNING,
            pathname=__file__,
            lineno=0,
            msg="%d",
            args=("not-a-number",),
            exc_info=None,
        )
        # getMessage() 会抛 TypeError；过滤器应放行而非传播异常
        assert noise_filter.filter(broken) is True


class TestTranscriberUsesProcessorSampleRate:
    """音频时长计算必须使用 processor.target_sample_rate，而非硬编码 24000。

    背景：checkpoint 缺 preprocessor_config.json 时库回退 24000/3200 默认值，
    与历史硬编码恰好一致；但若未来 checkpoint 自带不同采样率的配置，
    硬编码会静默算错 audio_duration → generation budget 失准（静默失败风险）。
    """

    def test_speech_audio_duration_uses_given_rate(self):
        from unittest.mock import MagicMock

        with mock_vibevoice_imports():
            from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
                VibeVoiceAsrTranscriber,
            )

            speech_tensors = MagicMock()
            speech_tensors.shape = [1, 48000]

            # 采样率 16000 → 3.0s；缺省回退 24000 → 2.0s
            assert (
                VibeVoiceAsrTranscriber._speech_audio_duration(speech_tensors, 16000.0)
                == 3.0
            )
            assert (
                VibeVoiceAsrTranscriber._speech_audio_duration(speech_tensors, None)
                == 2.0
            )
            assert VibeVoiceAsrTranscriber._speech_audio_duration(None, 24000.0) == 0.0

    def test_ensure_loaded_installs_noise_filters_and_logs_effective_params(self):
        from unittest.mock import patch

        with mock_vibevoice_imports():
            from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
                VibeVoiceAsrTranscriber,
            )
            from src.main.python.sheng_wen.transcriber.vibevoice_log_noise import (
                install_vibevoice_log_noise_filters,
            )

            transcriber = VibeVoiceAsrTranscriber(
                model_path="/path/to/model", device="cuda"
            )
            with patch(
                "src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber."
                "install_vibevoice_log_noise_filters",
                wraps=install_vibevoice_log_noise_filters,
            ) as install_spy:
                transcriber._ensure_loaded()
                install_spy.assert_called_once()

            # processor 由 MagicMock 提供 → effective params 行应能安全输出
            assert transcriber.processor is not None
            assert transcriber.model is not None
