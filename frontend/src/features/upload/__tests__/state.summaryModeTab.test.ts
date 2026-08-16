/**
 * P7 迁移：useTaskViewModel.summaryModeTab.adversarial.test.ts 的 upload 域部分
 * （[S1] composable 层 payload：三态 × 三通道 + 分P + 批量提交）。
 * 用例逻辑原样保留，仅改引用（useTaskViewModel → useUploadState）与 setup。
 * 每个用例失败 = 实现缺陷。
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
    patch: vi.fn(),
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

const installDefaultAxios = () => {
  mockedAxios.get.mockResolvedValue({ data: [] })
  mockedAxios.post.mockResolvedValue({ data: {} })
  mockedAxios.patch.mockResolvedValue({ data: {} })
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
}

const setupWsAndEnv = () => {
  vi.clearAllMocks()
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.stubGlobal('WebSocket', vi.fn(function () { return { close: vi.fn() } }))
}

describe('对抗性：模式 Tab 三选一（需求 1）— upload 域 payload', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setupWsAndEnv)

  it('[S1] 不传任何设置时 upload 域默认 summaryMode="none"，URL 提交 payload 默认 none', async () => {
    const { upload, wrapper } = mountUploadState()
    installDefaultAxios()

    expect(upload.summaryMode.value).toBe('none')
    upload.videoUrl.value = 'https://example.com/video.mp4'
    await upload.submitTask()

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none' }),
      expect.anything(),
    )

    wrapper.unmount()
  })

  it.each([
    ['URL 提交', 'url'],
    ['本地路径提交', 'local'],
    ['文件上传', 'file'],
  ] as const)('[S1] 三态 × %s：none/standard/agent 各自写入正确 payload', async (_label, path) => {
    const { upload, wrapper } = mountUploadState()
    installDefaultAxios()

    for (const mode of ['none', 'standard', 'agent'] as const) {
      upload.summaryMode.value = mode
      mockedAxios.post.mockClear()
      if (path === 'url') {
        upload.videoUrl.value = 'https://example.com/video.mp4'
        upload.localFilePath.value = ''
        upload.selectedFile.value = null
        await upload.submitTask()
        expect(mockedAxios.post).toHaveBeenCalledWith(
          '/tasks/',
          expect.objectContaining({ summary_mode: mode }),
          expect.anything(),
        )
      } else if (path === 'local') {
        upload.localFilePath.value = '/data/video.mp4'
        upload.videoUrl.value = ''
        upload.selectedFile.value = null
        await upload.submitTask()
        expect(mockedAxios.post).toHaveBeenCalledWith(
          '/upload/local-path',
          expect.objectContaining({ file_path: '/data/video.mp4', summary_mode: mode }),
          expect.anything(),
        )
      } else {
        upload.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
        upload.videoUrl.value = ''
        upload.localFilePath.value = ''
        await upload.submitTask()
        const [, formData] = mockedAxios.post.mock.calls.find(([u]) => u === '/upload')!
        expect(formData).toBeInstanceOf(FormData)
        expect((formData as FormData).get('summary_mode')).toBe(mode)
      }
    }

    wrapper.unmount()
  })

  it('[S1] 分P提交（submitTaskWithParts）三态 payload 正确', async () => {
    const { upload, wrapper } = mountUploadState()
    installDefaultAxios()

    upload.summaryMode.value = 'standard'
    await upload.submitTaskWithParts('https://www.bilibili.com/video/BV1xx', {
      mode: 'merge',
      indices: [0, 1],
    })
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'standard' }),
      expect.anything(),
    )

    upload.summaryMode.value = 'agent'
    mockedAxios.post.mockClear()
    await upload.submitTaskWithParts('https://www.bilibili.com/video/BV1xx', {
      mode: 'separate',
      indices: [0],
    })
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'agent' }),
      expect.anything(),
    )

    wrapper.unmount()
  })

  it('[S1] 多文件批量提交（submitLocalPathTasks）也携带当前三态模式', async () => {
    const { upload, wrapper } = mountUploadState()
    installDefaultAxios()

    upload.summaryMode.value = 'agent'
    await upload.submitLocalPathTasks(['/a.mp3', '/b.mp3'], 'separate')

    const calls = mockedAxios.post.mock.calls.filter(([u]) => u === '/upload/local-path')
    expect(calls).toHaveLength(2)
    for (const [, payload] of calls) {
      expect(payload).toEqual(expect.objectContaining({ summary_mode: 'agent' }))
    }

    wrapper.unmount()
  })
})
