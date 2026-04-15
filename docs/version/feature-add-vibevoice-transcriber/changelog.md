# VibeVoice Transcriber - Change Log

**Branch**: `feature/add-vibevoice-transcriber`
**Date**: 2026-04-09

## Change Log

- 2026-04-09: Added VibeVoice ASR transcriber integration for local model inference with CUDA execution and `bfloat16`/`float16` dtype support. The transcriber loads a VibeVoice ASR model from a local directory, uses `Qwen/Qwen2.5-7B` as the default language model, and exposes runtime settings for model path based deployment.
- 2026-04-09: Added VibeVoice-specific transcription settings: `vibevoice_language_model` for selecting the auxiliary language model, `vibevoice_max_new_tokens` for generation length control, and `vibevoice_dtype` for inference precision selection. Default values are `Qwen/Qwen2.5-7B`, `8192`, and `bfloat16`.
- 2026-04-09: Added VibeVoice model path validation through `POST /transcription/settings/validate-model-path`. The validator checks that the configured directory exists, resolves to an absolute local path, contains `config.json`, and includes at least one model weight file (`model.safetensors` or `pytorch_model.bin`). `preprocessor_config.json` is treated as optional but recommended.
- 2026-04-09: Documented hardware constraints for VibeVoice ASR. CUDA is required for model loading and inference in the current implementation. Deployments without a CUDA-capable environment cannot use `vibe_voice_asr` and should continue using another transcriber type.
- 2026-04-09: Fixed sharded safetensors/pytorch model file detection bug in VibeVoice validator. Added support for `model.safetensors.index.json` and `pytorch_model.bin.index.json` as valid weight file indicators.
- 2026-04-09: Added VibeVoice dual inference mode support (local + API). New `VibeVoiceApiTranscriber` sends audio via HTTP to vLLM API endpoints with streaming SSE. New `VibeVoiceServiceManager` manages vLLM subprocess lifecycle (start/stop/health-check/port-scan). Settings UI now includes inference mode toggle (本地加载/推理服务) and API service configuration.
- 2026-04-09: Added API endpoints for VibeVoice service management: `POST /vibevoice-scan` (port scan), `POST /vibevoice-service/start`, `POST /vibevoice-service/stop`, `GET /vibevoice-service/status`. New config fields: `vibevoice_inference_mode` ("local"/"api"), `vibevoice_api_url`.
- 2026-04-15: Added audio chunking for long audio (>5 min). Audio is split into 3-min WAV chunks (24kHz mono) via ffmpeg, transcribed independently, and merged with timestamp offsets. Prevents CUDA OOM on RTX 2080 Ti (22GB VRAM). New module `audio_chunker.py` with `get_audio_duration()`, `split_audio_into_chunks()`, `cleanup_chunks()`.
- 2026-04-15: Optimized VibeVoice ASR inference: switched to SDPA attention implementation (~20% speedup), reduced default `max_new_tokens` from 8192 to 2048, added automatic BitsAndBytes INT4 quantization detection. Overall RTF improved from ~7x (with OOM crashes) to ~1.0-1.7x.
- 2026-04-15: Pinned PyTorch 2.5.1+cu121 and added local vibevoice as editable dependency in pyproject.toml.
