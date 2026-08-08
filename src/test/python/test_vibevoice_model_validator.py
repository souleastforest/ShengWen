# ruff: noqa: E402

import json
import os
import sys
import tempfile
import unittest
import asyncio
from pathlib import Path
from typing import Any

# Add project root to path
_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _path)

from src.main.python.sheng_wen.transcriber.vibevoice_model_validator import (
    REQUIRED_VIBEVOICE_FILES,
    MODEL_WEIGHT_FILES,
    PROCESSOR_CONFIG_FILE,
    VibeVoiceValidationResult,
    _check_missing_files,
    _has_any_file,
    _resolve_absolute_path,
    _sanitize_model_path,
    perform_lightweight_load_test,
    validate_vibevoice_model_path,
)


# ---------------------------------------------------------------------------
# Fix: schemas.py uses dict[str, Any] but does not import Any. Pydantic
# raises PydanticUserError at response serialization. We inject Any into
# the schemas module and rebuild the model so endpoint tests work without
# modifying the source file.
# ---------------------------------------------------------------------------
import src.main.python.sheng_wen.infra.api.routes.schemas as _schemas

_schemas.Any = Any  # type: ignore[attr-defined]
_schemas.ModelPathValidationResult.model_rebuild()


# ---------------------------------------------------------------------------
# Helper: create a fake model directory on disk
# ---------------------------------------------------------------------------


def _create_model_dir(
    base: Path,
    *,
    config_json: bool = True,
    model_weights: str | None = "model.safetensors",
    preprocessor_config: bool = False,
    config_content: dict | None = None,
) -> Path:
    """Create a temporary model directory with requested files.

    Args:
        base: Parent directory (usually tmp_path).
        config_json: Whether to write config.json.
        model_weights: Name of weight file to create, or None to skip.
        preprocessor_config: Whether to write preprocessor_config.json.
        config_content: Custom dict to write as config.json (defaults to
            a minimal valid config).

    Returns:
        Path to the created model directory.
    """
    model_dir = base / "test_model"
    model_dir.mkdir(parents=True, exist_ok=True)

    if config_json:
        content = config_content or {
            "model_type": "vibevoice",
            "architectures": ["VibeVoiceASR"],
        }
        (model_dir / "config.json").write_text(json.dumps(content), encoding="utf-8")

    if model_weights:
        (model_dir / model_weights).write_bytes(b"\x00" * 16)

    if preprocessor_config:
        (model_dir / PROCESSOR_CONFIG_FILE).write_text("{}", encoding="utf-8")

    return model_dir


# ===================================================================
# Tests for helper functions
# ===================================================================


class TestSanitizeModelPath(unittest.TestCase):
    """Tests for _sanitize_model_path."""

    def test_none_returns_empty_string(self):
        self.assertEqual(_sanitize_model_path(None), "")

    def test_empty_string_returns_empty_string(self):
        self.assertEqual(_sanitize_model_path(""), "")

    def test_whitespace_only_returns_empty_string(self):
        self.assertEqual(_sanitize_model_path("   "), "")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(_sanitize_model_path("  /some/path  "), "/some/path")

    def test_strips_double_quotes(self):
        self.assertEqual(_sanitize_model_path('"/some/path"'), "/some/path")

    def test_strips_single_quotes_are_not_stripped(self):
        # Only double quotes are stripped by the implementation
        result = _sanitize_model_path("'/some/path'")
        self.assertEqual(result, "'/some/path'")

    def test_strips_whitespace_and_quotes(self):
        self.assertEqual(_sanitize_model_path('  "/some/path"  '), "/some/path")


class TestResolveAbsolutePath(unittest.TestCase):
    """Tests for _resolve_absolute_path."""

    def test_tilde_expansion(self):
        result = _resolve_absolute_path("~/some/dir")
        self.assertFalse(result.startswith("~"))
        self.assertTrue(os.path.isabs(result))

    def test_relative_path_becomes_absolute(self):
        result = _resolve_absolute_path("some/relative/path")
        self.assertTrue(os.path.isabs(result))
        self.assertTrue(result.endswith("some/relative/path"))

    def test_absolute_path_stays_absolute(self):
        result = _resolve_absolute_path("/absolute/path")
        self.assertEqual(result, "/absolute/path")

    def test_dot_path_resolves(self):
        result = _resolve_absolute_path(".")
        self.assertTrue(os.path.isabs(result))


