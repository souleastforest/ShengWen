export const TaskStatus = {
  PENDING: "PENDING",
  DOWNLOADING: "DOWNLOADING",
  UPLOADING: "UPLOADING",
  TRANSCRIBING: "TRANSCRIBING",
  SUMMARIZING: "SUMMARIZING",
  COMPLETED: "COMPLETED",
  FAILED: "FAILED",
  PARTIAL: "PARTIAL"
} as const;

/**
 * 转录段级结果（后端 SegmentOut，见 routes/schemas.py:27-34）：
 * start/end 为秒数（float）；speaker_id 仅带说话人识别的 ASR 有（如 VibeVoice），
 * fast_whisper 缺省。来自 tasks.transcript_segments / task_parts.transcript_segments
 * 的 JSON 数组（TEXT NULL）。
 */
export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  speaker_id?: string;
}

export interface TaskPart {
  task_id: string;
  part_index: number;
  cid?: number;
  title?: string;
  duration?: number;
  status: string;
  progress: number;
  error_message?: string;
  transcript?: string;
  // 段级转录结果（include_text=true 时返回，列表剥离为 null）
  transcript_segments?: TranscriptSegment[] | null;
  summary?: string;
  audio_duration?: number;
  transcription_time?: number;
  // 后端 task_parts 行更新时间（get_task_parts 全量列，见 task_parts.py:38）
  updated_at?: string;
}

export type TaskStatus = typeof TaskStatus[keyof typeof TaskStatus];
// summary_mode：UI 提交三态——'none'（仅转录）/ 'standard'（标准）/ 'agent'（Agent）。
// 'auto' 为后端/历史任务兼容值（自动判定 standard/agent），UI 不再发送。
export type SummaryMode = 'standard' | 'agent' | 'auto' | 'none';

/**
 * 音频缺失原因（与后端 storage 回收 / 字幕直取逻辑保持一致）：
 * - subtitle_only: 任务未下载音频，直接使用了 B 站字幕进行转录
 * - reclaimed: 任务媒体文件已被存储回收器清理
 */
export type AudioMissingReason = 'subtitle_only' | 'reclaimed';

export interface QueueSnapshot {
  name: string;
  active_task_id: string | null;
  queue_size: number;
  waiting_task_ids: string[];
}

export interface QueueResponse {
  queues: QueueSnapshot[];
  timestamp: string;
}

export interface Task {
  id: string;
  video_url: string;
  status: TaskStatus;
  part_count?: number;
  part_completed?: number;
  part_failed?: number;
  current_part?: number;
  has_parts?: boolean;
  created_at: string;
  latest_modified_at?: string;
  progress: number;
  title?: string;
  topic?: string;
  transcript?: string;
  // 段级转录结果（include_content=true 时返回，列表/轻量详情剥离为 null）
  transcript_segments?: TranscriptSegment[] | null;
  // summary 允许 null：后端 include_content=false 时返回 null / _summary_overview
  // 截断版；前端"详情加载失败"标记亦用 null 表示"未加载但详情已结算"
  summary?: string | null;
  error_message?: string;
  transcription_time?: number;
  audio_duration?: number;
  author_name?: string;
  author_url?: string;
  summary_mode?: SummaryMode;
  summary_chunk_total?: number;
  summary_chunk_done?: number;
  summary_meta?: string;
  // ASR 分片进度（仅转录阶段非空；转录完成转 SUMMARIZING 时后端置 null，
  // 与 summary_chunk_* 对称）。展示"转录分片 done/total"。
  asr_chunk_total?: number | null;
  asr_chunk_done?: number | null;
  audio_downloaded?: boolean;
  audio_missing_reason?: AudioMissingReason;
  source_name?: string;
}

export interface CreateTaskRequest {
  video_url: string;
  quality: string;
  summary_mode?: SummaryMode;
  // 仅转录（summary_mode='none'）时是否在转录完成后自动生成标题（默认开启）
  generate_topic?: boolean;
  // B 站分 P 处理配置（仅多 P 视频需要；mode=merge 合并 / separate 拆分）
  bilibili_parts?: BilibiliPartsConfig;
}

/**
 * POST /upload/local-path 请求体（后端 LocalPathTaskCreate，见
 * routes/schemas.py:11-20）。generate_topic 仅在仅转录模式随开关显式发送；
 * 标准/Agent 模式不发送该字段。
 */
export interface LocalPathCreateTaskRequest {
  file_path: string;
  summary_mode?: SummaryMode;
  generate_topic?: boolean;
}

/**
 * POST /tasks/{task_id}/re-summarize 请求体（后端 ReSummarizeRequest，见
 * routes/schemas.py:60）。summary_mode 缺省时后端沿用任务已存值。
 */
export interface ReSummarizeRequest {
  summary_mode?: SummaryMode;
}

/**
 * POST /tasks/{task_id}/re-transcribe 请求体（后端 ReTranscribeRequest，见
 * routes/schemas.py:67）。summary_mode 缺省沿用任务已存值；generate_topic
 * 为重新转录后的"总结标题"开关，缺省沿用任务已存值（老任务默认 True），
 * 前端当前不发送该字段。
 */
export interface ReTranscribeRequest {
  summary_mode?: SummaryMode;
  generate_topic?: boolean;
}

export interface MarkdownHeadingItem {
  id: string;
  text: string;
  level: number;
}

