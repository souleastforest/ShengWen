"""Tests for transcriber registry and classmethod protocol."""

import pytest

from src.main.python.sheng_wen.transcriber.transcriber import (
    ModelPathValidationResult,
    Transcriber,
    _TRANSCRIBER_REGISTRY,
)


class TestModelPathValidationResult:
    def test_fields(self):
        result = ModelPathValidationResult(
            valid=True,
            message="ok",
            resolved_path="/tmp",
            missing_files=[],
        )
        assert result.valid is True
        assert result.message == "ok"
        assert result.resolved_path == "/tmp"
        assert result.missing_files == []


class TestRegistry:
    def test_fast_whisper_registered_on_import(self):
        """Importing FastWhisperTranscriber should register it."""
        from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import (
            FastWhisperTranscriber,
        )

        assert "fast_whisper" in _TRANSCRIBER_REGISTRY
        assert _TRANSCRIBER_REGISTRY["fast_whisper"] is FastWhisperTranscriber

    def test_get_class_returns_registered_class(self):
        """get_class should return the class for a registered name."""
        cls = Transcriber.get_class("fast_whisper")
        from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import (
            FastWhisperTranscriber,
        )

        assert cls is FastWhisperTranscriber

    def test_get_class_unknown_raises_value_error(self):
        """get_class with unknown name should raise ValueError."""
        with pytest.raises(ValueError, match="未注册"):
            Transcriber.get_class("nonexistent_transcriber")

    def test_base_transcriber_not_registered(self):
        """Transcriber base class (no transcriber_name) should NOT be in registry."""
        assert "" not in _TRANSCRIBER_REGISTRY


class TestDefaultClassmethods:
    def test_default_validate_model_path_accepts_existing_dir(self):
        """Default validate_model_path returns valid for an existing directory."""
        result = Transcriber.validate_model_path("/tmp")
        assert result.valid is True
        assert result.resolved_path == "/tmp"

    def test_default_validate_model_path_rejects_nonexistent(self):
        """Default validate_model_path returns invalid for nonexistent path."""
        result = Transcriber.validate_model_path("/nonexistent/path/abc123")
        assert result.valid is False

    def test_default_required_model_files_returns_empty(self):
        """Default required_model_files returns empty list."""
        assert Transcriber.required_model_files() == []

    def test_default_build_runtime_kwargs_returns_empty(self):
        """Default build_runtime_kwargs returns empty dict."""
        assert Transcriber.build_runtime_kwargs({}) == {}


class TestFastWhisperClassmethods:
    """Tests for FastWhisperTranscriber classmethods."""

    @pytest.fixture()
    def ctranslate2_model_dir(self, tmp_path):
        (tmp_path / "config.json").write_text("{}")
        (tmp_path / "model.bin").write_text("fake")
        (tmp_path / "tokenizer.json").write_text("{}")
        (tmp_path / "vocabulary.txt").write_text("a b c")
        return tmp_path

    @pytest.fixture()
    def ctranslate2_model_dir_vocab_json(self, tmp_path):
        (tmp_path / "config.json").write_text("{}")
        (tmp_path / "model.bin").write_text("fake")
        (tmp_path / "tokenizer.json").write_text("{}")
        (tmp_path / "vocabulary.json").write_text("{}")
        return tmp_path

    def test_validate_accepts_valid_ctranslate2_dir(self, ctranslate2_model_dir):
        from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import (
            FastWhisperTranscriber,
        )

        result = FastWhisperTranscriber.validate_model_path(str(ctranslate2_model_dir))
        assert result.valid is True
        assert result.missing_files == []

    def test_validate_accepts_vocabulary_json_alternative(
        self, ctranslate2_model_dir_vocab_json
    ):
        from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import (
            FastWhisperTranscriber,
        )

        result = FastWhisperTranscriber.validate_model_path(
            str(ctranslate2_model_dir_vocab_json)
        )
        assert result.valid is True

    def test_validate_rejects_missing_files(self, tmp_path):
        from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import (
            FastWhisperTranscriber,
        )

        (tmp_path / "config.json").write_text("{}")
        result = FastWhisperTranscriber.validate_model_path(str(tmp_path))
        assert result.valid is False
        assert "model.bin" in result.missing_files

    def test_validate_rejects_empty_path(self):
        from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import (
            FastWhisperTranscriber,
        )

        result = FastWhisperTranscriber.validate_model_path("")
        assert result.valid is False

    def test_required_model_files(self):
        from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import (
            FastWhisperTranscriber,
        )

        files = FastWhisperTranscriber.required_model_files()
        assert "config.json" in files
        assert "model.bin" in files
        assert "tokenizer.json" in files
        assert "vocabulary.txt" in files

    def test_build_runtime_kwargs_manual_path(self, ctranslate2_model_dir):
        from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import (
            FastWhisperTranscriber,
        )

        state = {
            "device": "cuda",
            "model_source": "manual_path",
            "model_path": str(ctranslate2_model_dir),
            "model_size": "tiny",
        }
        kwargs = FastWhisperTranscriber.build_runtime_kwargs(state)
        assert kwargs["device"] == "cuda"
        assert kwargs["compute_type"] == "int8_float16"
        assert "model_size_or_path" in kwargs

    def test_build_runtime_kwargs_auto_download(self):
        from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import (
            FastWhisperTranscriber,
        )

        state = {
            "device": "cpu",
            "model_source": "auto_download",
            "model_size": "base",
            "model_path": "",
        }
        kwargs = FastWhisperTranscriber.build_runtime_kwargs(state)
        assert kwargs["device"] == "cpu"
        assert kwargs["compute_type"] == "int8"
        assert kwargs["model_size"] == "base"
        assert "model_size_or_path" not in kwargs


