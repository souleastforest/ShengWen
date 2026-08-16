/**
 * P7 设置域状态（features/settings/state.ts）——从 useTaskViewModel 按域拆分。
 *
 * 语义逐条平移（行为等价铁律，p7-composable-spec.md §3.3）：
 * - 三表单保存：成功写回 ref 并返回；失败"抛错 + error 置值"双通道
 *   （App 的 handle* 依赖 throw 静默吞）；
 * - validateModelPath 失败构造 { valid:false, ... } 安全视图并 throw；
 * - scanVibeVoiceServices 失败返回 []（不 throw）；
 * - readBilibiliCookieFromBrowser 成功后自动 refetch transcriptionSettings；
 * - vibevoiceInferenceMode / vibevoiceApiUrl 随 transcriptionSettings watch 同步。
 *
 * 依赖方向：仅 shared/api、src/types。不订阅 WS（vibevoice 状态为轮询/显式拉取）。
 */
import { ref, watch } from 'vue'
import type { Ref } from 'vue'
import { apiClient, isAxiosError } from '../../shared/api/client'
import type {
  LLMProvider,
  LLMSettings,
  UpdateLLMSettingsRequest,
  TranscriptionSettings,
  UpdateTranscriptionSettingsRequest,
  SummarizationSettings,
  UpdateSummarizationSettingsRequest,
  BilibiliCookieFromBrowserResult,
  ModelPathValidationRequest,
  ModelPathValidationResult,
  VibeVoiceServiceScanResult,
  VibeVoiceServiceStatus,
} from '../../types'

export interface SettingsState {
  llmProviders: Ref<LLMProvider[]>
  llmSettings: Ref<LLMSettings | null>
  isUpdatingLlmSettings: Ref<boolean>
  transcriptionSettings: Ref<TranscriptionSettings | null>
  isUpdatingTranscriptionSettings: Ref<boolean>
  summarizationSettings: Ref<SummarizationSettings | null>
  isUpdatingSummarizationSettings: Ref<boolean>
  isReadingBilibiliCookieFromBrowser: Ref<boolean>
  modelPathValidationResult: Ref<ModelPathValidationResult | null>
  isValidatingModelPath: Ref<boolean>
  /** 随 transcriptionSettings watch 同步 */
  vibevoiceInferenceMode: Ref<'local' | 'api'>
  vibevoiceApiUrl: Ref<string>
  vibevoiceServiceStatus: Ref<VibeVoiceServiceStatus | null>
  isScanningVibeVoice: Ref<boolean>
  isStartingVibeVoice: Ref<boolean>
  isStoppingVibeVoice: Ref<boolean>
  error: Ref<string | null>

  fetchLlmProviders(): Promise<void>
  fetchLlmSettings(): Promise<void>
  updateLlmSettings(payload: UpdateLLMSettingsRequest): Promise<LLMSettings>
  fetchTranscriptionSettings(): Promise<void>
  updateTranscriptionSettings(payload: UpdateTranscriptionSettingsRequest): Promise<TranscriptionSettings>
  fetchSummarizationSettings(): Promise<void>
  updateSummarizationSettings(payload: UpdateSummarizationSettingsRequest): Promise<SummarizationSettings>
  validateModelPath(request: ModelPathValidationRequest): Promise<ModelPathValidationResult>
  scanVibeVoiceServices(): Promise<VibeVoiceServiceScanResult[]>
  startVibeVoiceService(modelPath: string, port: number, dtype: string): Promise<void>
  stopVibeVoiceService(): Promise<void>
  fetchVibeVoiceServiceStatus(): Promise<VibeVoiceServiceStatus>
  testLlm(): Promise<{ status: string; message: string }>
  readBilibiliCookieFromBrowser(): Promise<BilibiliCookieFromBrowserResult>
  clearModelPathValidation(): void
}

// api adapter 注入点（规格 §4 方案 A：模块级单例；可选签名保留为未来 seam 扩展）
export type SettingsApiAdapter = typeof apiClient

