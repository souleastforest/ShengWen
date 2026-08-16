/**
 * P7 seam 契约测试：features/upload/state.ts
 *
 * 规格（p7-composable-spec.md §3.2）逐键断言：
 * - 导出面形状：UploadState 全部 state refs + actions + DEFAULT_MAX_UPLOAD_BYTES；
 * - 三通道 payload 形状（quality='audio_only' 显式常量、generate_topic 仅
 *   none 模式发送、multipart FormData）；
 * - cancelSubmitting 语义（abort + isSubmitting/uploadProgress 复位）；
 * - uploadMaxBytes 对账（fetchUploadConfig 成功应用 / 失败静默回退默认值）；
 * - isBilibiliUrl 判定（bilibili.com / b23.tv / 非 B 站）。
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import type { AxiosRequestConfig } from 'axios'
import { DEFAULT_MAX_UPLOAD_BYTES, useUploadState } from '../state'
import type { UploadState } from '../state'

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

const mountUploadState = () => {
  let upload!: UploadState
  const TestComponent = defineComponent({
    setup() {
      upload = useUploadState()
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  return { upload, wrapper }
}

const setup = () => {
  vi.clearAllMocks()
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  mockedAxios.get.mockResolvedValue({ data: [] })
  mockedAxios.post.mockResolvedValue({ data: {} })
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
  vi.stubGlobal('WebSocket', vi.fn(function () { return { close: vi.fn() } }))
}

describe('P7 seam：features/upload/state.ts 导出面与语义', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setup)

  it('导出面形状：state refs / actions / DEFAULT_MAX_UPLOAD_BYTES 逐键存在', () => {
    const { upload } = mountUploadState()

    for (const key of ['videoUrl', 'selectedFile', 'localFilePath', 'isLocalClient', 'summaryMode', 'generateTopic', 'isSubmitting', 'uploadProgress', 'uploadMaxBytes', 'error']) {
      expect(upload[key as keyof UploadState]).toBeDefined()
      expect((upload[key as keyof UploadState] as { value: unknown }).value).toBeDefined()
    }
    for (const key of [
      'submitTask', 'submitLocalPathTask', 'uploadFile', 'cancelSubmitting',
      'fetchUploadConfig', 'submitLocalPathTasks', 'submitTaskWithParts',
      'checkBilibiliVideoInfo', 'checkLocalPath', 'scanLocalFolder', 'isBilibiliUrl',
    ]) {
      expect(typeof upload[key as keyof UploadState]).toBe('function')
    }
    expect(DEFAULT_MAX_UPLOAD_BYTES).toBe(2 * 1024 * 1024 * 1024)
    expect(upload.summaryMode.value).toBe('none') // UI 三态默认仅转录
    expect(upload.generateTopic.value).toBe(true)
  })

  it('URL 提交 payload：quality="audio_only" 显式常量；none 模式携带 generate_topic；standard 不携带', async () => {
    const { upload, wrapper } = mountUploadState()

    upload.videoUrl.value = 'https://example.com/video.mp4'
    await upload.submitTask()
    const nonePayload = mockedAxios.post.mock.calls.find(([u]) => u === '/tasks/')![1] as Record<string, unknown>
    expect(nonePayload).toEqual(expect.objectContaining({
      video_url: 'https://example.com/video.mp4',
      quality: 'audio_only',
      summary_mode: 'none',
      generate_topic: true,
    }))

    mockedAxios.post.mockClear()
    upload.summaryMode.value = 'standard'
    upload.videoUrl.value = 'https://example.com/video.mp4'
    await upload.submitTask()
    const standardPayload = mockedAxios.post.mock.calls.find(([u]) => u === '/tasks/')![1] as Record<string, unknown>
    expect(standardPayload.summary_mode).toBe('standard')
    expect('generate_topic' in standardPayload).toBe(false)

    wrapper.unmount()
  })

  it('文件上传 FormData：summary_mode 与 generate_topic（none）随 multipart 发送', async () => {
    const { upload, wrapper } = mountUploadState()

    upload.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
    await upload.submitTask()

    const [, formData] = mockedAxios.post.mock.calls.find(([u]) => u === '/upload')!
    expect(formData).toBeInstanceOf(FormData)
    expect((formData as FormData).get('summary_mode')).toBe('none')
    expect((formData as FormData).get('generate_topic')).toBe('true')

    wrapper.unmount()
  })

  it('cancelSubmitting：abort 控制器 + isSubmitting/uploadProgress 复位', async () => {
    const { upload, wrapper } = mountUploadState()
    let capturedSignal: AbortSignal | null = null
    mockedAxios.post.mockImplementation((_url: string, _body: unknown, config?: AxiosRequestConfig) => {
      const signal = config?.signal as AbortSignal | undefined
      capturedSignal = signal ?? null
      return new Promise((_resolve, reject) => {
        signal?.addEventListener('abort', () => {
          const err = new Error('canceled')
          Object.assign(err, { code: 'ERR_CANCELED' })
          reject(err)
        })
      })
    })

    upload.videoUrl.value = 'https://example.com/video.mp4'
    const pending = upload.submitTask()
    expect(upload.isSubmitting.value).toBe(true)
    expect(capturedSignal).not.toBeNull()

    upload.cancelSubmitting()
    await pending // isCanceledRequest 命中 → 静默返回

    expect(upload.isSubmitting.value).toBe(false)
    expect(upload.uploadProgress.value).toBe(0)

    wrapper.unmount()
  })

  it('fetchUploadConfig：成功应用后端上限；失败静默保留当前值（初始默认 DEFAULT_MAX_UPLOAD_BYTES）', async () => {
    const { upload, wrapper } = mountUploadState()

    // 初始未拉取过：失败静默回退默认值
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).endsWith('/upload/config')) {
        return Promise.reject(new Error('network down'))
      }
      return Promise.resolve({ data: [] })
    })
    await upload.fetchUploadConfig()
    expect(upload.uploadMaxBytes.value).toBe(DEFAULT_MAX_UPLOAD_BYTES)

    // 成功应用后端上限
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).endsWith('/upload/config')) {
        return Promise.resolve({ data: { max_upload_mb: 1024 } })
      }
      return Promise.resolve({ data: [] })
    })
    await upload.fetchUploadConfig()
    expect(upload.uploadMaxBytes.value).toBe(1024 * 1024 * 1024)

    // 再次失败：静默保留已生效值（不阻塞 UI，不误回退）
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).endsWith('/upload/config')) {
        return Promise.reject(new Error('network down'))
      }
      return Promise.resolve({ data: [] })
    })
    await upload.fetchUploadConfig()
    expect(upload.uploadMaxBytes.value).toBe(1024 * 1024 * 1024)

    wrapper.unmount()
  })

  it('isBilibiliUrl：bilibili.com / b23.tv 命中；其他域名不命中', () => {
    const { upload } = mountUploadState()
    expect(upload.isBilibiliUrl('https://www.bilibili.com/video/BV1xx')).toBe(true)
    expect(upload.isBilibiliUrl('https://b23.tv/abc')).toBe(true)
    expect(upload.isBilibiliUrl('https://example.com/video')).toBe(false)
    expect(upload.isBilibiliUrl('not a url')).toBe(false)
  })
})
