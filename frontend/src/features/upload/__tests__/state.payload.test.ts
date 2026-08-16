/**
 * P7 迁移：仅转录模式（需求 B）前端 payload —— upload 域
 * 原文件 useTaskViewModel.adversarial.test.ts [B5]（4 用例）+
 * useTaskViewModel.p1Defenses.test.ts [P1-5] quality 死绑定清理（1 用例）。
 * 用例逻辑原样保留，仅改引用（useTaskViewModel → useUploadState）与 setup。
 */
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { useUploadState } from '../state'
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
  vi.stubGlobal('WebSocket', vi.fn(function () { return { close: vi.fn() } }))

  mockedAxios.get.mockResolvedValue({ data: [] })
  mockedAxios.post.mockResolvedValue({ data: {} })
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
}

describe('对抗性：仅转录模式（需求 B）前端 payload — upload 域', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setup)

  it('[B5] 开关置 none 后 URL 提交 payload 携带 summary_mode="none"', async () => {
    const { upload, wrapper } = mountUploadState()

    upload.summaryMode.value = 'none'
    upload.videoUrl.value = 'https://example.com/video.mp4'
    await upload.submitTask()

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ video_url: 'https://example.com/video.mp4', summary_mode: 'none' }),
      expect.anything(),
    )

    wrapper.unmount()
  })

  it('[B5] 开关置 none 后本地路径提交 payload 携带 summary_mode="none"', async () => {
    const { upload, wrapper } = mountUploadState()

    upload.summaryMode.value = 'none'
    upload.localFilePath.value = '/data/video.mp4'
    await upload.submitTask()

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/upload/local-path',
      expect.objectContaining({ file_path: '/data/video.mp4', summary_mode: 'none' }),
      expect.anything(),
    )

    wrapper.unmount()
  })

  it('[B5] 开关置 none 后文件上传 FormData 携带 summary_mode="none"', async () => {
    const { upload, wrapper } = mountUploadState()

    upload.summaryMode.value = 'none'
    upload.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
    await upload.submitTask()

    expect(mockedAxios.post).toHaveBeenCalled()
    const [, formData] = mockedAxios.post.mock.calls.find(([url]) => url === '/upload')!
    expect(formData).toBeInstanceOf(FormData)
    expect((formData as FormData).get('summary_mode')).toBe('none')

    wrapper.unmount()
  })

  it('[B5] 开关置 none 后分P提交 payload 携带 summary_mode="none"', async () => {
    const { upload, wrapper } = mountUploadState()

    upload.summaryMode.value = 'none'
    await upload.submitTaskWithParts('https://www.bilibili.com/video/BV1xx', {
      mode: 'merge',
      indices: [0, 1],
    })

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none', bilibili_parts: { mode: 'merge', indices: [0, 1] } }),
      expect.anything(),
    )

    wrapper.unmount()
  })

  it('[P1-5] 提交 payload 使用显式常量 quality=audio_only（行为等价）；upload 域不再导出 quality 状态', async () => {
    const { upload, wrapper } = mountUploadState()

    upload.videoUrl.value = 'https://example.com/video.mp4'
    await upload.submitTask()

    const postCall = mockedAxios.post.mock.calls.find(([url]) => String(url).includes('/tasks/'))
    expect(postCall).toBeTruthy()
    const payload = postCall![1] as Record<string, unknown>
    expect(payload.quality).toBe('audio_only')

    // 死绑定清理：quality 状态已从导出面移除
    expect('quality' in upload).toBe(false)

    wrapper.unmount()
  })
})
