"""Tests for transcriber registry and classmethod protocol."""

import os

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
