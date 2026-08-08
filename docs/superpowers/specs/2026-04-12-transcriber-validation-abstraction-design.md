# Transcriber Validation & Build Abstraction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple transcriber-specific validation, kwargs construction, and model file description from `settings_manager.py` by moving them onto each `Transcriber` subclass as classmethods.

**Architecture:** Add a lightweight registry + 3 classmethod protocol to the `Transcriber` ABC. Each concrete transcriber implements its own `validate_model_path()`, `required_model_files()`, and `build_runtime_kwargs()`. `settings_manager.py` dispatches via the registry instead of hardcoded if-else.

**Tech Stack:** Python 3.12+, existing FastAPI + Pydantic stack, no new dependencies.

---

## Background

`settings_manager.py` (702 lines) currently contains hardcoded logic for different transcriber types in 4 locations:

1. `get_settings()` L369-391 — Path validation fixed to `_validate_manual_model_dir` (CTranslate2 format), returns `REQUIRED_MANUAL_MODEL_FILES`
2. `update_settings()` L514-517 — `manual_path` always validates via CTranslate2 file check
3. `_build_transcriber_kwargs()` L414-432 — Builds kwargs only for `fast_whisper`
4. `update_settings()` L530-545 — `should_rebuild` always creates `get_transcriber("fast_whisper")`

This causes bugs when using VibeVoice ASR: the settings API rejects valid HuggingFace model paths because it checks for CTranslate2 files (`model.bin`).

## Design

### 1. Transcriber Registry & Protocol

**File:** `src/main/python/sheng_wen/transcriber/transcriber.py`

Add a module-level registry and `ModelPathValidationResult` dataclass:

```python
_TRANSCRIBER_REGISTRY: dict[str, type[Transcriber]] = {}

@dataclass
class ModelPathValidationResult:
    valid: bool
    message: str
    resolved_path: str
    missing_files: list[str]
```

Modify `Transcriber` ABC to auto-register subclasses:

```python
class Transcriber(ABC):
    transcriber_name: str = ""  # subclasses must set this

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        name = getattr(cls, "transcriber_name", None)
        if name:
            _TRANSCRIBER_REGISTRY[name] = cls

    @staticmethod
    def get_class(name: str) -> type[Transcriber]:
        if name not in _TRANSCRIBER_REGISTRY:
            raise ValueError(f"未注册的转录器类型: {name}")
        return _TRANSCRIBER_REGISTRY[name]
```

Add 3 classmethods with conservative defaults:

```python
    @classmethod
    def validate_model_path(cls, path: str) -> ModelPathValidationResult:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(abs_path):
            return ModelPathValidationResult(True, "路径有效。", abs_path, [])
        return ModelPathValidationResult(False, f"路径不存在或不是目录: {abs_path}", abs_path, [])

    @classmethod
    def required_model_files(cls) -> list[str]:
        return []

    @classmethod
    def build_runtime_kwargs(cls, runtime_state: dict) -> dict:
        return {}
```

### 2. FastWhisperTranscriber Implementation

**File:** `src/main/python/sheng_wen/transcriber/fast_whisper_transcriber.py`

```python
class FastWhisperTranscriber(Transcriber):
    transcriber_name = "fast_whisper"

    @classmethod
    def validate_model_path(cls, path: str) -> ModelPathValidationResult:
        # Migrate logic from settings_manager._validate_manual_model_dir
        # Checks: config.json, model.bin, tokenizer.json, vocabulary.txt
        # vocabulary.json is acceptable alternative to vocabulary.txt

    @classmethod
    def required_model_files(cls) -> list[str]:
        return ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]

    @classmethod
    def build_runtime_kwargs(cls, runtime_state: dict) -> dict:
        device = runtime_state.get("device", "cpu")
        kwargs = {
            "device": device,
            "compute_type": "int8_float16" if device == "cuda" else "int8",
        }
        if runtime_state.get("model_source") == "manual_path":
            kwargs["model_size_or_path"] = runtime_state.get("model_path", "")
        else:
            kwargs["model_size"] = runtime_state.get("model_size", "tiny")
        return kwargs
```

### 3. VibeVoiceAsrTranscriber Implementation

**File:** `src/main/python/sheng_wen/transcriber/vibe_voice_asr_transcriber.py`

```python
class VibeVoiceAsrTranscriber(Transcriber):
    transcriber_name = "vibe_voice_asr"

    @classmethod
    def validate_model_path(cls, path: str) -> ModelPathValidationResult:
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
            "language_model_pretrained_name": runtime_state.get("vibevoice_language_model", "Qwen/Qwen2.5-7B"),
            "max_new_tokens": runtime_state.get("vibevoice_max_new_tokens", 8192),
            "dtype": runtime_state.get("vibevoice_dtype", "bfloat16"),
        }
```

### 4. VibeVoiceApiTranscriber Implementation

**File:** `src/main/python/sheng_wen/transcriber/vibe_voice_api_transcriber.py`

```python
class VibeVoiceApiTranscriber(Transcriber):
    transcriber_name = "vibe_voice_api"

    # validate_model_path: uses default (API mode, no local path needed)
    # required_model_files: uses default (empty list)

    @classmethod
    def build_runtime_kwargs(cls, runtime_state: dict) -> dict:
        return {
            "api_url": runtime_state.get("vibevoice_api_url", ""),
            "max_new_tokens": runtime_state.get("vibevoice_max_new_tokens", 8192),
        }
```

### 5. settings_manager.py Refactoring

**File:** `src/main/python/sheng_wen/transcriber/settings_manager.py`

**Delete:**
- `REQUIRED_MANUAL_MODEL_FILES` constant (L227-232)
- `_validate_manual_model_dir()` function (L254-280)
- `_build_transcriber_kwargs()` method (L414-432)

**Update `get_settings()`:**
- Replace `_validate_manual_model_dir()` call with `Transcriber.get_class(transcriber_type).validate_model_path(model_path)`
- Replace `REQUIRED_MANUAL_MODEL_FILES` with `cls.required_model_files()`

**Update `update_settings()`:**
- Validation: replace hardcoded check with `Transcriber.get_class(next_transcriber_type).validate_model_path(next_model_path)`
- Rebuild: replace hardcoded `get_transcriber("fast_whisper")` with `cls.build_runtime_kwargs()` + `get_transcriber(next_transcriber_type, **kwargs)`

### 6. api.py Refactoring

**File:** `src/main/python/sheng_wen/api.py`

Simplify `get_transcriber_worker()` — the vibe_voice kwargs construction blocks can use `cls.build_runtime_kwargs()` instead of hand-crafted dicts.

## Impact Summary

| File | Change |
|------|--------|
| `transcriber.py` | Add registry, `ModelPathValidationResult`, 3 classmethods to ABC |
| `fast_whisper_transcriber.py` | Implement 3 classmethods + `transcriber_name` |
| `vibe_voice_asr_transcriber.py` | Implement 3 classmethods + `transcriber_name` |
| `vibe_voice_api_transcriber.py` | Implement `build_runtime_kwargs` + `transcriber_name` |
| `settings_manager.py` | Delete hardcoded validation/build functions, dispatch via registry |
| `api.py` | Simplify kwargs construction using `cls.build_runtime_kwargs()` |
