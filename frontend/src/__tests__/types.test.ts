import { describe, it, expect } from 'vitest'
import { TaskStatus } from '../types'
import type {
  ModelPathValidationRequest,
  ModelPathValidationResult,
  TranscriptionSettings,
  UpdateTranscriptionSettingsRequest,
} from '../types'

describe('VibeVoice runtime values for typed shapes', () => {
  it('exposes the expected task status constants', () => {
    expect(TaskStatus).toEqual({
      PENDING: 'PENDING',
      DOWNLOADING: 'DOWNLOADING',
      UPLOADING: 'UPLOADING',
      TRANSCRIBING: 'TRANSCRIBING',
      SUMMARIZING: 'SUMMARIZING',
      COMPLETED: 'COMPLETED',
      FAILED: 'FAILED',
    })
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
      vibevoice_max_new_tokens: 8192,
      vibevoice_dtype: 'bfloat16',
      vibevoice_inference_mode: 'local',
      vibevoice_api_url: '',
    }

    expect(settings).toMatchObject({
      transcriber_type: 'vibe_voice_asr',
      vibevoice_language_model: 'Qwen/Qwen2.5-7B',
      vibevoice_max_new_tokens: 8192,
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
})
