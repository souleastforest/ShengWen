# Transcriber Validation & Build Abstraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple transcriber-specific validation, kwargs construction, and model file description from `settings_manager.py` by moving them onto each `Transcriber` subclass as classmethods.

**Architecture:** Add a lightweight registry via `__init_subclass__` + 3 classmethod protocol (`validate_model_path`, `required_model_files`, `build_runtime_kwargs`) to the `Transcriber` ABC. Each concrete transcriber implements its own. `settings_manager.py` dispatches via `Transcriber.get_class(name)` instead of hardcoded if-else.

**Tech Stack:** Python 3.12+, pytest, existing FastAPI + Pydantic stack, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-12-transcriber-validation-abstraction-design.md`

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `src/main/python/sheng_wen/transcriber/transcriber.py` | Modify | Add `ModelPathValidationResult`, `_TRANSCRIBER_REGISTRY`, `__init_subclass__`, `get_class()`, 3 default classmethods |
| `src/main/python/sheng_wen/transcriber/fast_whisper_transcriber.py` | Modify | Add `transcriber_name = "fast_whisper"`, implement 3 classmethods |
| `src/main/python/sheng_wen/transcriber/vibe_voice_asr_transcriber.py` | Modify | Add `transcriber_name = "vibe_voice_asr"`, implement 3 classmethods |
| `src/main/python/sheng_wen/transcriber/vibe_voice_api_transcriber.py` | Modify | Add `transcriber_name = "vibe_voice_api"`, implement `build_runtime_kwargs` |
| `src/main/python/sheng_wen/transcriber/settings_manager.py` | Modify | Delete `REQUIRED_MANUAL_MODEL_FILES`, `_validate_manual_model_dir`, `_build_transcriber_kwargs`; refactor `get_settings()` and `update_settings()` to dispatch via registry |
| `src/main/python/sheng_wen/api.py` | Modify | Simplify `get_transcriber_worker()` to use `cls.build_runtime_kwargs()` |
| `tests/test_transcriber_registry.py` | Create | Tests for registry, classmethod protocol, and concrete implementations |

---

### Task 1: Add registry, ModelPathValidationResult, and protocol classmethods to transcriber.py

**Files:**
- Modify: `src/main/python/sheng_wen/transcriber/transcriber.py`
- Create: `tests/test_transcriber_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transcriber_registry.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcriber_registry.py -v`
Expected: FAIL — `ModelPathValidationResult` and `_TRANSCRIBER_REGISTRY` do not exist yet.

- [ ] **Step 3: Implement registry and protocol in transcriber.py**

First, add `from __future__ import annotations` as the very first line of `transcriber.py` (before all other imports). This is needed because `_TRANSCRIBER_REGISTRY` references `Transcriber` in its type hint before the class is defined.

Then add the following to `src/main/python/sheng_wen/transcriber/transcriber.py`, **after** the existing dataclasses section (after `TranscriptionResult` class) and **before** the `Transcriber` ABC class:

```python
# --- 注册表 ---

_TRANSCRIBER_REGISTRY: dict[str, type[Transcriber]] = {}


@dataclass
class ModelPathValidationResult:
    """模型路径验证结果。"""
    valid: bool
    message: str
    resolved_path: str
    missing_files: list[str]
```

**Note:** This forward-references `Transcriber` in the type hint. Since `_TRANSCRIBER_REGISTRY` is only used at runtime (not at class-definition time), the forward reference is safe. Place it AFTER `TranscriptionResult` and BEFORE `class Transcriber`.

Then modify `class Transcriber(ABC)` to add `__init_subclass__`, `get_class`, and 3 default classmethods:

```python
class Transcriber(ABC):
    """
    语音转文本转录器的抽象基类。
    """

    transcriber_name: str = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        name = getattr(cls, "transcriber_name", "")
        if name:
            _TRANSCRIBER_REGISTRY[name] = cls

    def __init__(self, **kwargs):
        pass

    @staticmethod
    def get_class(name: str) -> type[Transcriber]:
        """获取已注册的转录器类。如果模块未加载，尝试延迟导入。"""
        if name not in _TRANSCRIBER_REGISTRY:
            module_name = f"src.main.python.sheng_wen.transcriber.{name}_transcriber"
            try:
                importlib.import_module(module_name)
            except (ImportError, ModuleNotFoundError):
                raise ValueError(f"未注册的转录器类型: {name}") from None
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
        pass