class TestVibeVoiceAsrClassmethods:
    """Tests for VibeVoiceAsrTranscriber classmethods."""

    @pytest.fixture()
    def vibevoice_model_dir(self, tmp_path):
        (tmp_path / "config.json").write_text('{"model_type": "vibevoice"}')
        (tmp_path / "model.safetensors").write_text("fake")
        (tmp_path / "preprocessor_config.json").write_text("{}")
        return tmp_path

    def test_validate_accepts_valid_vibevoice_dir(self, vibevoice_model_dir):
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        result = VibeVoiceAsrTranscriber.validate_model_path(str(vibevoice_model_dir))
        assert result.valid is True

    def test_validate_rejects_missing_config(self, tmp_path):
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        (tmp_path / "model.safetensors").write_text("fake")
        result = VibeVoiceAsrTranscriber.validate_model_path(str(tmp_path))
        assert result.valid is False

    def test_required_model_files(self):
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        files = VibeVoiceAsrTranscriber.required_model_files()
        assert "config.json" in files

    def test_build_runtime_kwargs(self, vibevoice_model_dir):
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        state = {
            "model_path": str(vibevoice_model_dir),
            "device": "cuda",
            "vibevoice_language_model": "Qwen/Qwen2.5-7B",
            "vibevoice_max_new_tokens": 4096,
            "vibevoice_dtype": "float16",
        }
        kwargs = VibeVoiceAsrTranscriber.build_runtime_kwargs(state)
        assert kwargs["model_path"] == str(vibevoice_model_dir)
        assert kwargs["device"] == "cuda"
        assert kwargs["language_model_pretrained_name"] == "Qwen/Qwen2.5-7B"
        assert kwargs["max_new_tokens"] == 4096
        assert kwargs["dtype"] == "float16"

    def test_build_runtime_kwargs_defaults(self, vibevoice_model_dir):
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        state = {"model_path": str(vibevoice_model_dir)}
        kwargs = VibeVoiceAsrTranscriber.build_runtime_kwargs(state)
        assert kwargs["device"] == "cuda"
        assert kwargs["max_new_tokens"] == 16384
        assert kwargs["dtype"] == "bfloat16"


class TestVibeVoiceApiClassmethods:
    """Tests for VibeVoiceApiTranscriber classmethods."""

    def test_validate_uses_default_accepts_dir(self, tmp_path):
        from src.main.python.sheng_wen.transcriber.vibe_voice_api_transcriber import (
            VibeVoiceApiTranscriber,
        )

        result = VibeVoiceApiTranscriber.validate_model_path(str(tmp_path))
        assert result.valid is True

    def test_required_model_files_returns_empty(self):
        from src.main.python.sheng_wen.transcriber.vibe_voice_api_transcriber import (
            VibeVoiceApiTranscriber,
        )

        assert VibeVoiceApiTranscriber.required_model_files() == []

    def test_build_runtime_kwargs(self):
        from src.main.python.sheng_wen.transcriber.vibe_voice_api_transcriber import (
            VibeVoiceApiTranscriber,
        )

        state = {
            "vibevoice_api_url": "http://localhost:8000",
            "vibevoice_max_new_tokens": 4096,
        }
        kwargs = VibeVoiceApiTranscriber.build_runtime_kwargs(state)
        assert kwargs["api_url"] == "http://localhost:8000"
        assert kwargs["max_new_tokens"] == 4096

    def test_build_runtime_kwargs_defaults(self):
        from src.main.python.sheng_wen.transcriber.vibe_voice_api_transcriber import (
            VibeVoiceApiTranscriber,
        )

        state = {}
        kwargs = VibeVoiceApiTranscriber.build_runtime_kwargs(state)
        assert kwargs["api_url"] == ""
        assert kwargs["max_new_tokens"] == 16384
