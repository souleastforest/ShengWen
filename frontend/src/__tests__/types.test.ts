import { describe, it, expect } from 'vitest'
import { TaskStatus } from '../types'
import type {
  ModelPathValidationRequest,
  ModelPathValidationResult,
  TranscriptionSettings,
  UpdateTranscriptionSettingsRequest,
  QueueResponse,
  Task,
  AudioMissingReason,
  CreateTaskRequest,
  LocalPathCreateTaskRequest,
  ReSummarizeRequest,
  ReTranscribeRequest,
  TaskPart,
  TranscriptSegment,
} from '../types'

describe('VibeVoice runtime values for typed shapes', () => {
  it('exposes the expected task status constants (8 values, 与后端一致)', () => {
    expect(TaskStatus).toEqual({
      PENDING: 'PENDING',
      DOWNLOADING: 'DOWNLOADING',
      UPLOADING: 'UPLOADING',
      TRANSCRIBING: 'TRANSCRIBING',
      SUMMARIZING: 'SUMMARIZING',
      COMPLETED: 'COMPLETED',
      FAILED: 'FAILED',
      PARTIAL: 'PARTIAL',
    })
  })

  it('matches the queue snapshot contract shape', () => {
    const response: QueueResponse = {
      queues: [
        {
          name: 'VideoDownloaderWorker',
          active_task_id: null,
          queue_size: 0,
          waiting_task_ids: [],
        },
        {
          name: 'TranscriberWorker',
          active_task_id: 'task-9',
          queue_size: 2,
          waiting_task_ids: ['task-1', 'task-2'],
        },
      ],
      timestamp: '2026-08-08T00:00:00Z',
    }

    expect(response.queues[0]).toEqual({
      name: 'VideoDownloaderWorker',
      active_task_id: null,
      queue_size: 0,
      waiting_task_ids: [],
    })
    expect(response.queues[1]).toEqual({
      name: 'TranscriberWorker',
      active_task_id: 'task-9',
      queue_size: 2,
      waiting_task_ids: ['task-1', 'task-2'],
    })
    expect(Object.keys(response)).toEqual(['queues', 'timestamp'])
  })

  it('matches the VibeVoice-related transcription settings fields', () => {
    const settings: TranscriptionSettings = {
      device: 'cuda',
      model_source: 'manual_path',
      model_size: 'large',
      model_path: '/models/vibevoice',
      model_path_valid: true,
      model_path_message: 'ready',
      model_path_resolved: '/models/vibevoice',
      required_model_files: ['config.json', 'model.bin'],
      cuda_available: true,
      available_devices: ['cpu', 'cuda'],
      has_nvidia_gpu: true,
      torch_installed: true,
      torch_cuda_built: true,
      ctranslate2_installed: true,
      ctranslate2_cuda_device_count: 1,
      cuda_reason: '',
      cuda_message: 'CUDA available',
      enable_bilibili_subtitle_fetch: false,
      has_bilibili_sessdata: false,
      bilibili_cookie_source: '',
      bilibili_sessdata_masked: '',
      transcriber_type: 'vibe_voice_asr',
      vibevoice_language_model: 'Qwen/Qwen2.5-7B',
      vibevoice_max_new_tokens: 16384,
      vibevoice_dtype: 'bfloat16',
      vibevoice_inference_mode: 'local',
      vibevoice_api_url: '',
    }

    expect(settings).toMatchObject({
      transcriber_type: 'vibe_voice_asr',
      vibevoice_language_model: 'Qwen/Qwen2.5-7B',
      vibevoice_max_new_tokens: 16384,
      vibevoice_dtype: 'bfloat16',
      vibevoice_inference_mode: 'local',
      vibevoice_api_url: '',
    })
    expect(Object.keys(settings)).toEqual(
      expect.arrayContaining([
        'transcriber_type',
        'vibevoice_language_model',
        'vibevoice_max_new_tokens',
        'vibevoice_dtype',
        'vibevoice_inference_mode',
        'vibevoice_api_url',
      ]),
    )
  })

  it('matches the VibeVoice-related update request fields', () => {
    const update: UpdateTranscriptionSettingsRequest = {
      transcriber_type: 'vibe_voice_asr',
      vibevoice_language_model: 'Qwen/Qwen2.5-7B',
      vibevoice_max_new_tokens: 4096,
      vibevoice_dtype: 'float16',
      vibevoice_inference_mode: 'api',
      vibevoice_api_url: 'http://localhost:8000',
    }

    expect(update).toEqual({
      transcriber_type: 'vibe_voice_asr',
      vibevoice_language_model: 'Qwen/Qwen2.5-7B',
      vibevoice_max_new_tokens: 4096,
      vibevoice_dtype: 'float16',
      vibevoice_inference_mode: 'api',
      vibevoice_api_url: 'http://localhost:8000',
    })
  })

  it('matches the model path validation request fields', () => {
    const request: ModelPathValidationRequest = {
      path: '/models/vibevoice',
      transcriber_type: 'vibe_voice_asr',
    }

    expect(request).toEqual({
      path: '/models/vibevoice',
      transcriber_type: 'vibe_voice_asr',
    })
  })

  it('matches the model path validation result fields', () => {
    const result: ModelPathValidationResult = {
      valid: false,
      message: 'missing files',
      resolved_path: '/models/vibevoice',
      missing_files: ['config.json'],
      has_processor_config: false,
      details: { config_loaded: false },
    }

    expect(result).toMatchObject({
      valid: false,
      message: 'missing files',
      resolved_path: '/models/vibevoice',
      missing_files: ['config.json'],
      has_processor_config: false,
      details: { config_loaded: false },
    })
    expect(Object.keys(result)).toEqual([
      'valid',
      'message',
      'resolved_path',
      'missing_files',
      'has_processor_config',
      'details',
    ])
  })

  it('matches the audio status contract fields on Task (与后端 storage 回收字段一致)', () => {
    const reclaimedTask: Task = {
      id: 'task-1',
      video_url: 'https://example.com/v.mp4',
      status: 'COMPLETED',
      progress: 1,
      created_at: '2026-08-08T00:00:00Z',
      audio_downloaded: false,
      audio_missing_reason: 'reclaimed',
    }

    expect(reclaimedTask.audio_downloaded).toBe(false)
    expect(reclaimedTask.audio_missing_reason).toBe('reclaimed')

    // 字段为可选：未回填的旧任务不应报类型错误
    const legacyTask: Task = {
      id: 'task-2',
      video_url: 'https://example.com/v2.mp4',
      status: 'FAILED',
      progress: 0,
      created_at: '2026-08-01T00:00:00Z',
    }
    expect(legacyTask.audio_downloaded).toBeUndefined()
    expect(legacyTask.audio_missing_reason).toBeUndefined()
  })

  it('AudioMissingReason 仅允许 subtitle_only / reclaimed 两个值', () => {
    const subtitleOnly: AudioMissingReason = 'subtitle_only'
    const reclaimed: AudioMissingReason = 'reclaimed'
    expect([subtitleOnly, reclaimed]).toEqual(['subtitle_only', 'reclaimed'])
  })
})