export interface LLMProvider {
  id: string;
  label: string;
  default_base_url: string;
  default_model_id: string;
  description: string;
}

export interface LLMSettings {
  provider: string;
  base_url: string;
  model_id: string;
  temperature: number;
  context_window_size: number;
  has_api_key: boolean;
  api_key_hint: string;
  extra_headers: Record<string, string>;
}

export interface UpdateLLMSettingsRequest {
  provider: string;
  base_url?: string;
  api_key?: string;
  model_id?: string;
  temperature?: number;
  context_window_size?: number;
  extra_headers?: Record<string, string>;
}

export interface TranscriptionSettings {
  device: "cpu" | "cuda";
  model_source: "auto_download" | "manual_path";
  model_size: "tiny" | "base" | "small" | "medium" | "large";
  model_path: string;
  model_path_valid: boolean;
  model_path_message: string;
  model_path_resolved: string;
  required_model_files: string[];
  cuda_available: boolean;
  available_devices: string[];
  has_nvidia_gpu: boolean;
  torch_installed: boolean;
  torch_cuda_built: boolean;
  ctranslate2_installed: boolean;
  ctranslate2_cuda_device_count: number;
  cuda_reason: string;
  cuda_message: string;
  enable_bilibili_subtitle_fetch: boolean;
  has_bilibili_sessdata: boolean;
  bilibili_cookie_source: string;
  bilibili_sessdata_masked: string;
  transcriber_type: "fast_whisper" | "vibe_voice_asr";
  vibevoice_language_model: string;
  vibevoice_max_new_tokens: number;
  vibevoice_dtype: "bfloat16" | "float16";
  vibevoice_inference_mode: "local" | "api";
  vibevoice_api_url: string;
}

export interface UpdateTranscriptionSettingsRequest {
  device?: "cpu" | "cuda";
  model_source?: "auto_download" | "manual_path";
  model_size?: "tiny" | "base" | "small" | "medium" | "large";
  model_path?: string;
  enable_bilibili_subtitle_fetch?: boolean;
  bilibili_sessdata?: string;
  clear_bilibili_sessdata?: boolean;
  transcriber_type?: "fast_whisper" | "vibe_voice_asr";
  vibevoice_language_model?: string;
  vibevoice_max_new_tokens?: number;
  vibevoice_dtype?: "bfloat16" | "float16";
  vibevoice_inference_mode?: "local" | "api";
  vibevoice_api_url?: string;
}

export interface ModelPathValidationRequest {
  path: string;
  transcriber_type: "fast_whisper" | "vibe_voice_asr";
}

export interface ModelPathValidationResult {
  valid: boolean;
  message: string;
  resolved_path: string;
  missing_files: string[];
  has_processor_config: boolean;
  details: Record<string, unknown>;
}

export interface VibeVoiceServiceScanResult {
  url: string;
  status: "available" | "unreachable";
}

export interface VibeVoiceServiceStatus {
  running: boolean;
  pid: number | null;
  api_url: string;
  api_healthy: boolean;
}

export interface SummarizationSettings {
  mode: SummaryMode;
  auto_chunk_min_audio_duration_sec: number;
  auto_chunk_min_transcript_lines: number;
  chunk_target_duration_sec: number;
  chunk_min_duration_sec: number;
  chunk_max_duration_sec: number;
  boundary_jump_sec: number;
  prev_tail_timestamp_lines_m: number;
  prev_summary_tail_chars_j: number;
  llm_call_retry_max: number;
  max_agent_value_chars: number;
  fallback_to_standard_on_agent_error: boolean;
}

export interface UpdateSummarizationSettingsRequest {
  mode?: SummaryMode;
  auto_chunk_min_audio_duration_sec?: number;
  auto_chunk_min_transcript_lines?: number;
  chunk_target_duration_sec?: number;
  chunk_min_duration_sec?: number;
  chunk_max_duration_sec?: number;
  boundary_jump_sec?: number;
  prev_tail_timestamp_lines_m?: number;
  prev_summary_tail_chars_j?: number;
  llm_call_retry_max?: number;
  max_agent_value_chars?: number;
  fallback_to_standard_on_agent_error?: boolean;
}

export interface BilibiliCookieFromBrowserResult {
  success: boolean;
  sessdata?: string;
  sessdata_masked?: string;
  source_browser?: string;
  error?: string;
}

export interface BilibiliVideoPartInfo {
  index: number;
  cid: number;
  title: string;
  duration: number;
}

export interface BilibiliVideoInfo {
  is_multi_part: boolean;
  title: string;
  bvid: string;
  duration: number;
  parts?: BilibiliVideoPartInfo[];
  // 探针状态：'ok' 正常 / 'degraded' 网络故障降级为单P语义（前端不得静默提交）；
  // 缺省为旧后端（无 status 字段），按响应特征启发式识别
  status?: 'ok' | 'degraded';
}

export interface BilibiliPartsConfig {
  mode: 'merge' | 'separate';
  indices: number[];
}

export interface LocalFolderFile {
  name: string;
  path: string;
  size: number;
}

export interface LocalFolderScanResult {
  folder_path: string;
  files: LocalFolderFile[];
  total: number;
}

export interface LocalPathCheckResult {
  type: 'file' | 'folder' | 'not_found';
  path: string;
}
