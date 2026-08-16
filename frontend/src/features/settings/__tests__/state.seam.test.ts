/**
 * P7 seam 契约测试：features/settings/state.ts
 *
 * 规格（p7-composable-spec.md §3.3）逐键断言：
 * - 导出面形状：SettingsState 全部 state refs + actions；
 * - 三表单保存 payload + "失败抛错 + error 置值"双通道（App 的 handle*
 *   依赖 throw 静默吞）；
 * - validateModelPath 失败构造 { valid:false, ... } 安全视图并 throw；
 * - scanVibeVoiceServices 失败返回 []（不 throw）；
 * - vibevoiceInferenceMode / vibevoiceApiUrl 随 transcriptionSettings watch 同步。
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { useSettingsState } from '../state'
import type { SettingsState } from '../state'

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

const setup = () => {
  vi.clearAllMocks()
  vi.spyOn(console, 'error').mockImplementation(() => {})
  mockedAxios.get.mockResolvedValue({ data: [] })
  mockedAxios.put.mockResolvedValue({ data: {} })
  mockedAxios.post.mockResolvedValue({ data: {} })
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
  vi.stubGlobal('WebSocket', vi.fn(function () { return { close: vi.fn() } }))
}

describe('P7 seam：features/settings/state.ts 导出面与语义', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setup)

  it('导出面形状：state refs / actions 逐键存在', () => {
    const { settings } = mountSettingsState()

    for (const key of [
      'llmProviders', 'llmSettings', 'isUpdatingLlmSettings',
      'transcriptionSettings', 'isUpdatingTranscriptionSettings',
      'summarizationSettings', 'isUpdatingSummarizationSettings',
      'isReadingBilibiliCookieFromBrowser', 'modelPathValidationResult',
      'isValidatingModelPath', 'vibevoiceInferenceMode', 'vibevoiceApiUrl',
      'vibevoiceServiceStatus', 'isScanningVibeVoice', 'isStartingVibeVoice',
      'isStoppingVibeVoice', 'error',
    ]) {
      expect(settings[key as keyof SettingsState]).toBeDefined()
      expect((settings[key as keyof SettingsState] as { value: unknown }).value).toBeDefined()
    }
    for (const key of [
      'fetchLlmProviders', 'fetchLlmSettings', 'updateLlmSettings',
      'fetchTranscriptionSettings', 'updateTranscriptionSettings',
      'fetchSummarizationSettings', 'updateSummarizationSettings',
      'validateModelPath', 'scanVibeVoiceServices', 'startVibeVoiceService',
      'stopVibeVoiceService', 'fetchVibeVoiceServiceStatus', 'testLlm',
      'readBilibiliCookieFromBrowser', 'clearModelPathValidation',
    ]) {
      expect(typeof settings[key as keyof SettingsState]).toBe('function')
    }
    // 默认值语义
    expect(settings.vibevoiceInferenceMode.value).toBe('local')
    expect(settings.vibevoiceApiUrl.value).toBe('')
  })

  it('updateLlmSettings：payload 透传 + 成功写回；失败抛错 + error 置值（双通道）', async () => {
    const { settings, wrapper } = mountSettingsState()
    const llmSettings = {
      provider: 'openai', base_url: 'https://api.openai.com', model_id: 'gpt-4o',
      temperature: 0.7, context_window_size: 128000, has_api_key: true,
      api_key_hint: 'sk-***', extra_headers: {},
    }
    mockedAxios.put.mockResolvedValue({ data: llmSettings })

    const result = await settings.updateLlmSettings({ provider: 'openai' })
    expect(result).toEqual(llmSettings)
    expect(mockedAxios.put).toHaveBeenCalledWith('/llm/settings', { provider: 'openai' })
    expect(settings.llmSettings.value).toEqual(llmSettings)

    // 失败：抛错 + error 置值
    mockedAxios.put.mockRejectedValueOnce({
      isAxiosError: true,
      response: { data: { detail: 'API Key 无效' } },
    })
    await expect(settings.updateLlmSettings({ provider: 'openai' })).rejects.toBeTruthy()
    expect(settings.error.value).toBe('API Key 无效')

    wrapper.unmount()
  })

  it('updateTranscriptionSettings 失败：抛错 + error 置值；成功写回 ref', async () => {
    const { settings, wrapper } = mountSettingsState()
    const transcriptionSettings = {
      device: 'cpu', model_source: 'auto_download', model_size: 'small',
      model_path: '', model_path_valid: true, model_path_message: '',
      model_path_resolved: '', required_model_files: [], cuda_available: false,
      available_devices: ['cpu'], has_nvidia_gpu: false, torch_installed: true,
      torch_cuda_built: false, ctranslate2_installed: true,
      ctranslate2_cuda_device_count: 0, cuda_reason: '', cuda_message: '',
      enable_bilibili_subtitle_fetch: false, has_bilibili_sessdata: false,
      bilibili_cookie_source: '', bilibili_sessdata_masked: '',
      transcriber_type: 'fast_whisper', vibevoice_language_model: '',
      vibevoice_max_new_tokens: 128, vibevoice_dtype: 'bfloat16',
      vibevoice_inference_mode: 'local', vibevoice_api_url: '',
    }
    mockedAxios.put.mockResolvedValue({ data: transcriptionSettings })

    const result = await settings.updateTranscriptionSettings({ device: 'cpu' })
    expect(result).toEqual(transcriptionSettings)
    expect(settings.transcriptionSettings.value).toEqual(transcriptionSettings)

    mockedAxios.put.mockRejectedValueOnce(new Error('network down'))
    await expect(settings.updateTranscriptionSettings({ device: 'cpu' })).rejects.toThrow('network down')
    expect(settings.error.value).toBe('更新转录配置失败')

    wrapper.unmount()
  })

  it('validateModelPath：失败构造 { valid:false, ... } 安全视图并 throw', async () => {
    const { settings, wrapper } = mountSettingsState()
    const request = { path: '/missing/model', transcriber_type: 'vibe_voice_asr' as const }
    const networkError = Object.assign(new Error('Network Error'), { isAxiosError: true })

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

  it('scanVibeVoiceServices 失败返回 []（不 throw）；成功返回扫描结果', async () => {
    const { settings, wrapper } = mountSettingsState()

    mockedAxios.post.mockRejectedValueOnce(new Error('boom'))
    await expect(settings.scanVibeVoiceServices()).resolves.toEqual([])
    expect(settings.isScanningVibeVoice.value).toBe(false)

    mockedAxios.post.mockResolvedValueOnce({ data: [{ url: 'http://127.0.0.1:8001', status: 'available' }] })
    const results = await settings.scanVibeVoiceServices()
    expect(results).toEqual([{ url: 'http://127.0.0.1:8001', status: 'available' }])

    wrapper.unmount()
  })

  it('vibevoiceInferenceMode/vibevoiceApiUrl 随 transcriptionSettings watch 同步', async () => {
    const { settings, wrapper } = mountSettingsState()

    settings.transcriptionSettings.value = {
      vibevoice_inference_mode: 'api',
      vibevoice_api_url: 'http://127.0.0.1:8001',
    } as SettingsState['transcriptionSettings']['value']
    await flushPromises()

    expect(settings.vibevoiceInferenceMode.value).toBe('api')
    expect(settings.vibevoiceApiUrl.value).toBe('http://127.0.0.1:8001')

    wrapper.unmount()
  })
})