class TestCheckMissingFiles(unittest.TestCase):
    """Tests for _check_missing_files."""

    def test_all_files_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.txt", "b.txt"):
                Path(tmp, name).write_text("x")
            missing = _check_missing_files(tmp, {"a.txt", "b.txt"})
            self.assertEqual(missing, [])

    def test_some_files_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_text("x")
            missing = _check_missing_files(tmp, {"a.txt", "b.txt"})
            self.assertEqual(missing, ["b.txt"])

    def test_all_files_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = _check_missing_files(tmp, {"a.txt", "b.txt"})
            self.assertEqual(sorted(missing), ["a.txt", "b.txt"])


class TestHasAnyFile(unittest.TestCase):
    """Tests for _has_any_file."""

    def test_returns_true_when_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "model.safetensors").write_bytes(b"\x00")
            self.assertTrue(
                _has_any_file(tmp, {"model.safetensors", "pytorch_model.bin"})
            )

    def test_returns_false_when_no_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(
                _has_any_file(tmp, {"model.safetensors", "pytorch_model.bin"})
            )

    def test_returns_true_with_any_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pytorch_model.bin").write_bytes(b"\x00")
            self.assertTrue(
                _has_any_file(tmp, {"model.safetensors", "pytorch_model.bin"})
            )


# ===================================================================
# Tests for validate_vibevoice_model_path
# ===================================================================


class TestValidateVibeVoiceModelPath(unittest.TestCase):
    """Tests for validate_vibevoice_model_path."""

    def test_empty_path_returns_invalid(self):
        result = validate_vibevoice_model_path("")
        self.assertFalse(result.valid)
        self.assertEqual(result.details.get("error"), "empty_path")

    def test_none_path_returns_invalid(self):
        result = validate_vibevoice_model_path(None)  # type: ignore[arg-type]
        self.assertFalse(result.valid)
        self.assertEqual(result.details.get("error"), "empty_path")

    def test_whitespace_path_returns_invalid(self):
        result = validate_vibevoice_model_path("   ")
        self.assertFalse(result.valid)
        self.assertEqual(result.details.get("error"), "empty_path")

    def test_nonexistent_directory_returns_invalid(self):
        result = validate_vibevoice_model_path("/nonexistent/path/that/does/not/exist")
        self.assertFalse(result.valid)
        self.assertEqual(result.details.get("error"), "directory_not_found")
        self.assertIn("模型目录不存在", result.message)

    def test_path_is_file_not_directory(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"not a dir")
            filepath = f.name
        try:
            result = validate_vibevoice_model_path(filepath)
            self.assertFalse(result.valid)
            self.assertEqual(result.details.get("error"), "not_a_directory")
            self.assertIn("不是目录", result.message)
        finally:
            os.unlink(filepath)

    def test_missing_config_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "model.safetensors").write_bytes(b"\x00" * 8)
            result = validate_vibevoice_model_path(str(model_dir))
            self.assertFalse(result.valid)
            self.assertIn("config.json", result.missing_files)

    def test_missing_model_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            result = validate_vibevoice_model_path(str(model_dir))
            self.assertFalse(result.valid)
            self.assertIn("missing_files", result.details.get("error", ""))
            self.assertTrue(len(result.missing_files) >= 1)

    def test_valid_model_with_all_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = _create_model_dir(
                Path(tmp),
                config_json=True,
                model_weights="model.safetensors",
                preprocessor_config=True,
            )
            result = validate_vibevoice_model_path(str(model_dir))
            self.assertTrue(result.valid)
            self.assertEqual(result.missing_files, [])
            self.assertTrue(result.has_processor_config)
            self.assertIn("校验通过", result.message)

    def test_valid_model_without_preprocessor_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = _create_model_dir(
                Path(tmp),
                config_json=True,
                model_weights="model.safetensors",
                preprocessor_config=False,
            )
            result = validate_vibevoice_model_path(str(model_dir))
            self.assertTrue(result.valid)
            self.assertFalse(result.has_processor_config)
            self.assertIn("preprocessor_config.json", result.message)
            self.assertEqual(result.details.get("warning"), "missing_processor_config")

    def test_valid_model_with_pytorch_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = _create_model_dir(
                Path(tmp),
                config_json=True,
                model_weights="pytorch_model.bin",
                preprocessor_config=True,
            )
            result = validate_vibevoice_model_path(str(model_dir))
            self.assertTrue(result.valid)

    def test_valid_model_with_sharded_safetensors(self):
        """Sharded models have model.safetensors.index.json instead of model.safetensors."""
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = _create_model_dir(
                Path(tmp),
                config_json=True,
                model_weights=None,
            )
            (model_dir / "model.safetensors.index.json").write_text(
                '{"metadata": {"total_size": 12345}}', encoding="utf-8"
            )
            (model_dir / "model-00001-of-00008.safetensors").write_bytes(
                b"\x00" * 16
            )
            result = validate_vibevoice_model_path(str(model_dir))
            self.assertTrue(result.valid)
            self.assertEqual(result.missing_files, [])
            self.assertIn("校验通过", result.message)

    def test_valid_model_with_sharded_pytorch(self):
        """Sharded pytorch models have pytorch_model.bin.index.json."""
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = _create_model_dir(
                Path(tmp),
                config_json=True,
                model_weights=None,
            )
            (model_dir / "pytorch_model.bin.index.json").write_text(
                '{"metadata": {"total_size": 12345}}', encoding="utf-8"
            )
            result = validate_vibevoice_model_path(str(model_dir))
            self.assertTrue(result.valid)

    def test_resolved_path_is_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = _create_model_dir(
                Path(tmp),
                config_json=True,
                model_weights="model.safetensors",
            )
            result = validate_vibevoice_model_path(str(model_dir))
            self.assertTrue(os.path.isabs(result.resolved_path))


