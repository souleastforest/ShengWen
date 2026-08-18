# 测试环境依赖补装：torch 2.5.1+cu121 + vibevoice - Change Log

**Branch**: `fix/vibe-voice-deps`
**Date**: 2026-08-18

## Change Log

- 2026-08-18: 测试环境（21001）补装 `vibe_voice_asr` 转录器运行依赖，与生产环境（21010）版本对齐：
  - `torch==2.5.1+cu121`：通过 `[[tool.uv.index]]` 具名显式 index（`pytorch-cu121`，`explicit = true`）+ `[tool.uv.sources] torch = { index = "pytorch-cu121" }` 限定 CUDA 12.1 wheel 仅从 pytorch 官方源解析，其余依赖仍走 PyPI（避免 `unsafe-best-match` 把 requests/tqdm 等 20+ 个包降级到 pytorch 镜像的 2023 年旧版快照——首次安装实测降级 requests 2.33.1→2.28.1 / urllib3 2.6.3→1.26.13 等，已通过 `uv lock --upgrade-package` 恢复为 PyPI 最新版并重锁）。
  - `vibevoice==1.0.0`：本地源码路径依赖 `{ path = "../VibeVoice-bilibili-subtitle/VibeVoice" }`（生产为 pip editable 安装，测试环境走 uv path 依赖进 pyproject，`uv sync` 稳定可复现）。其声明的 transformers/accelerate/librosa/gradio/diffusers/aiortc/av 等依赖链一并解析锁定。
  - 验证：`torch.__version__ == 2.5.1+cu121`、`torch.cuda.is_available() == True`（RTX 2080 Ti 22GB，driver 535.261.03）；`import vibevoice` 成功；`Transcriber.get_class('vibe_voice_asr')` 注册 OK（懒加载模块 import 即注册，不触 db、不加载模型）；全量 `pytest tests/` **328 passed**（此前因 torch 缺失 collection error 的 `test_vibe_voice_asr_chunking.py` / `test_vibe_voice_empty_transcript.py` 现可正常收集，22 用例通过）；ruff check 通过。
  - 注意：pytest 收集时 conftest 模块级 `from ...db import Base, db` 会构造 `TaskDB()`（仅 mtime 变化、size 不变，隔离 fixture 已防写库）。
- 2026-08-18: 真实任务验证暴露模型加载链下一层缺失——`vibe_voice_asr` INT4 模型 `config.json` 声明 `quantization_config: {load_in_4bit: true, bnb_4bit_quant_type: nf4, ...}`，transformers `from_pretrained` 走 4bit 路径时报 `No package metadata was found for bitsandbytes`。补装 `bitsandbytes>=0.50.1`（PyPI 默认版本；0.50.x 已移除 `bnb.cuda_available` 布尔属性，改由 `bitsandbytes.cuda_specs.get_cuda_specs()` 报告 CUDA 版本，本机返回 `(12, 1)`；cu121 兼容性以真实 4bit 模型加载为准）：
  - 验证（临时脚本模拟 `_ensure_loaded` 完整加载链，shell 进程跑，非服务进程）：`VibeVoiceASRProcessor.from_pretrained(VibeVoice-ASR-INT4, language_model_pretrained_name=Qwen2.5-7B 本地路径)` + `VibeVoiceASRForConditionalGeneration.from_pretrained(..., dtype=bfloat16, trust_remote_code=True)` + `.to(cuda).eval()` → **加载成功**，耗时 91.20s（2 个 safetensors shard），VRAM 5.85GB（22GB 余量充足），`del` + `torch.cuda.empty_cache()` 后 VRAM 归零释放干净；无 bnb 错误、无 OOM。非致命告警（与 bnb 无关，属既有行为）：INT4 目录缺 `preprocessor_config.json` 走默认配置、tokenizer 类名告警、transformers `torch_dtype` deprecation。
  - 回归：全量 `pytest tests/` **328 passed**（与基线一致，无回归）；ruff 跳过（无代码改动，仅依赖与文档）。
  - 遗留：生产环境（21010）未装 bitsandbytes，若生产跑 VibeVoice 推理将同样缺失（生产当前未跑过，仅提示不操作）。