```

Also add `import os` at the top of the file (it's needed by `validate_model_path`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcriber_registry.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Run existing tests to verify no regressions**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/main/python/sheng_wen/transcriber/transcriber.py tests/test_transcriber_registry.py
git commit -m "feat(transcriber): add registry, ModelPathValidationResult, and classmethod protocol to ABC"
```

---

### Task 2: Add classmethods to FastWhisperTranscriber

**Files:**
- Modify: `src/main/python/sheng_wen/transcriber/fast_whisper_transcriber.py`
- Modify: `tests/test_transcriber_registry.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_transcriber_registry.py`:

```python
class TestFastWhisperClassmethods:
    """Tests for FastWhisperTranscriber classmethods."""

    @pytest.fixture()
    def ctranslate2_model_dir(self, tmp_path):
        """Create a temporary directory mimicking a CTranslate2 model."""
        (tmp_path / "config.json").write_text("{}")
        (tmp_path / "model.bin").write_text("fake")
        (tmp_path / "tokenizer.json").write_text("{}")
        (tmp_path / "vocabulary.txt").write_text("a b c")
        return tmp_path

    @pytest.fixture()
    def ctranslate2_model_dir_vocab_json(self, tmp_path):
        """CTranslate2 model with vocabulary.json instead of vocabulary.txt."""
        (tmp_path / "config.json").write_text("{}")
        (tmp_path / "model.bin").write_text("fake")
        (tmp_path / "tokenizer.json").write_text("{}")
        (tmp_path / "vocabulary.json").write_text("{}")
        return tmp_path

    def test_validate_accepts_valid_ctranslate2_dir(self, ctranslate2_model_dir):
        from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import (
            FastWhisperTranscriber,
        )

        result = FastWhisperTranscriber.validate_model_path(
            str(ctranslate2_model_dir)
        )
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
        # Missing model.bin, tokenizer.json, vocabulary.txt
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
        assert kwargs["model_size_or_path"] == str(ctranslate2_model_dir)

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcriber_registry.py::TestFastWhisperClassmethods -v`
Expected: FAIL — `FastWhisperTranscriber` has no `transcriber_name` or classmethods yet.

- [ ] **Step 3: Implement classmethods on FastWhisperTranscriber**

In `src/main/python/sheng_wen/transcriber/fast_whisper_transcriber.py`, add `transcriber_name` and 3 classmethods to `FastWhisperTranscriber`.

Add `import os` at the top if not already present (it is — line 2).

Then add to the class body, right after `class FastWhisperTranscriber(Transcriber):`:

```python
    transcriber_name = "fast_whisper"

    REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")

    @classmethod
    def validate_model_path(cls, path: str) -> "ModelPathValidationResult":
        from .transcriber import ModelPathValidationResult

        resolved = str(path or "").strip().strip('"')
        if not resolved:
            return ModelPathValidationResult(False, "请填写本地模型目录路径。", "", [])
        abs_path = os.path.abspath(os.path.expanduser(os.path.expandvars(resolved)))
        if not os.path.exists(abs_path):
            return ModelPathValidationResult(
                False, f"模型目录不存在: {abs_path}", abs_path, []
            )
        if not os.path.isdir(abs_path):
            return ModelPathValidationResult(
                False, f"模型路径不是目录: {abs_path}", abs_path, []
            )
        missing = [
            name
            for name in cls.REQUIRED_FILES
            if not os.path.isfile(os.path.join(abs_path, name))
            and not (
                name == "vocabulary.txt"
                and os.path.isfile(os.path.join(abs_path, "vocabulary.json"))
            )
        ]
        if missing:
            return ModelPathValidationResult(
                False,
                "模型目录缺少必要文件: " + ", ".join(missing),
                abs_path,
                missing,
            )
        return ModelPathValidationResult(True, "模型目录校验通过。", abs_path, [])

    @classmethod
    def required_model_files(cls) -> list[str]:
        return list(cls.REQUIRED_FILES)

    @classmethod
    def build_runtime_kwargs(cls, runtime_state: dict) -> dict:
        device = runtime_state.get("device", "cpu")
        kwargs: dict[str, str] = {
            "device": device,
            "compute_type": "int8_float16" if device == "cuda" else "int8",
        }
        if runtime_state.get("model_source") == "manual_path":
            validation = cls.validate_model_path(runtime_state.get("model_path", ""))
            if not validation.valid:
                raise ValueError(validation.message)
            kwargs["model_size_or_path"] = validation.resolved_path
        else:
            kwargs["model_size"] = runtime_state.get("model_size", "tiny")
        return kwargs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcriber_registry.py -v`
