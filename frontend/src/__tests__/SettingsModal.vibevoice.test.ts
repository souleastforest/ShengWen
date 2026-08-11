import { describe, it, expect, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import SettingsModal from '../components/SettingsModal.vue'
import type {
  TranscriptionSettings,
  ModelPathValidationResult,
  LLMProvider,
  LLMSettings,
  SummarizationSettings,
} from '../types'

const createTranscriptionSettings = (
  overrides: Partial<TranscriptionSettings> = {},
): TranscriptionSettings => ({
  device: 'cpu',
  model_source: 'auto_download',
  model_size: 'tiny',
  model_path: '',
  model_path_valid: false,
  model_path_message: '',
  model_path_resolved: '',
  required_model_files: ['config.json'],
  cuda_available: false,
  available_devices: ['cpu'],
  has_nvidia_gpu: false,
  torch_installed: true,
  torch_cuda_built: false,
  ctranslate2_installed: true,
  ctranslate2_cuda_device_count: 0,
  cuda_reason: '',
  cuda_message: 'No CUDA',
  enable_bilibili_subtitle_fetch: false,
  has_bilibili_sessdata: false,
  bilibili_cookie_source: '',
  bilibili_sessdata_masked: '',
  transcriber_type: 'fast_whisper',
  vibevoice_language_model: 'Qwen/Qwen2.5-7B',
  vibevoice_max_new_tokens: 16384,
  vibevoice_dtype: 'bfloat16',
  vibevoice_inference_mode: 'local',
  vibevoice_api_url: '',
  ...overrides,
})

const defaultProps = {
  isOpen: true,
  llmProviders: [] as LLMProvider[],
  llmSettings: null as LLMSettings | null,
  isUpdatingLlmSettings: false,
  isTestingLlm: false,
  transcriptionSettings: createTranscriptionSettings(),
  isUpdatingTranscriptionSettings: false,
  summarizationSettings: null as SummarizationSettings | null,
  isUpdatingSummarizationSettings: false,
  isReadingBilibiliCookieFromBrowser: false,
  modelPathValidationResult: null as ModelPathValidationResult | null,
  isValidatingModelPath: false,
  vibevoiceServiceStatus: null,
  isScanningVibeVoice: false,
  isStartingVibeVoice: false,
  isStoppingVibeVoice: false,
  clearModelPathValidation: vi.fn(),
}

const globalStubs = {
  PhX: { template: '<div />' },
  PhCpu: { template: '<div />' },
  PhKey: { template: '<div />' },
  PhFlask: { template: '<div />' },
  PhSpinner: { template: '<div />' },
  PhBrain: { template: '<div />' },
  PhMicrophone: { template: '<div />' },
  PhGitBranch: { template: '<div />' },
  Transition: false,
}

function createWrapper(overrides: Partial<typeof defaultProps> = {}) {
  return mount(SettingsModal, {
    props: {
      ...defaultProps,
      transcriptionSettings: createTranscriptionSettings(),
      clearModelPathValidation: vi.fn(),
      ...overrides,
    },
    global: { stubs: globalStubs },
  })
}

function findButtonByText(wrapper: VueWrapper, text: string) {
  const button = wrapper
    .findAll('button')
    .find((candidate) => candidate.text().includes(text))

  expect(button, `Expected to find button containing "${text}"`).toBeTruthy()
  return button!
}

async function openTranscriptionTab(wrapper: VueWrapper) {
  await findButtonByText(wrapper, '转录设置').trigger('click')
}

async function switchToVibeVoice(wrapper: VueWrapper) {
  await findButtonByText(wrapper, 'VibeVoice ASR').trigger('click')
}

describe('SettingsModal VibeVoice UI', () => {
  it('shows FastWhisper UI by default', async () => {
    const wrapper = createWrapper()

    await openTranscriptionTab(wrapper)

    expect(wrapper.text()).toContain('自动下载')
    expect(wrapper.text()).not.toContain('VibeVoice 模型目录')
    expect(wrapper.text()).not.toContain('语言模型')
    expect(wrapper.text()).not.toContain('数据类型')
  })

  it('switches to VibeVoice UI when VibeVoice ASR button is clicked', async () => {
    const wrapper = createWrapper({
      transcriptionSettings: createTranscriptionSettings({
        cuda_available: true,
      }),
    })

    await openTranscriptionTab(wrapper)
    await switchToVibeVoice(wrapper)

    expect(wrapper.text()).not.toContain('转录模型来源')
    expect(wrapper.text()).toContain('VibeVoice 模型目录')
    expect(wrapper.text()).toContain('语言模型')
    expect(wrapper.text()).toContain('数据类型')
    expect(wrapper.text()).toContain('VibeVoice ASR 需要 CUDA 环境')
  })

  it('shows validation badge after validation result is set', async () => {
    const wrapper = createWrapper({
      transcriptionSettings: createTranscriptionSettings({
        transcriber_type: 'vibe_voice_asr',
        cuda_available: true,
      }),
      modelPathValidationResult: {
        valid: true,
        message: '验证成功',
        resolved_path: '/models/vibevoice',
        missing_files: [],
        has_processor_config: true,
        details: {},
      },
    })

    await openTranscriptionTab(wrapper)

    expect(wrapper.text()).toContain('路径有效')
  })

  it('shows error badge when validation fails', async () => {
    const wrapper = createWrapper({
      transcriptionSettings: createTranscriptionSettings({
        transcriber_type: 'vibe_voice_asr',
        cuda_available: true,
      }),
      modelPathValidationResult: {
        valid: false,
        message: '缺少模型文件',
        resolved_path: '/bad/path',
        missing_files: ['config.json'],
        has_processor_config: false,
        details: {},
      },
    })

    await openTranscriptionTab(wrapper)

    expect(wrapper.text()).toContain('路径无效')
  })

  it('validate button emits validateModelPath event', async () => {
    const wrapper = createWrapper({
      transcriptionSettings: createTranscriptionSettings({
        transcriber_type: 'vibe_voice_asr',
        cuda_available: true,
      }),
    })

    await openTranscriptionTab(wrapper)

    const modelPathInput = wrapper.get('input[placeholder="例如: /models/vibevoice-asr"]')
    await modelPathInput.setValue('/models/vibevoice-asr')
    await findButtonByText(wrapper, '验证路径').trigger('click')

    expect(wrapper.emitted('validateModelPath')).toEqual([
      [{ path: '/models/vibevoice-asr', transcriber_type: 'vibe_voice_asr' }],
    ])
  })

  it('save emits correct payload for FastWhisper', async () => {
    const wrapper = createWrapper()

    await openTranscriptionTab(wrapper)
    await findButtonByText(wrapper, '保存设置').trigger('click')

    expect(wrapper.emitted('updateTranscriptionSettings')).toEqual([
      [{
        device: 'cpu',
        transcriber_type: 'fast_whisper',
        model_path: '',
        enable_bilibili_subtitle_fetch: false,
        model_source: 'auto_download',
        model_size: 'tiny',
      }],
    ])
  })

  it('save emits correct payload for VibeVoice', async () => {
    const wrapper = createWrapper({
      transcriptionSettings: createTranscriptionSettings({
        cuda_available: true,
      }),
    })

    await openTranscriptionTab(wrapper)
    await switchToVibeVoice(wrapper)
    await findButtonByText(wrapper, '保存设置').trigger('click')

    expect(wrapper.emitted('updateTranscriptionSettings')).toEqual([
      [{
        device: 'cuda',
        transcriber_type: 'vibe_voice_asr',
        model_path: '',
        enable_bilibili_subtitle_fetch: false,
        model_source: 'manual_path',
        vibevoice_language_model: 'Qwen/Qwen2.5-7B',
        vibevoice_max_new_tokens: 16384,
        vibevoice_dtype: 'bfloat16',
        vibevoice_inference_mode: 'local',
        vibevoice_api_url: '',
      }],
    ])
  })
})