export function useSettingsState(_options?: { api?: SettingsApiAdapter }): SettingsState {
  const llmProviders = ref<LLMProvider[]>([])
  const llmSettings = ref<LLMSettings | null>(null)
  const isUpdatingLlmSettings = ref(false)
  const transcriptionSettings = ref<TranscriptionSettings | null>(null)
  const isUpdatingTranscriptionSettings = ref(false)
  const summarizationSettings = ref<SummarizationSettings | null>(null)
  const isUpdatingSummarizationSettings = ref(false)
  const isReadingBilibiliCookieFromBrowser = ref(false)
  const modelPathValidationResult = ref<ModelPathValidationResult | null>(null)
  const isValidatingModelPath = ref(false)
  const vibevoiceInferenceMode = ref<'local' | 'api'>(
    transcriptionSettings.value?.vibevoice_inference_mode || 'local'
  )
  const vibevoiceApiUrl = ref(
    transcriptionSettings.value?.vibevoice_api_url || ''
  )
  const vibevoiceServiceStatus = ref<VibeVoiceServiceStatus | null>(null)
  const isScanningVibeVoice = ref(false)
  const isStartingVibeVoice = ref(false)
  const isStoppingVibeVoice = ref(false)
  const error = ref<string | null>(null)

  const clearModelPathValidation = () => {
    modelPathValidationResult.value = null
  }

  watch(transcriptionSettings, (settings) => {
    if (!settings) return
    vibevoiceInferenceMode.value = settings.vibevoice_inference_mode || 'local'
    vibevoiceApiUrl.value = settings.vibevoice_api_url || ''
  })

  const fetchLlmProviders = async () => {
    try {
      const response = await apiClient.get('/llm/providers')
      llmProviders.value = response.data
    } catch (err) {
      console.error('Failed to fetch LLM providers:', err)
      error.value = '获取 LLM 供应商列表失败'
    }
  }

  const fetchLlmSettings = async () => {
    try {
      const response = await apiClient.get('/llm/settings')
      llmSettings.value = response.data
    } catch (err) {
      console.error('Failed to fetch LLM settings:', err)
      error.value = '获取 LLM 配置失败'
    }
  }

  const updateLlmSettings = async (payload: UpdateLLMSettingsRequest) => {
    isUpdatingLlmSettings.value = true
    try {
      const response = await apiClient.put('/llm/settings', payload)
      llmSettings.value = response.data
      return response.data as LLMSettings
    } catch (err) {
      console.error('Failed to update LLM settings:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '更新 LLM 配置失败'
      } else {
        error.value = '更新 LLM 配置失败'
      }
      throw err
    } finally {
      isUpdatingLlmSettings.value = false
    }
  }

  const fetchTranscriptionSettings = async () => {
    try {
      const response = await apiClient.get('/transcription/settings')
      transcriptionSettings.value = response.data
    } catch (err) {
      console.error('Failed to fetch transcription settings:', err)
      error.value = '获取转录配置失败'
    }
  }

  const updateTranscriptionSettings = async (payload: UpdateTranscriptionSettingsRequest) => {
    isUpdatingTranscriptionSettings.value = true
    error.value = null
    try {
      const response = await apiClient.put('/transcription/settings', payload)
      transcriptionSettings.value = response.data
      return response.data as TranscriptionSettings
    } catch (err) {
      console.error('Failed to update transcription settings:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '更新转录配置失败'
      } else {
        error.value = '更新转录配置失败'
      }
      throw err
    } finally {
      isUpdatingTranscriptionSettings.value = false
    }
  }

  const validateModelPath = async (request: ModelPathValidationRequest): Promise<ModelPathValidationResult> => {
    isValidatingModelPath.value = true
    modelPathValidationResult.value = null
    try {
      const response = await apiClient.post('/transcription/settings/validate-model-path', request)
      modelPathValidationResult.value = response.data
      return response.data as ModelPathValidationResult
    } catch (err) {
      console.error('Failed to validate model path:', err)
      const result: ModelPathValidationResult = {
        valid: false,
        message: isAxiosError(err) && err.response?.data?.detail
          ? String(err.response.data.detail)
          : '验证请求失败',
        resolved_path: request.path,
        missing_files: [],
        has_processor_config: false,
        details: {},
      }
      modelPathValidationResult.value = result
      throw err
    } finally {
      isValidatingModelPath.value = false
    }
  }

  const scanVibeVoiceServices = async (): Promise<VibeVoiceServiceScanResult[]> => {
    isScanningVibeVoice.value = true
    try {
      const response = await apiClient.post('/transcription/settings/vibevoice-scan')
      return response.data as VibeVoiceServiceScanResult[]
    } catch (err) {
      console.error('Failed to scan VibeVoice services:', err)
      return []
    } finally {
      isScanningVibeVoice.value = false
    }
  }

  const startVibeVoiceService = async (modelPath: string, port: number, dtype: string): Promise<void> => {
    isStartingVibeVoice.value = true
    try {
      await apiClient.post('/transcription/settings/vibevoice-service/start', {
        model_path: modelPath,
        port,
        dtype,
      })
    } finally {
      isStartingVibeVoice.value = false
    }
  }

  const stopVibeVoiceService = async (): Promise<void> => {
    isStoppingVibeVoice.value = true
    try {
      await apiClient.post('/transcription/settings/vibevoice-service/stop')
    } finally {
      isStoppingVibeVoice.value = false
    }
  }

  const fetchVibeVoiceServiceStatus = async (): Promise<VibeVoiceServiceStatus> => {
    const response = await apiClient.get('/transcription/settings/vibevoice-service/status')
    vibevoiceServiceStatus.value = response.data
    return response.data as VibeVoiceServiceStatus
  }

  const testLlm = async () => {
    try {
      const response = await apiClient.post('/llm/test')
      return response.data
    } catch (err) {
      console.error('Failed to test LLM:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '测试 LLM 失败'
      } else {
        error.value = '测试 LLM 失败'
      }
      throw err
    }
  }

  const fetchSummarizationSettings = async () => {
    try {
      const response = await apiClient.get('/summarization/settings')
      summarizationSettings.value = response.data
    } catch (err) {
      console.error('Failed to fetch summarization settings:', err)
      error.value = '获取总结配置失败'
    }
  }

  const updateSummarizationSettings = async (payload: UpdateSummarizationSettingsRequest) => {
    isUpdatingSummarizationSettings.value = true
    try {
      const response = await apiClient.put('/summarization/settings', payload)
      summarizationSettings.value = response.data
      return response.data as SummarizationSettings
    } catch (err) {
      console.error('Failed to update summarization settings:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '更新总结配置失败'
      } else {
        error.value = '更新总结配置失败'
      }
      throw err
    } finally {
      isUpdatingSummarizationSettings.value = false
    }
  }

  const readBilibiliCookieFromBrowser = async (): Promise<BilibiliCookieFromBrowserResult> => {
    isReadingBilibiliCookieFromBrowser.value = true
    try {
      const response = await apiClient.post('/transcription/settings/bilibili-cookie/from-browser')
      const result = response.data as BilibiliCookieFromBrowserResult
      if (result.success) {
        // Refresh transcription settings to reflect the new cookie
        await fetchTranscriptionSettings()
      }
      return result
    } catch (err) {
      console.error('Failed to read Bilibili cookie from browser:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '从浏览器读取 Cookie 失败'
      } else {
        error.value = '从浏览器读取 Cookie 失败'
      }
      throw err
    } finally {
      isReadingBilibiliCookieFromBrowser.value = false
    }
  }

  return {
    llmProviders,
    llmSettings,
    isUpdatingLlmSettings,
    transcriptionSettings,
    isUpdatingTranscriptionSettings,
    summarizationSettings,
    isUpdatingSummarizationSettings,
    isReadingBilibiliCookieFromBrowser,
    modelPathValidationResult,
    isValidatingModelPath,
    vibevoiceInferenceMode,
    vibevoiceApiUrl,
    vibevoiceServiceStatus,
    isScanningVibeVoice,
    isStartingVibeVoice,
    isStoppingVibeVoice,
    error,
    fetchLlmProviders,
    fetchLlmSettings,
    updateLlmSettings,
    fetchTranscriptionSettings,
    updateTranscriptionSettings,
    validateModelPath,
    scanVibeVoiceServices,
    startVibeVoiceService,
    stopVibeVoiceService,
    fetchVibeVoiceServiceStatus,
    testLlm,
    fetchSummarizationSettings,
    updateSummarizationSettings,
    readBilibiliCookieFromBrowser,
    clearModelPathValidation,
  }
}