Expected: All tests PASS (both Task 1 and Task 2 tests).

- [ ] **Step 5: Run existing tests to verify no regressions**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/main/python/sheng_wen/transcriber/fast_whisper_transcriber.py tests/test_transcriber_registry.py
git commit -m "feat(transcriber): add classmethods to FastWhisperTranscriber"
```

---

### Task 3: Add classmethods to VibeVoiceAsrTranscriber

**Files:**
- Modify: `src/main/python/sheng_wen/transcriber/vibe_voice_asr_transcriber.py`
- Modify: `tests/test_transcriber_registry.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_transcriber_registry.py`:

```python
class TestVibeVoiceAsrClassmethods:
    """Tests for VibeVoiceAsrTranscriber classmethods."""

    @pytest.fixture()
    def vibevoice_model_dir(self, tmp_path):
        """Create a temporary directory mimicking a VibeVoice model."""
        (tmp_path / "config.json").write_text('{"model_type": "vibevoice"}')
        (tmp_path / "model.safetensors").write_text("fake")
        (tmp_path / "preprocessor_config.json").write_text("{}")
        return tmp_path

    def test_validate_accepts_valid_vibevoice_dir(self, vibevoice_model_dir):
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        result = VibeVoiceAsrTranscriber.validate_model_path(
            str(vibevoice_model_dir)
        )
        assert result.valid is True

    def test_validate_rejects_missing_config(self, tmp_path):
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        # Only has model weights, no config.json
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

        state = {
            "model_path": str(vibevoice_model_dir),
        }
        kwargs = VibeVoiceAsrTranscriber.build_runtime_kwargs(state)
        assert kwargs["device"] == "cuda"
        assert kwargs["language_model_pretrained_name"] == "Qwen/Qwen2.5-7B"
        assert kwargs["max_new_tokens"] == 8192
        assert kwargs["dtype"] == "bfloat16"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcriber_registry.py::TestVibeVoiceAsrClassmethods -v`
Expected: FAIL — `VibeVoiceAsrTranscriber` has no classmethods yet.

- [ ] **Step 3: Implement classmethods on VibeVoiceAsrTranscriber**

In `src/main/python/sheng_wen/transcriber/vibe_voice_asr_transcriber.py`, add to the `VibeVoiceAsrTranscriber` class body, right after `class VibeVoiceAsrTranscriber(Transcriber):`:

```python
    transcriber_name = "vibe_voice_asr"

    @classmethod
    def validate_model_path(cls, path: str) -> "ModelPathValidationResult":
        from .transcriber import ModelPathValidationResult

        result = perform_lightweight_load_test(path)
        return ModelPathValidationResult(
            valid=result.valid,
            message=result.message,
            resolved_path=result.resolved_path,
            missing_files=result.missing_files,
        )

    @classmethod
    def required_model_files(cls) -> list[str]:
        return ["config.json"]

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcriber_registry.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/main/python/sheng_wen/transcriber/vibe_voice_asr_transcriber.py tests/test_transcriber_registry.py
git commit -m "feat(transcriber): add classmethods to VibeVoiceAsrTranscriber"
```

---

### Task 4: Add classmethods to VibeVoiceApiTranscriber

**Files:**
- Modify: `src/main/python/sheng_wen/transcriber/vibe_voice_api_transcriber.py`
- Modify: `tests/test_transcriber_registry.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_transcriber_registry.py`:

```python
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
        assert kwargs["max_new_tokens"] == 8192
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcriber_registry.py::TestVibeVoiceApiClassmethods -v`
Expected: FAIL on `build_runtime_kwargs` (no classmethod yet).

- [ ] **Step 3: Implement classmethods on VibeVoiceApiTranscriber**

In `src/main/python/sheng_wen/transcriber/vibe_voice_api_transcriber.py`, add to the `VibeVoiceApiTranscriber` class body, right after `class VibeVoiceApiTranscriber(Transcriber):`:

```python
    transcriber_name = "vibe_voice_api"

    @classmethod
    def build_runtime_kwargs(cls, runtime_state: dict) -> dict:
        return {
            "api_url": runtime_state.get("vibevoice_api_url", ""),
            "max_new_tokens": runtime_state.get("vibevoice_max_new_tokens", 8192),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcriber_registry.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/main/python/sheng_wen/transcriber/vibe_voice_api_transcriber.py tests/test_transcriber_registry.py
git commit -m "feat(transcriber): add classmethods to VibeVoiceApiTranscriber"
```

---

### Task 5: Refactor settings_manager.py

**Files:**
- Modify: `src/main/python/sheng_wen/transcriber/settings_manager.py`

This is the core refactoring task. We delete 3 hardcoded functions and update 2 methods to dispatch via the registry.

- [ ] **Step 1: Delete `REQUIRED_MANUAL_MODEL_FILES` constant**

In `src/main/python/sheng_wen/transcriber/settings_manager.py`, delete lines 227-232:

```python
# DELETE THIS:
REQUIRED_MANUAL_MODEL_FILES = (
    "config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.txt",
)
```

- [ ] **Step 2: Delete `_validate_manual_model_dir()` function**

Delete the entire function (lines 254-280):

```python
# DELETE THIS ENTIRE FUNCTION:
def _validate_manual_model_dir(model_path: str) -> tuple[bool, str, str]:
    ...
```

- [ ] **Step 3: Delete `_build_transcriber_kwargs()` and `build_transcriber_kwargs()` methods**

Delete both methods from `TranscriptionSettingsManager` (lines 414-441):

```python
# DELETE THESE TWO METHODS:
def _build_transcriber_kwargs(self, device, model_source, model_size, model_path):
    ...

def build_transcriber_kwargs(self):
    ...
```

- [ ] **Step 4: Add import for `Transcriber` and `ModelPathValidationResult`**

At the top of `settings_manager.py`, change the existing import:

```python
# FROM:
from .transcriber import get_transcriber

# TO:
from .transcriber import ModelPathValidationResult, Transcriber, get_transcriber
```

- [ ] **Step 5: Update `get_settings()` method**

Replace the validation and `required_model_files` sections in `get_settings()`. The method currently does (lines 369-390):

```python
# OLD CODE — replace this block:
manual_valid, manual_message, manual_resolved_path = _validate_manual_model_dir(
    model_path
)
model_path_valid = manual_valid if model_source == "manual_path" else True
model_path_message = (
    manual_message
    if model_source == "manual_path"
    else "自动下载模式：首次使用会自动下载/加载所选模型。"
)
```

Replace with:

```python
# NEW CODE:
transcriber_cls = Transcriber.get_class(transcriber_type)
if model_source == "manual_path":
    validation = transcriber_cls.validate_model_path(model_path)
    model_path_valid = validation.valid
    model_path_message = validation.message
    model_path_resolved = validation.resolved_path
else:
    model_path_valid = True
    model_path_message = "自动下载模式：首次使用会自动下载/加载所选模型。"
    model_path_resolved = ""
```

Also update the return dict. Currently line 387-390:

```python
# OLD:
"model_path_resolved": manual_resolved_path
if model_source == "manual_path"
else "",
"required_model_files": list(REQUIRED_MANUAL_MODEL_FILES),
```

Replace with:

```python
# NEW:
"model_path_resolved": model_path_resolved,
"required_model_files": transcriber_cls.required_model_files(),
```

- [ ] **Step 6: Update `update_settings()` method**

**6a.** Add `current_transcriber_type` to the lock-protected read section. Currently lines 478-483:

```python
# OLD:
with self._lock:
    current_device = self._device
    current_model_source = self._model_source
    current_model_size = self._model_size
    current_model_path = self._model_path
    worker_for_rebuild = self._transcriber_worker
```

Replace with:

```python
# NEW:
with self._lock:
    current_device = self._device
    current_model_source = self._model_source
    current_model_size = self._model_size
    current_model_path = self._model_path
    current_transcriber_type = self._transcriber_type
    worker_for_rebuild = self._transcriber_worker
```

**6b.** Compute `next_transcriber_type` before validation. Add after the `next_model_path` computation (after line 507):

```python
        next_transcriber_type = current_transcriber_type
        if transcriber_type is not None:
            t_type = str(transcriber_type or "fast_whisper").strip().lower()
            if t_type in {"fast_whisper", "vibe_voice_asr"}:
                next_transcriber_type = t_type
```

**6c.** Replace validation block. Currently lines 514-517:

```python
# OLD:
if next_model_source == "manual_path":
    valid, message, _ = _validate_manual_model_dir(next_model_path)
    if not valid:
        raise ValueError(message)
```

Replace with:

```python
# NEW:
if next_model_source == "manual_path":
    transcriber_cls = Transcriber.get_class(next_transcriber_type)
    validation = transcriber_cls.validate_model_path(next_model_path)
    if not validation.valid:
        raise ValueError(validation.message)
```

**6d.** Add `transcriber_type_changed` to rebuild condition. Currently lines 519-528:

```python
# OLD:
device_changed = next_device != current_device
model_source_changed = next_model_source != current_model_source
model_size_changed = next_model_size != current_model_size
model_path_changed = next_model_path != current_model_path
should_rebuild = bool(worker_for_rebuild) and (
    device_changed
    or model_source_changed
    or model_size_changed
    or model_path_changed
)
```

Replace with:

```python
# NEW:
device_changed = next_device != current_device
model_source_changed = next_model_source != current_model_source
model_size_changed = next_model_size != current_model_size
model_path_changed = next_model_path != current_model_path
transcriber_type_changed = next_transcriber_type != current_transcriber_type
should_rebuild = bool(worker_for_rebuild) and (
    device_changed
    or model_source_changed
    or model_size_changed
    or model_path_changed
    or transcriber_type_changed
)
```

**6e.** Replace rebuild block. Currently lines 530-545:

```python
# OLD:
transcriber = None
if should_rebuild:
    try:
        logger.info(
            "[TranscriptionSettingsManager] 正在重建转录器实例，若模型未缓存可能会触发下载，请稍候..."
        )
        transcriber_kwargs = self._build_transcriber_kwargs(
            device=next_device,
            model_source=next_model_source,
            model_size=next_model_size,
            model_path=next_model_path,
        )
        transcriber = get_transcriber("fast_whisper", **transcriber_kwargs)
        logger.info("[TranscriptionSettingsManager] 转录器实例重建完成。")
    except Exception as e:
        raise ValueError(f"切换转录配置失败: {e}") from e
```

Replace with:

```python
# NEW:
transcriber = None
if should_rebuild:
    try:
        logger.info(
            "[TranscriptionSettingsManager] 正在重建转录器实例，若模型未缓存可能会触发下载，请稍候..."
        )
        rebuild_cls = Transcriber.get_class(next_transcriber_type)
        runtime_state = {
            "device": next_device,
            "model_source": next_model_source,
            "model_size": next_model_size,
            "model_path": next_model_path,
            "transcriber_type": next_transcriber_type,
            "vibevoice_language_model": self._vibevoice_language_model,
            "vibevoice_max_new_tokens": self._vibevoice_max_new_tokens,
            "vibevoice_dtype": self._vibevoice_dtype,
            "vibevoice_inference_mode": self._vibevoice_inference_mode,
            "vibevoice_api_url": self._vibevoice_api_url,
        }
        transcriber_kwargs = rebuild_cls.build_runtime_kwargs(runtime_state)
        transcriber = get_transcriber(next_transcriber_type, **transcriber_kwargs)
        logger.info("[TranscriptionSettingsManager] 转录器实例重建完成。")
    except Exception as e:
        raise ValueError(f"切换转录配置失败: {e}") from e
```

**6f.** Update the transcriber_type update block inside the lock. Currently lines 590-596:

```python
# OLD:
if transcriber_type is not None:
    t_type = str(transcriber_type or "fast_whisper").strip().lower()
    if t_type in {"fast_whisper", "vibe_voice_asr"}:
        self._transcriber_type = t_type
        logger.info(
            f"[TranscriptionSettingsManager] 已更新转录器类型: {self._transcriber_type}"
        )
```

Replace with:

```python
# NEW:
if transcriber_type is not None:
    t_type = str(transcriber_type or "fast_whisper").strip().lower()
    if t_type in {"fast_whisper", "vibe_voice_asr"}:
        self._transcriber_type = t_type
        logger.info(
            f"[TranscriptionSettingsManager] 已更新转录器类型: {self._transcriber_type}"
        )
    else:
        logger.warning(
            f"[TranscriptionSettingsManager] 未知的转录器类型: {t_type}，忽略更新"
        )
```

- [ ] **Step 7: Run all tests to verify no regressions**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All tests pass.

- [ ] **Step 8: Lint**

Run: `./scripts/lint.sh src/main/python/sheng_wen/transcriber/settings_manager.py`
Expected: No errors.

- [ ] **Step 9: Commit**

```bash
git add src/main/python/sheng_wen/transcriber/settings_manager.py
git commit -m "refactor(settings): dispatch validation and kwargs via transcriber registry"
```

---

### Task 6: Refactor api.py to use build_runtime_kwargs

**Files:**
- Modify: `src/main/python/sheng_wen/api.py`

- [ ] **Step 1: Update `get_transcriber_worker()` to use registry**

The current `get_transcriber_worker()` (lines 138-204) has hand-crafted kwargs for each transcriber type. Replace the entire function body from the `if transcriber_type == "vibe_voice_asr":` block onward.

Currently (lines 150-196):

```python
    if transcriber_type == "vibe_voice_asr":
        inference_mode = str(
            runtime_transcription_state.get("vibevoice_inference_mode", "local")
        )
        if inference_mode == "api":
            api_url = str(runtime_transcription_state.get("vibevoice_api_url", "")).strip()
            if not api_url:
                raise ValueError("API 推理模式需要填写推理服务地址")
            logger.info(f"[Transcriber] VibeVoice API 模式, URL: {api_url}")
            transcriber_config = {
                "api_url": api_url,
                "max_new_tokens": runtime_transcription_state.get(
                    "vibevoice_max_new_tokens", 8192
                ),
            }
            from .transcriber.vibe_voice_api_transcriber import VibeVoiceApiTranscriber
            transcriber = VibeVoiceApiTranscriber(**transcriber_config)
        else:
            model_path = str(runtime_transcription_state.get("model_path") or "")
            transcriber_config = {
                "model_path": model_path,
                "device": runtime_transcription_state.get("device", "cuda"),
                "language_model_pretrained_name": runtime_transcription_state.get(
                    "vibevoice_language_model", "Qwen/Qwen2.5-7B"
                ),
                "max_new_tokens": runtime_transcription_state.get(
                    "vibevoice_max_new_tokens", 8192
                ),
                "dtype": runtime_transcription_state.get("vibevoice_dtype", "bfloat16"),
            }
            logger.info(f"[Transcriber] VibeVoice-ASR 本地模式, 路径: {model_path}")
            transcriber = get_transcriber("vibe_voice_asr", **transcriber_config)
    else:
        transcriber_config = transcription_settings_manager.build_transcriber_kwargs()
        model_source = str(
            runtime_transcription_state.get("model_source") or "auto_download"
        )
        if model_source == "manual_path":
            logger.info(
                f"[Transcriber] 使用本地模型路径: {transcriber_config.get('model_size_or_path')}"
            )
        else:
            logger.info(
                f"[Transcriber] 使用模型大小: {transcriber_config.get('model_size')}"
            )
        transcriber = get_transcriber(transcriber_type, **transcriber_config)
```

Replace the entire block above (lines 150-196) with:

```python
    # Determine effective transcriber type (resolve vibe_voice_asr + inference_mode)
    effective_type = transcriber_type
    if transcriber_type == "vibe_voice_asr":
        inference_mode = str(
            runtime_transcription_state.get("vibevoice_inference_mode", "local")
        )
        if inference_mode == "api":
            api_url = str(
                runtime_transcription_state.get("vibevoice_api_url", "")
            ).strip()
            if not api_url:
                raise ValueError("API 推理模式需要填写推理服务地址")
            effective_type = "vibe_voice_api"
            logger.info(f"[Transcriber] VibeVoice API 模式, URL: {api_url}")
        else:
            model_path = str(runtime_transcription_state.get("model_path") or "")
            logger.info(f"[Transcriber] VibeVoice-ASR 本地模式, 路径: {model_path}")

    # Build kwargs via transcriber classmethod and create instance
    from .transcriber.transcriber import Transcriber as TranscriberABC

    transcriber_cls = TranscriberABC.get_class(effective_type)
    transcriber_config = transcriber_cls.build_runtime_kwargs(
        runtime_transcription_state
    )

    model_source = str(
        runtime_transcription_state.get("model_source") or "auto_download"
    )
    if model_source == "manual_path":
        logger.info(
            f"[Transcriber] 使用本地模型路径: {transcriber_config.get('model_size_or_path')}"
        )
    else:
        logger.info(
            f"[Transcriber] 使用模型大小: {transcriber_config.get('model_size')}"
        )

    transcriber = get_transcriber(effective_type, **transcriber_config)
```

Also remove the now-unused import of `VibeVoiceApiTranscriber` if it was added inside the function (the old code had `from .transcriber.vibe_voice_api_transcriber import VibeVoiceApiTranscriber`). The new code uses `get_transcriber()` instead.

- [ ] **Step 2: Run all tests to verify no regressions**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All tests pass.

- [ ] **Step 3: Lint**

Run: `./scripts/lint.sh src/main/python/sheng_wen/api.py`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add src/main/python/sheng_wen/api.py
git commit -m "refactor(api): simplify get_transcriber_worker using build_runtime_kwargs"
```

---

### Task 7: Final verification and changelog

**Files:**
- Modify: `docs/version/` (changelog entry, per project convention)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All tests pass.

- [ ] **Step 2: Run linter on all changed files**

Run: `./scripts/lint.sh src/main/python/sheng_wen/transcriber/ src/main/python/sheng_wen/api.py tests/test_transcriber_registry.py`
Expected: No errors.

- [ ] **Step 3: Verify the VibeVoice ASR model path validation works**

Run: `uv run python -c "
from src.main.python.sheng_wen.transcriber.transcriber import Transcriber
cls = Transcriber.get_class('vibe_voice_asr')
result = cls.validate_model_path(os.path.expanduser('~/projects/huggingface/models/VibeVoice-ASR'))
print(f'valid={result.valid}, message={result.message}, resolved={result.resolved_path}')
"`
Expected: `valid=True`

- [ ] **Step 4: Verify FastWhisper validation still works**

Run: `uv run python -c "
from src.main.python.sheng_wen.transcriber.transcriber import Transcriber
cls = Transcriber.get_class('fast_whisper')
files = cls.required_model_files()
print(f'FastWhisper files: {files}')
result = cls.validate_model_path('/nonexistent')
print(f'valid={result.valid}, missing={result.missing_files}')
"`
Expected: `valid=False` for nonexistent path.

- [ ] **Step 5: Add changelog entry**

Create or update the version changelog file at `docs/version/` following the project's branch-based naming convention. Add an entry for the `feature/add-vibevoice-transcriber` branch describing the transcriber validation abstraction refactor.

- [ ] **Step 6: Final commit**

```bash
git add docs/version/
git commit -m "docs: add changelog entry for transcriber validation abstraction"
```
