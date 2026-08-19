from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl

from src.main.python.sheng_wen.db import TaskStatus


class LocalPathTaskCreate(BaseModel):
    file_path: str = Field(..., min_length=1, description="本机文件绝对路径")
    summary_mode: Optional[str] = Field(
        default=None,
        description="总结模式: standard | agent | auto（前端建议仅 standard/agent）",
    )
    generate_topic: bool = Field(
        default=True,
        description="仅转录（summary_mode=none）时，转录完成后是否对全文生成标题（默认开启）",
    )


class TaskUpdate(BaseModel):
    topic: Optional[str] = None


class Task(BaseModel):
    id: str
    video_url: str
    status: TaskStatus
    created_at: datetime
    latest_modified_at: Optional[datetime] = None
    progress: float = 0.0
    title: Optional[str] = None
    topic: Optional[str] = None
    transcript: Optional[str] = None
    summary: Optional[str] = None
    error_message: Optional[str] = None
    audio_duration: Optional[float] = None
    transcription_time: Optional[float] = None
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    summary_mode: Optional[str] = None
    summary_chunk_total: Optional[int] = None
    summary_chunk_done: Optional[int] = None
    summary_meta: Optional[str] = None
    audio_downloaded: Optional[bool] = None
    audio_missing_reason: Optional[str] = None
    source_name: Optional[str] = None
    # ASR 分片进度（仅转录阶段非空，与 summary_chunk_* 对称；加性字段向后兼容）
    asr_chunk_total: Optional[int] = None
    asr_chunk_done: Optional[int] = None
    part_count: Optional[int] = None
    part_completed: Optional[int] = None
    part_failed: Optional[int] = None
    current_part: Optional[int] = None
    has_parts: bool = False


class ReSummarizeRequest(BaseModel):
    summary_mode: Optional[str] = Field(
        default=None,
        description="重新总结时指定模式: standard | agent | auto",
    )


class ReTranscribeRequest(BaseModel):
    summary_mode: Optional[str] = Field(
        default=None,
        description="重新转录后进入总结时指定模式: standard | agent | auto",
    )
    generate_topic: Optional[bool] = Field(
        default=None,
        description="重新转录后的'总结标题'开关；缺省时沿用任务已存值（老任务默认 True）",
    )


class LLMProviderInfo(BaseModel):
    id: str
    label: str
    default_base_url: str
    default_model_id: str
    description: str


class LLMSettings(BaseModel):
    provider: str
    base_url: str
    model_id: str
    temperature: float
    context_window_size: int
    has_api_key: bool
    api_key_hint: str
    extra_headers: dict[str, str] = {}


class LLMSettingsUpdate(BaseModel):
    provider: str
    base_url: Optional[str] = None
    api_key: Optional[str] = Field(default=None, description="不传则保持当前密钥")
    model_id: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    context_window_size: Optional[int] = Field(default=None, ge=1)
    extra_headers: Optional[dict[str, str]] = None


class TranscriptionSettings(BaseModel):
    device: str
    transcriber_type: str = "fast_whisper"
    model_source: str
    model_size: str
    model_path: str
    model_path_valid: bool
    model_path_message: str
    model_path_resolved: str
    required_model_files: list[str]
    cuda_available: bool
    available_devices: list[str]
    has_nvidia_gpu: bool
    torch_installed: bool
    torch_cuda_built: bool
    ctranslate2_installed: bool
    ctranslate2_cuda_device_count: int
    cuda_reason: str
    cuda_message: str
    enable_bilibili_subtitle_fetch: bool
    has_bilibili_sessdata: bool
    bilibili_cookie_source: str
    bilibili_sessdata_masked: str
    vibevoice_language_model: str
    vibevoice_max_new_tokens: int
    vibevoice_dtype: str
    vibevoice_inference_mode: str
    vibevoice_api_url: str


class TranscriptionSettingsUpdate(BaseModel):
    device: Optional[str] = Field(default=None, description="cpu 或 cuda")
    transcriber_type: Optional[str] = Field(
        default=None, description="fast_whisper 或 vibe_voice_asr"
    )
    model_source: Optional[str] = Field(
        default=None, description="auto_download 或 manual_path"
    )
    model_size: Optional[str] = Field(
        default=None, description="tiny/base/small/medium/large"
    )
    model_path: Optional[str] = Field(default=None, description="手动模型目录路径")
    vibevoice_language_model: Optional[str] = Field(
        default=None, description="VibeVoice 使用的语言模型目录或 Hugging Face 模型 ID"
    )
    vibevoice_max_new_tokens: Optional[int] = Field(
        default=None, ge=1, description="VibeVoice 最大生成 Token 数"
    )
    vibevoice_dtype: Optional[str] = Field(
        default=None, description="VibeVoice 数据类型: bfloat16 或 float16"
    )
    vibevoice_inference_mode: Optional[str] = Field(
        default=None, description="VibeVoice 推理模式: local 或 api"
    )
    vibevoice_api_url: Optional[str] = Field(
        default=None, description="VibeVoice vLLM API 地址"
    )
    enable_bilibili_subtitle_fetch: Optional[bool] = Field(
        default=None,
        description="是否优先尝试直取 B 站字幕（失败时回退 ASR）",
    )
    bilibili_sessdata: Optional[str] = Field(
        default=None,
        description="设置全局 B 站 SESSDATA（明文保存在本机 config/settings.json）",
    )
    clear_bilibili_sessdata: Optional[bool] = Field(
        default=None,
        description="是否清空当前保存的全局 B 站 SESSDATA",
    )


