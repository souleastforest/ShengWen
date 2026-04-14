"""VibeVoice ASR model path validation."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

try:
    from loguru import logger
except ImportError:
    logger = logging.getLogger(__name__)


# Required files for VibeVoice models
REQUIRED_VIBEVOICE_FILES = {
    "config.json",
}

# Model weight files (at least one required)
MODEL_WEIGHT_FILES = {
    "pytorch_model.bin",
    "model.safetensors",
    "model.safetensors.index.json",
    "pytorch_model.bin.index.json",
}

# Processor configuration
PROCESSOR_CONFIG_FILE = "preprocessor_config.json"


@dataclass
class VibeVoiceValidationResult:
    """Result of VibeVoice model validation."""

    valid: bool
    message: str
    resolved_path: str
    missing_files: list[str]
    has_processor_config: bool
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary for API responses."""
        return {
            "valid": self.valid,
            "message": self.message,
            "resolved_path": self.resolved_path,
            "missing_files": self.missing_files,
            "has_processor_config": self.has_processor_config,
            "details": self.details,
        }


def validate_vibevoice_model_path(model_path: str) -> VibeVoiceValidationResult:
    """
    Validate a VibeVoice model directory path.

    Checks for:
    - Directory exists and is readable
    - Required config.json present
    - Model weights (pytorch_model.bin or model.safetensors) present
    - Preprocessor config (optional but recommended)

    Args:
        model_path: Path to the model directory

    Returns:
        VibeVoiceValidationResult with validation details
    """
    resolved = _sanitize_model_path(model_path)

    if not resolved:
        return VibeVoiceValidationResult(
            valid=False,
            message="请填写本地模型目录路径。",
            resolved_path="",
            missing_files=[],
            has_processor_config=False,
            details={"error": "empty_path"},
        )

    abs_path = _resolve_absolute_path(resolved)

    # Check directory exists
    if not os.path.exists(abs_path):
        return VibeVoiceValidationResult(
            valid=False,
            message=f"模型目录不存在: {abs_path}",
            resolved_path=abs_path,
            missing_files=[],
            has_processor_config=False,
            details={"error": "directory_not_found"},
        )

    if not os.path.isdir(abs_path):
        return VibeVoiceValidationResult(
            valid=False,
            message=f"模型路径不是目录: {abs_path}",
            resolved_path=abs_path,
            missing_files=[],
            has_processor_config=False,
            details={"error": "not_a_directory"},
        )

    # Check for required files
    missing_required = _check_missing_files(abs_path, REQUIRED_VIBEVOICE_FILES)

    # Check for model weights (at least one required)
    has_model_weights = _has_any_file(abs_path, MODEL_WEIGHT_FILES)
    missing_weights = list(MODEL_WEIGHT_FILES) if not has_model_weights else []

    # Check for processor config (optional but recommended)
    has_processor_config = os.path.isfile(os.path.join(abs_path, PROCESSOR_CONFIG_FILE))

    # Collect all missing files
    all_missing = missing_required + missing_weights

    if all_missing:
        return VibeVoiceValidationResult(
            valid=False,
            message="模型目录缺少必要文件: " + ", ".join(all_missing),
            resolved_path=abs_path,
            missing_files=all_missing,
            has_processor_config=has_processor_config,
            details={
                "error": "missing_files",
                "missing_required": missing_required,
                "missing_weights": missing_weights,
            },
        )

    # Build success message
    details: dict[str, Any] = {
        "config_present": True,
        "model_weights_present": True,
        "processor_config_present": has_processor_config,
    }

    if not has_processor_config:
        message = (
            "模型目录校验通过，但缺少 preprocessor_config.json（处理器配置）。"
            "模型可能仍可加载，但建议检查模型完整性。"
        )
        details["warning"] = "missing_processor_config"
    else:
        message = "模型目录校验通过。"

    logger.info(f"[VibeVoiceModelValidator] {message} Path: {abs_path}")

    return VibeVoiceValidationResult(
        valid=True,
        message=message,
        resolved_path=abs_path,
        missing_files=[],
        has_processor_config=has_processor_config,
        details=details,
    )


def perform_lightweight_load_test(model_path: str) -> VibeVoiceValidationResult:
    """
    Perform a lightweight validation by attempting to load the model config.

    This is more thorough than file checking but doesn't load full model weights.

    Args:
        model_path: Path to the model directory

    Returns:
        VibeVoiceValidationResult with validation details
    """
    # First do basic file validation
    result = validate_vibevoice_model_path(model_path)

    if not result.valid:
        return result

    abs_path = result.resolved_path

    try:
        # Try to load and parse config.json to verify it's valid JSON
        import json

        config_path = os.path.join(abs_path, "config.json")
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        # Basic sanity checks on config
        if not isinstance(config, dict):
            return VibeVoiceValidationResult(
                valid=False,
                message="config.json 格式无效：不是有效的 JSON 对象",
                resolved_path=abs_path,
                missing_files=[],
                has_processor_config=result.has_processor_config,
                details={"error": "invalid_config_format"},
            )

        # Check for expected VibeVoice config keys
        model_type = config.get("model_type", "").lower()
        arch = config.get("architectures", [])

        result.details["config_loaded"] = True
        result.details["model_type"] = model_type
        result.details["architectures"] = arch

        # Log architecture info for debugging
        if arch:
            logger.info(f"[VibeVoiceModelValidator] Detected architectures: {arch}")

        return result

    except json.JSONDecodeError as e:
        return VibeVoiceValidationResult(
            valid=False,
            message=f"config.json 解析失败: {e}",
            resolved_path=abs_path,
            missing_files=[],
            has_processor_config=result.has_processor_config,
            details={"error": "json_parse_error", "parse_error": str(e)},
        )
    except Exception as e:
        return VibeVoiceValidationResult(
            valid=False,
            message=f"加载模型配置时出错: {e}",
            resolved_path=abs_path,
            missing_files=[],
            has_processor_config=result.has_processor_config,
            details={"error": "config_load_error", "exception": str(e)},
        )


def _sanitize_model_path(value: str | None) -> str:
    """Sanitize model path input."""
    return str(value or "").strip().strip('"')


def _resolve_absolute_path(resolved: str) -> str:
    """Resolve path to absolute path with expanded variables."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(resolved)))


def _check_missing_files(directory: str, required_files: set[str]) -> list[str]:
    """Check which required files are missing from the directory."""
    return [
        name
        for name in required_files
        if not os.path.isfile(os.path.join(directory, name))
    ]


def _has_any_file(directory: str, possible_files: set[str]) -> bool:
    """Check if directory contains at least one of the possible files."""
    return any(os.path.isfile(os.path.join(directory, name)) for name in possible_files)
