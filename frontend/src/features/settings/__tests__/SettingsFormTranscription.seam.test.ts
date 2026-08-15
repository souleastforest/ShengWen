/**
 * P6 seam 契约测试：features/settings/components/SettingsFormTranscription.vue
 *
 * Q8 语义以弹窗（SettingsModal）为准：字段/夹逼/默认值统一到弹窗语义
 * （含 transcriber_type / vibevoice_* 字段与模型路径校验）。
 * 契约面：
 * - props: transcriptionSettings / isUpdatingTranscriptionSettings /
 *   isReadingBilibiliCookieFromBrowser / modelPathValidationResult /
 *   isValidatingModelPath / vibevoiceServiceStatus / isScanningVibeVoice /
 *   isStartingVibeVoice / isStoppingVibeVoice / clearModelPathValidation / isOpen
 * - emits: updateTranscriptionSettings / readBilibiliCookieFromBrowser /
 *   validateModelPath / scanVibeVoiceServices / startVibeVoiceService /
 *   stopVibeVoiceService / fetchVibeVoiceServiceStatus
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsFormTranscription from '../components/SettingsFormTranscription.vue'
import type {
  TranscriptionSettings,
  ModelPathValidationResult,
  VibeVoiceServiceStatus,
} from '../../../types'

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
  required_model_files: ['config.json', 'model.bin'],
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

const baseProps = {
  transcriptionSettings: createTranscriptionSettings(),
  isUpdatingTranscriptionSettings: false,
  isReadingBilibiliCookieFromBrowser: false,
  modelPathValidationResult: null as ModelPathValidationResult | null,
  isValidatingModelPath: false,
  vibevoiceServiceStatus: null as VibeVoiceServiceStatus | null,
  isScanningVibeVoice: false,
  isStartingVibeVoice: false,
  isStoppingVibeVoice: false,
  clearModelPathValidation: vi.fn(),
  isOpen: true,
}

const mountForm = (overrides: Record<string, unknown> = {}) =>
  mount(SettingsFormTranscription, {
    props: { ...baseProps, transcriptionSettings: createTranscriptionSettings(), ...overrides },
  })

const findButtonByText = (wrapper: VueWrapper, text: string) => {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().includes(text))
  expect(button, `Expected button containing "${text}"`).toBeTruthy()
  return button!
}

const switchToVibeVoice = async (wrapper: VueWrapper) => {
  await findButtonByText(wrapper, 'VibeVoice ASR').trigger('click')
}

describe('SettingsFormTranscription seam：默认 FastWhisper 视图', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('默认显示 FastWhisper 配置（自动下载/手动目录/模型大小），不含 VibeVoice 字段', () => {
    const wrapper = mountForm()
    expect(wrapper.text()).toContain('自动下载')
    expect(wrapper.text()).not.toContain('VibeVoice 模型目录')
    expect(wrapper.text()).not.toContain('语言模型')
  })

  it('无 CUDA 时 VibeVoice ASR 按钮禁用', () => {
    const wrapper = mountForm()
    const vibeVoiceButton = wrapper
      .findAll('button')
      .find((b) => b.text().includes('VibeVoice ASR'))!
    expect((vibeVoiceButton.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('API 推理模式下 VibeVoice ASR 可用（无 CUDA 也可选）', () => {
    const wrapper = mountForm({
      transcriptionSettings: createTranscriptionSettings({
        cuda_available: false,
        vibevoice_inference_mode: 'api',
      }),
    })
    const vibeVoiceButton = wrapper
      .findAll('button')
      .find((b) => b.text().includes('VibeVoice ASR'))!
    expect((vibeVoiceButton.element as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('SettingsFormTranscription seam：保存 payload（弹窗语义）', () => {
  it('FastWhisper 保存 emit updateTranscriptionSettings（含 transcriber_type）', async () => {
    const wrapper = mountForm({
      transcriptionSettings: createTranscriptionSettings({ model_source: 'manual_path' }),
    })
    await findButtonByText(wrapper, '保存设置').trigger('click')

    const emits = wrapper.emitted('updateTranscriptionSettings')!
    expect(emits).toHaveLength(1)
    expect(emits![0]![0]).toEqual({
      device: 'cpu',
      transcriber_type: 'fast_whisper',
      model_path: '',
      enable_bilibili_subtitle_fetch: false,
      model_source: 'manual_path',
      model_size: 'tiny',
    })
  })

  it('填写 B 站 SESSDATA 后保存携带 bilibili_sessdata 并清空输入', async () => {
    const wrapper = mountForm()
    const sessdataInput = wrapper.findAll('input[type="password"]')[0]!
    await sessdataInput.setValue('sessdata-123')
    await findButtonByText(wrapper, '保存设置').trigger('click')

    const emits = wrapper.emitted('updateTranscriptionSettings')!
    expect(emits![0]![0]).toMatchObject({ bilibili_sessdata: 'sessdata-123' })
    expect((sessdataInput.element as HTMLInputElement).value).toBe('')
  })

  it('VibeVoice 保存携带 vibevoice_* 字段且 model_source 固定 manual_path', async () => {
    const wrapper = mountForm({
      transcriptionSettings: createTranscriptionSettings({ cuda_available: true }),
    })
    await switchToVibeVoice(wrapper)
    await findButtonByText(wrapper, '保存设置').trigger('click')

    const emits = wrapper.emitted('updateTranscriptionSettings')!
    expect(emits![0]![0]).toMatchObject({
      transcriber_type: 'vibe_voice_asr',
      model_source: 'manual_path',
      vibevoice_language_model: 'Qwen/Qwen2.5-7B',
      vibevoice_max_new_tokens: 16384,
      vibevoice_dtype: 'bfloat16',
      vibevoice_inference_mode: 'local',
      vibevoice_api_url: '',
    })
    // FastWhisper 专属字段不携带
    expect(emits![0]![0]).not.toHaveProperty('model_size')
  })

  it('清空已保存 Cookie emit { clear_bilibili_sessdata: true }', async () => {
    const wrapper = mountForm({
      transcriptionSettings: createTranscriptionSettings({ has_bilibili_sessdata: true }),
    })
    await findButtonByText(wrapper, '清空已保存 Cookie').trigger('click')
    const emits = wrapper.emitted('updateTranscriptionSettings')!
    expect(emits![0]![0]).toEqual({ clear_bilibili_sessdata: true })
  })
})

describe('SettingsFormTranscription seam：模型路径校验与 B 站 Cookie', () => {
  it('验证路径按钮 emit validateModelPath（含 transcriber_type）', async () => {
    const wrapper = mountForm({
      transcriptionSettings: createTranscriptionSettings({ cuda_available: true }),
    })
    await switchToVibeVoice(wrapper)
    const pathInput = wrapper.findAll('input').find((i) => (i.element as HTMLInputElement).value === '')!
    await pathInput.setValue('/models/vibevoice')
    await findButtonByText(wrapper, '验证路径').trigger('click')

    const emits = wrapper.emitted('validateModelPath')!
    expect(emits![0]![0]).toEqual({
      path: '/models/vibevoice',
      transcriber_type: 'vibe_voice_asr',
    })
  })

  it('校验结果徽标展示', () => {
    const wrapper = mountForm({
      transcriptionSettings: createTranscriptionSettings({ cuda_available: true, transcriber_type: 'vibe_voice_asr' }),
      modelPathValidationResult: { valid: true, message: '模型文件齐全', resolved_path: '/x', missing_files: [], has_processor_config: true, details: {} },
    })
    expect(wrapper.text()).toContain('路径有效')
  })

  it('切换转录器类型触发 clearModelPathValidation', async () => {
    const clearModelPathValidation = vi.fn()
    const wrapper = mountForm({
      transcriptionSettings: createTranscriptionSettings({ cuda_available: true }),
      clearModelPathValidation,
    })
    await switchToVibeVoice(wrapper)
    expect(clearModelPathValidation).toHaveBeenCalled()
  })

  it('从浏览器读取按钮 emit readBilibiliCookieFromBrowser', async () => {
    const wrapper = mountForm()
    await findButtonByText(wrapper, '从浏览器读取').trigger('click')
    expect(wrapper.emitted('readBilibiliCookieFromBrowser')).toHaveLength(1)
  })

  it('isOpen 且 API 推理模式时 emit fetchVibeVoiceServiceStatus', () => {
    const wrapper = mountForm({
      transcriptionSettings: createTranscriptionSettings({ vibevoice_inference_mode: 'api' }),
    })
    // 立即 watcher 触发
    expect(wrapper.emitted('fetchVibeVoiceServiceStatus')).toBeTruthy()
  })

  it('本地推理模式不 emit fetchVibeVoiceServiceStatus', () => {
    const wrapper = mountForm()
    expect(wrapper.emitted('fetchVibeVoiceServiceStatus')).toBeUndefined()
  })
})