class ModelPathValidationRequest(BaseModel):
    path: str = Field(..., description="待验证的本地模型目录路径")
    transcriber_type: str = Field(
        default="fast_whisper",
        description="转录器类型: fast_whisper 或 vibe_voice_asr",
    )


class ModelPathValidationResult(BaseModel):
    valid: bool
    message: str
    resolved_path: str = ""
    missing_files: list[str] = []
    has_processor_config: bool = False
    details: dict[str, Any] = {}


class BilibiliCookieFromBrowserResult(BaseModel):
    success: bool = Field(description="是否成功读取")
    sessdata: Optional[str] = Field(
        default=None, description="读取到的 SESSDATA（完整值）"
    )
    sessdata_masked: Optional[str] = Field(
        default=None, description="脱敏后的 SESSDATA"
    )
    source_browser: Optional[str] = Field(default=None, description="读取来源浏览器")
    error: Optional[str] = Field(default=None, description="错误信息")


class BilibiliVideoPartInfo(BaseModel):
    index: int = Field(description="分P索引（0-based）")
    cid: int = Field(description="分P的 cid")
    title: str = Field(description="分P标题")
    duration: int = Field(description="分P时长（秒）")


class BilibiliVideoInfoRequest(BaseModel):
    url: str = Field(..., description="B站视频链接")


class BilibiliVideoInfo(BaseModel):
    is_multi_part: bool = Field(description="是否为多P视频")
    title: str = Field(description="视频标题")
    bvid: str = Field(description="BV号")
    duration: int = Field(description="视频总时长（秒）")
    parts: Optional[list[BilibiliVideoPartInfo]] = Field(
        default=None, description="分P列表（仅多P视频）"
    )
    status: Optional[str] = Field(
        default=None,
        description=(
            "探针状态: ok（正常）/ degraded（网络故障降级为单P语义，"
            "前端不得静默提交）；缺省为旧版本"
        ),
    )


class BilibiliPartsConfig(BaseModel):
    mode: str = Field(description="处理模式: merge（合并）或 separate（拆分）")
    indices: list[int] = Field(description="要处理的分P索引列表")


class TaskCreate(BaseModel):
    video_url: HttpUrl
    quality: Optional[str] = "best"
    summary_mode: Optional[str] = Field(
        default=None,
        description="总结模式: standard | agent | auto（前端建议仅 standard/agent）",
    )
    bilibili_sessdata: Optional[str] = Field(
        default=None,
        description="任务级 B 站 SESSDATA，可覆盖全局配置与环境变量",
    )
    bilibili_parts: Optional[BilibiliPartsConfig] = Field(
        default=None,
        description="B站分P处理配置（仅多P视频需要）",
    )
    generate_topic: bool = Field(
        default=True,
        description="仅转录（summary_mode=none）时，转录完成后是否对全文生成标题（默认开启）",
    )


class SummarizationSettings(BaseModel):
    mode: str
    auto_chunk_min_audio_duration_sec: int
    auto_chunk_min_transcript_lines: int
    chunk_target_duration_sec: int
    chunk_min_duration_sec: int
    chunk_max_duration_sec: int
    boundary_jump_sec: int
    prev_tail_timestamp_lines_m: int
    prev_summary_tail_chars_j: int
    llm_call_retry_max: int
    max_agent_value_chars: int
    fallback_to_standard_on_agent_error: bool


class SummarizationSettingsUpdate(BaseModel):
    mode: Optional[str] = Field(default=None, description="auto | standard | agent")
    auto_chunk_min_audio_duration_sec: Optional[int] = Field(default=None, ge=300)
    auto_chunk_min_transcript_lines: Optional[int] = Field(default=None, ge=100)
    chunk_target_duration_sec: Optional[int] = Field(default=None, ge=60)
    chunk_min_duration_sec: Optional[int] = Field(default=None, ge=30)
    chunk_max_duration_sec: Optional[int] = Field(default=None, ge=60)
    boundary_jump_sec: Optional[int] = Field(default=None, ge=1)
    prev_tail_timestamp_lines_m: Optional[int] = Field(default=None, ge=0)
    prev_summary_tail_chars_j: Optional[int] = Field(default=None, ge=0)
    llm_call_retry_max: Optional[int] = Field(default=None, ge=1)
    max_agent_value_chars: Optional[int] = Field(default=None, ge=100)
    fallback_to_standard_on_agent_error: Optional[bool] = None


class LLMTestResult(BaseModel):
    status: str
    message: str
    response: Optional[str] = None


class VersionInfo(BaseModel):
    version: str
