/**
 * P7 迁移：useTaskViewModel.vibevoice.test.ts → features/settings/state.ts
 * 用例逻辑原样保留，仅改引用（useTaskViewModel → useSettingsState）与 setup。
 */
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { useSettingsState } from '../state'
import type { SettingsState } from '../state'
import type { ModelPathValidationRequest, ModelPathValidationResult } from '../../../types'

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    isAxiosError: vi.fn(),
    isCancel: vi.fn(),
  },
}))

const mockedAxios = vi.mocked(axios)

function createDeferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

const mountSettingsState = () => {
  let settings!: SettingsState
  const TestComponent = defineComponent({
    setup() {
      settings = useSettingsState()
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  return { settings, wrapper }
}

describe('settings 域 VibeVoice model path validation（useTaskViewModel 迁移）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    mockedAxios.get.mockResolvedValue({ data: [] })
    mockedAxios.put.mockResolvedValue({ data: {} })
    mockedAxios.isCancel.mockReturnValue(false)
    mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
      return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
    })

    const WebSocketMock = vi.fn(function MockWebSocket(this: Record<string, unknown>) {
      this.close = vi.fn()
      this.onopen = null
      this.onmessage = null
      this.onclose = null
      this.onerror = null
    })

    vi.stubGlobal('WebSocket', WebSocketMock)
  })

  it('validateModelPath posts to the expected endpoint and stores a success result', async () => {
    const deferred = createDeferred<{ data: ModelPathValidationResult }>()
    mockedAxios.post.mockReturnValueOnce(deferred.promise)

    const { settings, wrapper } = mountSettingsState()
    const request: ModelPathValidationRequest = {
      path: '/models/vibevoice',
      transcriber_type: 'vibe_voice_asr',
    }
    const expectedResult: ModelPathValidationResult = {
      valid: true,
      message: 'OK',
      resolved_path: '/models/vibevoice',
      missing_files: [],
      has_processor_config: true,
      details: {},
    }

    const pending = settings.validateModelPath(request)

    expect(settings.isValidatingModelPath.value).toBe(true)
    expect(settings.modelPathValidationResult.value).toBeNull()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/transcription/settings/validate-model-path',
      request,
    )

    deferred.resolve({ data: expectedResult })

    await expect(pending).resolves.toEqual(expectedResult)
    expect(settings.modelPathValidationResult.value).toEqual(expectedResult)
    expect(settings.isValidatingModelPath.value).toBe(false)

    wrapper.unmount()
  })

  it('validateModelPath creates a fallback result on network error', async () => {
    const { settings, wrapper } = mountSettingsState()
    const request: ModelPathValidationRequest = {
      path: '/missing/model',
      transcriber_type: 'vibe_voice_asr',
    }
    const networkError = Object.assign(new Error('Network Error'), {
      isAxiosError: true,
    })

    mockedAxios.post.mockRejectedValueOnce(networkError)

    await expect(settings.validateModelPath(request)).rejects.toThrow('Network Error')
    expect(settings.modelPathValidationResult.value).toEqual({
      valid: false,
      message: '验证请求失败',
      resolved_path: '/missing/model',
      missing_files: [],
      has_processor_config: false,
      details: {},
    })
    expect(settings.isValidatingModelPath.value).toBe(false)

    wrapper.unmount()
  })

  it('validateModelPath uses response detail in the fallback result when available', async () => {
    const { settings, wrapper } = mountSettingsState()
    const request: ModelPathValidationRequest = {
      path: '/bad/model',
      transcriber_type: 'vibe_voice_asr',
    }
    const responseError = Object.assign(new Error('Request failed'), {
      isAxiosError: true,
      response: {
        data: {
          detail: '缺少 config.json',
        },
      },
    })

    mockedAxios.post.mockRejectedValueOnce(responseError)

    await expect(settings.validateModelPath(request)).rejects.toThrow('Request failed')
    expect(settings.modelPathValidationResult.value).toEqual({
      valid: false,
      message: '缺少 config.json',
      resolved_path: '/bad/model',
      missing_files: [],
      has_processor_config: false,
      details: {},
    })
    expect(settings.isValidatingModelPath.value).toBe(false)

    wrapper.unmount()
  })

  it('clearModelPathValidation resets the stored validation result', () => {
    const { settings, wrapper } = mountSettingsState()

    settings.modelPathValidationResult.value = {
      valid: true,
      message: 'OK',
      resolved_path: '/models/vibevoice',
      missing_files: [],
      has_processor_config: true,
      details: {},
    }

    settings.clearModelPathValidation()

    expect(settings.modelPathValidationResult.value).toBeNull()

    wrapper.unmount()
  })
})
