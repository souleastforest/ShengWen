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