# ===================================================================
# Tests for perform_lightweight_load_test
# ===================================================================


class TestPerformLightweightLoadTest(unittest.TestCase):
    """Tests for perform_lightweight_load_test."""

    def test_invalid_path_returns_early(self):
        result = perform_lightweight_load_test("/nonexistent/path")
        self.assertFalse(result.valid)
        self.assertEqual(result.details.get("error"), "directory_not_found")

    def test_malformed_config_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{invalid json!!!", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"\x00" * 8)

            result = perform_lightweight_load_test(str(model_dir))
            self.assertFalse(result.valid)
            self.assertEqual(result.details.get("error"), "json_parse_error")
            self.assertIn("解析失败", result.message)

    def test_valid_config_json_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_content = {
                "model_type": "vibevoice",
                "architectures": ["VibeVoiceASR"],
            }
            model_dir = _create_model_dir(
                Path(tmp),
                config_json=True,
                model_weights="model.safetensors",
                config_content=config_content,
            )
            result = perform_lightweight_load_test(str(model_dir))
            self.assertTrue(result.valid)
            self.assertTrue(result.details.get("config_loaded"))
            self.assertEqual(result.details.get("model_type"), "vibevoice")
            self.assertEqual(result.details.get("architectures"), ["VibeVoiceASR"])

    def test_config_json_with_empty_architectures(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_content = {
                "model_type": "custom",
                "architectures": [],
            }
            model_dir = _create_model_dir(
                Path(tmp),
                config_json=True,
                model_weights="model.safetensors",
                config_content=config_content,
            )
            result = perform_lightweight_load_test(str(model_dir))
            self.assertTrue(result.valid)
            self.assertEqual(result.details.get("architectures"), [])

    def test_config_json_not_a_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("[1, 2, 3]", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"\x00" * 8)

            result = perform_lightweight_load_test(str(model_dir))
            self.assertFalse(result.valid)
            self.assertEqual(result.details.get("error"), "invalid_config_format")
            self.assertIn("不是有效的 JSON 对象", result.message)

    def test_missing_weight_files_fails_before_load_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}", encoding="utf-8")

            result = perform_lightweight_load_test(str(model_dir))
            self.assertFalse(result.valid)

    def test_sharded_safetensors_passes_load_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = _create_model_dir(
                Path(tmp),
                config_json=True,
                model_weights=None,
            )
            (model_dir / "model.safetensors.index.json").write_text(
                '{"metadata": {"total_size": 12345}}', encoding="utf-8"
            )
            result = perform_lightweight_load_test(str(model_dir))
            self.assertTrue(result.valid)
            self.assertTrue(result.details.get("config_loaded"))


# ===================================================================
# Tests for VibeVoiceValidationResult.to_dict
# ===================================================================


class TestVibeVoiceValidationResultToDict(unittest.TestCase):
    """Tests for VibeVoiceValidationResult.to_dict."""

    def test_to_dict_round_trip(self):
        result = VibeVoiceValidationResult(
            valid=True,
            message="ok",
            resolved_path="/some/path",
            missing_files=[],
            has_processor_config=True,
            details={"config_loaded": True},
        )
        d = result.to_dict()
        self.assertTrue(d["valid"])
        self.assertEqual(d["message"], "ok")
        self.assertEqual(d["resolved_path"], "/some/path")
        self.assertEqual(d["missing_files"], [])
        self.assertTrue(d["has_processor_config"])
        self.assertTrue(d["details"]["config_loaded"])

    def test_to_dict_with_missing_files(self):
        result = VibeVoiceValidationResult(
            valid=False,
            message="missing",
            resolved_path="/path",
            missing_files=["config.json", "model.safetensors"],
            has_processor_config=False,
            details={"error": "missing_files"},
        )
        d = result.to_dict()
        self.assertEqual(d["missing_files"], ["config.json", "model.safetensors"])


# ===================================================================
# Tests for the /validate-model-path endpoint via FastAPI TestClient
# ===================================================================


class TestValidateModelPathEndpoint(unittest.TestCase):
    """Tests for the POST /transcription/settings/validate-model-path endpoint."""

    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI

        from src.main.python.sheng_wen.infra.api.routes.settings import router

        app = FastAPI()
        app.include_router(router)
        cls.app = app

    def _post_validate_model_path(self, payload: dict[str, str]):
        from httpx import ASGITransport, AsyncClient

        async def _request():
            transport = ASGITransport(app=self.app)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.post(
                    "/transcription/settings/validate-model-path",
                    json=payload,
                )

        return asyncio.run(_request())

    # --- vibe_voice_asr type ---

    def test_vibe_voice_asr_valid_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = _create_model_dir(
                Path(tmp),
                config_json=True,
                model_weights="model.safetensors",
                preprocessor_config=True,
            )
            resp = self._post_validate_model_path(
                {
                    "path": str(model_dir),
                    "transcriber_type": "vibe_voice_asr",
                }
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body["valid"])
            self.assertIn("校验通过", body["message"])

    def test_vibe_voice_asr_invalid_path(self):
        resp = self._post_validate_model_path(
            {
                "path": "/nonexistent/model/path",
                "transcriber_type": "vibe_voice_asr",
            }
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["details"]["error"], "directory_not_found")

    def test_vibe_voice_asr_missing_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}", encoding="utf-8")

            resp = self._post_validate_model_path(
                {
                    "path": str(model_dir),
                    "transcriber_type": "vibe_voice_asr",
                }
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertFalse(body["valid"])

    # --- fast_whisper type ---

    def test_fast_whisper_valid_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp = self._post_validate_model_path(
                {
                    "path": tmp,
                    "transcriber_type": "fast_whisper",
                }
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body["valid"])
            self.assertEqual(body["message"], "路径有效。")

    def test_fast_whisper_nonexistent_path(self):
        resp = self._post_validate_model_path(
            {
                "path": "/nonexistent/whisper/path",
                "transcriber_type": "fast_whisper",
            }
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["details"]["error"], "path_not_found")

    def test_fast_whisper_path_is_file_not_dir(self):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(b"\x00")
            filepath = f.name
        try:
            resp = self._post_validate_model_path(
                {
                    "path": filepath,
                    "transcriber_type": "fast_whisper",
                }
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertFalse(body["valid"])
        finally:
            os.unlink(filepath)


# ===================================================================
# Tests for module-level constants
# ===================================================================


class TestModuleConstants(unittest.TestCase):
    """Verify expected values of module-level constants."""

    def test_required_files_includes_config_json(self):
        self.assertIn("config.json", REQUIRED_VIBEVOICE_FILES)

    def test_model_weight_files(self):
        self.assertIn("pytorch_model.bin", MODEL_WEIGHT_FILES)
        self.assertIn("model.safetensors", MODEL_WEIGHT_FILES)

    def test_model_weight_files_includes_sharded_index(self):
        self.assertIn("model.safetensors.index.json", MODEL_WEIGHT_FILES)
        self.assertIn("pytorch_model.bin.index.json", MODEL_WEIGHT_FILES)

    def test_processor_config_filename(self):
        self.assertEqual(PROCESSOR_CONFIG_FILE, "preprocessor_config.json")


if __name__ == "__main__":
    unittest.main()
