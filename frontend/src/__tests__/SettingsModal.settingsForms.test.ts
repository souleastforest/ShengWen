/**
 * P6 SettingsModal 设置保存交互（活动入口）+ 双入口表单一致性
 *
 * Sidebar 内联设置面板为休眠 UI（isSettingsPanelOpen 恒 false，无打开入口），
 * 活动设置入口为 SettingsModal —— Q8 语义以弹窗为准。本测试覆盖代表性交互
 * "设置保存"：三个 tab 的保存按钮经 SettingsModal seam 发出正确 payload，
 * 且与 Sidebar 面板共用同一 SettingsForm×3 组件（双入口复用）。
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsModal from '../components/SettingsModal.vue'
import SettingsFormLlm from '../features/settings/components/SettingsFormLlm.vue'
import SettingsFormTranscription from '../features/settings/components/SettingsFormTranscription.vue'
import SettingsFormSummarization from '../features/settings/components/SettingsFormSummarization.vue'
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
    props: { ...defaultProps, ...overrides },
    global: { stubs: globalStubs },
  })
}

function findButtonByText(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().includes(text))
  expect(button, `Expected to find button containing "${text}"`).toBeTruthy()
  return button!
}

async function openTab(wrapper: VueWrapper, tabText: string) {
  await findButtonByText(wrapper, tabText).trigger('click')
}

describe('SettingsModal 设置保存交互', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('LLM tab：保存配置经 seam emit updateLlmSettings', async () => {
    const wrapper = createWrapper({
      llmProviders: [
        { id: 'openai', label: 'OpenAI', default_base_url: 'https://api.openai.com/v1', default_model_id: 'gpt-4o', description: '' },
      ],
      llmSettings: {
        provider: 'openai', base_url: 'https://api.openai.com/v1', model_id: 'gpt-4o',
        temperature: 0.7, context_window_size: 128000, has_api_key: false, api_key_hint: '', extra_headers: {},
      },
    })
    await openTab(wrapper, 'LLM 配置')
    await findButtonByText(wrapper, '保存配置').trigger('click')

    const emits = wrapper.emitted('updateLlmSettings')!
    expect(emits[0]![0]).toMatchObject({ provider: 'openai' })
  })

  it('LLM tab：测试连接经 seam emit updateLlmSettingsAndTest', async () => {
    const wrapper = createWrapper()
    await openTab(wrapper, 'LLM 配置')
    await findButtonByText(wrapper, '测试连接').trigger('click')
    expect(wrapper.emitted('updateLlmSettingsAndTest')).toHaveLength(1)
  })

  it('转录 tab：保存配置经 seam emit updateTranscriptionSettings', async () => {
    const wrapper = createWrapper()
    await openTab(wrapper, '转录设置')
    await findButtonByText(wrapper, '保存设置').trigger('click')

    const emits = wrapper.emitted('updateTranscriptionSettings')!
    expect(emits[0]![0]).toMatchObject({
      device: 'cpu',
      transcriber_type: 'fast_whisper',
      enable_bilibili_subtitle_fetch: false,
    })
  })

  it('Agent tab：保存配置经 seam emit updateSummarizationSettings', async () => {
    const wrapper = createWrapper({
      summarizationSettings: {
        mode: 'agent',
        auto_chunk_min_audio_duration_sec: 2400,
        auto_chunk_min_transcript_lines: 1800,
        chunk_target_duration_sec: 1200,
        chunk_min_duration_sec: 600,
        chunk_max_duration_sec: 1800,
        boundary_jump_sec: 10,
        prev_tail_timestamp_lines_m: 5,
        prev_summary_tail_chars_j: 300,
        llm_call_retry_max: 3,
        max_agent_value_chars: 500,
        fallback_to_standard_on_agent_error: true,
      },
    })
    await openTab(wrapper, 'Agent 设置')
    await findButtonByText(wrapper, '保存设置').trigger('click')

    const emits = wrapper.emitted('updateSummarizationSettings')!
    expect(emits[0]![0]).toMatchObject({
      chunk_target_duration_sec: 1200,
      chunk_min_duration_sec: 600,
      chunk_max_duration_sec: 1800,
    })
  })

  it('双入口复用：SettingsModal 渲染的是共享 SettingsForm×3 组件', async () => {
    const wrapper = createWrapper()
    expect(wrapper.findComponent(SettingsFormLlm).exists()).toBe(true)
    await openTab(wrapper, '转录设置')
    expect(wrapper.findComponent(SettingsFormTranscription).exists()).toBe(true)
    await openTab(wrapper, 'Agent 设置')
    expect(wrapper.findComponent(SettingsFormSummarization).exists()).toBe(true)
  })
})