describe('P4 类型契约：请求与分P形状', () => {
  it('CreateTaskRequest 含 bilibili_parts 分P配置（submitTaskWithParts 调用点契约）', () => {
    // 编译期：全部文档化字段必须是 CreateTaskRequest 合法键（缺失时 vue-tsc 报错）
    const requestKeys: (keyof CreateTaskRequest)[] = [
      'video_url',
      'quality',
      'summary_mode',
      'generate_topic',
      'bilibili_parts',
    ]
    expect(requestKeys).toContain('bilibili_parts')

    // 编译期 + 运行时：与 submitTaskWithParts 实际 payload 同形（features/upload/state.ts submitTaskWithParts）
    const mergeRequest: CreateTaskRequest = {
      video_url: 'https://www.bilibili.com/video/BV1xx411c7mD',
      quality: 'audio_only',
      summary_mode: 'none',
      bilibili_parts: { mode: 'merge', indices: [0, 1] },
    }
    expect(mergeRequest.bilibili_parts).toEqual({ mode: 'merge', indices: [0, 1] })

    const separateRequest: CreateTaskRequest = {
      video_url: 'https://www.bilibili.com/video/BV1xx411c7mD',
      quality: 'audio_only',
      summary_mode: 'standard',
      bilibili_parts: { mode: 'separate', indices: [1, 2] },
    }
    expect(separateRequest.bilibili_parts).toEqual({ mode: 'separate', indices: [1, 2] })
  })

  it('LocalPathCreateTaskRequest 形状与 submitLocalPathTask 调用点 payload 对齐', () => {
    // 编译期：调用点 payload（features/upload/state.ts submitLocalPathTask）满足类型
    const callSitePayload: LocalPathCreateTaskRequest = {
      file_path: '/media/video.mp4',
      summary_mode: 'none',
      generate_topic: true,
    }
    expect(callSitePayload).toEqual({
      file_path: '/media/video.mp4',
      summary_mode: 'none',
      generate_topic: true,
    })
    // 标准/Agent 模式不发送 generate_topic
    const standardMode: LocalPathCreateTaskRequest = {
      file_path: '/media/video.mp4',
      summary_mode: 'standard',
    }
    expect(standardMode.generate_topic).toBeUndefined()
  })

  it('ReSummarizeRequest 形状与 reSummarize 调用点 payload 对齐', () => {
    // 编译期：调用点 payload（features/task/state.ts reSummarize）满足类型
    const callSitePayload: ReSummarizeRequest = { summary_mode: 'standard' }
    expect(callSitePayload).toEqual({ summary_mode: 'standard' })
    // 缺省：不传 summary_mode 时后端沿用任务已存值
    const defaultPayload: ReSummarizeRequest = {}
    expect(defaultPayload).toEqual({})
  })

  it('ReTranscribeRequest 形状与 reTranscribe 调用点 payload 对齐', () => {
    // 编译期：调用点 payload（features/task/state.ts reTranscribe）满足类型
    const callSitePayload: ReTranscribeRequest = { summary_mode: 'none' }
    expect(callSitePayload).toEqual({ summary_mode: 'none' })
    // generate_topic 缺省沿用任务已存值（前端当前不发送该字段）
    const defaultPayload: ReTranscribeRequest = {}
    expect(defaultPayload).toEqual({})
    const withTopic: ReTranscribeRequest = {
      summary_mode: 'standard',
      generate_topic: false,
    }
    expect(withTopic).toEqual({ summary_mode: 'standard', generate_topic: false })
  })

  it('TaskPart 形状与后端 task_parts 序列化对齐（含 updated_at）', () => {
    // 编译期：后端 get_task_parts 全量列（src/main/python/sheng_wen/task_parts.py:74-77）
    // 必须全部是 TaskPart 合法键（缺失字段时 vue-tsc 报错）
    const backendColumns: (keyof TaskPart)[] = [
      'task_id',
      'part_index',
      'cid',
      'title',
      'duration',
      'status',
      'progress',
      'error_message',
      'transcript',
      'summary',
      'audio_duration',
      'transcription_time',
      'updated_at',
    ]
    expect(backendColumns).toContain('updated_at')
    expect(backendColumns).toContain('transcription_time')

    // 编译期 + 运行时：含 updated_at 的分P对象
    const part: TaskPart = {
      task_id: 'task-1',
      part_index: 2,
      cid: 54321,
      title: '第二P',
      duration: 300,
      status: 'COMPLETED',
      progress: 1,
      updated_at: '2026-08-14T00:00:00+00:00',
    }
    expect(part.updated_at).toBe('2026-08-14T00:00:00+00:00')
  })

  it('TranscriptSegment 结构：start/end 秒数 + text，speaker_id 可选', () => {
    const seg: TranscriptSegment = { start: 1.5, end: 4.25, text: '第一段', speaker_id: '1' }
    expect(seg).toEqual({ start: 1.5, end: 4.25, text: '第一段', speaker_id: '1' })

    // speaker_id 缺省合法（fast_whisper 无说话人）
    const segNoSpeaker: TranscriptSegment = { start: 0, end: 1, text: '无说话人' }
    expect(segNoSpeaker.speaker_id).toBeUndefined()
  })

  it('Task / TaskPart 携带可选 transcript_segments（后端 tasks/task_parts 新列）', () => {
    const segments: TranscriptSegment[] = [
      { start: 0, end: 2.5, text: '第一段' },
      { start: 2.5, end: 5, text: '第二段', speaker_id: '1' },
    ]
    const task: Task = {
      id: 'task-1',
      video_url: 'https://www.bilibili.com/video/BV1xx',
      status: 'COMPLETED',
      progress: 1.0,
      created_at: '2026-08-14T00:00:00Z',
      transcript: '000000 第一段',
      transcript_segments: segments,
    }
    expect(task.transcript_segments).toEqual(segments)

    const part: TaskPart = {
      task_id: 'task-1',
      part_index: 0,
      status: 'COMPLETED',
      progress: 1,
      transcript_segments: null,
    }
    expect(part.transcript_segments).toBeNull()
  })
})
