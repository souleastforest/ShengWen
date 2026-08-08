import { expect, test, type Page } from '@playwright/test'

type TranscriptionSettings = {
  device: 'cpu' | 'cuda'
  model_source: 'auto_download' | 'manual_path'
  model_size: 'tiny' | 'base' | 'small' | 'medium' | 'large'
  model_path: string
  model_path_valid: boolean
  model_path_message: string
  model_path_resolved: string
  required_model_files: string[]
  cuda_available: boolean
  available_devices: Array<'cpu' | 'cuda'>
  has_nvidia_gpu: boolean
  torch_installed: boolean
  torch_cuda_built: boolean
  ctranslate2_installed: boolean
  ctranslate2_cuda_device_count: number
  cuda_reason: string
  cuda_message: string
  enable_bilibili_subtitle_fetch: boolean
  has_bilibili_sessdata: boolean
  bilibili_cookie_source: string
  bilibili_sessdata_masked: string
  transcriber_type: 'fast_whisper' | 'vibe_voice_asr'
  vibevoice_language_model: string
  vibevoice_max_new_tokens: number
  vibevoice_dtype: 'bfloat16' | 'float16'
  vibevoice_inference_mode: 'local' | 'api'
  vibevoice_api_url: string
}

const defaultTranscriptionSettings = (
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
  vibevoice_max_new_tokens: 8192,
  vibevoice_dtype: 'bfloat16',
  vibevoice_inference_mode: 'local',
  vibevoice_api_url: '',
  ...overrides,
})

async function mockAppApi(page: Page, transcriptionSettings = defaultTranscriptionSettings()) {
  await page.route('**/tasks/', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await page.route('**/llm/providers', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
  })

  await page.route('**/llm/settings', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        provider: 'openai',
        base_url: 'https://api.example.com/v1',
        model_id: 'gpt-4.1-mini',
        temperature: 0.7,
        extra_headers: {},
      }),
    })
  })

  await page.route('**/summarization/settings', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        chunk_target_duration_sec: 1200,
        chunk_min_duration_sec: 600,
        chunk_max_duration_sec: 1800,
        boundary_jump_sec: 10,
        auto_chunk_min_audio_duration_sec: 2400,
        auto_chunk_min_transcript_lines: 1800,
        max_agent_value_chars: 500,
        fallback_to_standard_on_agent_error: true,
      }),
    })
  })

  await page.route('**/transcription/settings/validate-model-path', async (route) => {
    const request = route.request().postDataJSON() as { path?: string }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        valid: true,
        message: '验证成功',
        resolved_path: request.path || '/models/vibevoice-asr',
        missing_files: [],
        has_processor_config: true,
        details: {},
      }),
    })
  })

  await page.route('**/transcription/settings/vibevoice-scan', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
  })

  await page.route('**/transcription/settings/vibevoice-service/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        running: false,
        pid: null,
        port: null,
        api_healthy: false,
      }),
    })
  })

  await page.route('**/transcription/settings', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(transcriptionSettings),
      })
      return
    }

    const payload = route.request().postDataJSON()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...transcriptionSettings, ...payload }),
    })
  })
}

async function openSettings(page: Page, transcriptionSettings = defaultTranscriptionSettings()) {
  await mockAppApi(page, transcriptionSettings)
  await page.goto('/')
  await page.locator('button[title="设置"]').click()
  await expect(page.getByText('LLM 配置').last()).toBeVisible()
}

async function openTranscriptionTab(page: Page, transcriptionSettings = defaultTranscriptionSettings()) {
  await openSettings(page, transcriptionSettings)
  await page.getByRole('button', { name: '转录设置' }).click()
  await expect(page.getByRole('heading', { name: '硬件配置' })).toBeVisible()
}

function fieldControl(page: Page, label: string, control: 'input' | 'select') {
  return page.locator(`label:has-text("${label}")`).locator('..').locator(control)
}

test('Settings modal opens and shows LLM tab by default', async ({ page }) => {
  await openSettings(page)

  await expect(page.getByRole('button', { name: 'LLM 配置' })).toHaveClass(/bg-white/)
  await expect(page.getByText('LLM 配置').last()).toBeVisible()
})

test('Switch to transcription tab', async ({ page }) => {
  await openTranscriptionTab(page)

  await expect(page.getByRole('heading', { name: '硬件配置' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '模型配置' })).toBeVisible()
})

test('VibeVoice ASR button state depends on CUDA/API mode', async ({ page }) => {
  await openTranscriptionTab(page)

  const vibeVoiceButton = page.getByRole('button', { name: /VibeVoice ASR/ })
  await expect(vibeVoiceButton).toBeVisible()
  await expect(vibeVoiceButton).toBeDisabled()
  await expect(vibeVoiceButton).toContainText('需要 CUDA 环境')
})

test('Inference mode toggle appears when VibeVoice is selected', async ({ page }) => {
  await openTranscriptionTab(
    page,
    defaultTranscriptionSettings({
      cuda_available: true,
      available_devices: ['cpu', 'cuda'],
      has_nvidia_gpu: true,
      torch_cuda_built: true,
      ctranslate2_cuda_device_count: 1,
    }),
  )

  await page.getByRole('button', { name: /VibeVoice ASR/ }).click()
  await expect(page.getByRole('button', { name: '本地加载' })).toBeVisible()
  await expect(page.getByRole('button', { name: '推理服务' })).toBeVisible()

  await page.getByRole('button', { name: '推理服务' }).click()
  await expect(fieldControl(page, '推理服务地址', 'input')).toBeVisible()
})

test('Local mode shows CUDA-specific fields', async ({ page }) => {
  await openTranscriptionTab(
    page,
    defaultTranscriptionSettings({
      cuda_available: true,
      available_devices: ['cpu', 'cuda'],
      has_nvidia_gpu: true,
      torch_cuda_built: true,
      ctranslate2_cuda_device_count: 1,
    }),
  )

  await page.getByRole('button', { name: /VibeVoice ASR/ }).click()
  await page.getByRole('button', { name: '本地加载' }).click()

  await expect(fieldControl(page, '语言模型', 'input')).toBeVisible()
  await expect(fieldControl(page, '最大生成 Token 数', 'input')).toBeVisible()
  await expect(fieldControl(page, '数据类型', 'select')).toBeVisible()
  await expect(page.getByText('VibeVoice ASR 需要 CUDA 环境。请确保 GPU 可用且显存充足')).toBeVisible()
})
