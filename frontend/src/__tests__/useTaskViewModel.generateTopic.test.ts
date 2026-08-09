/**
 * 对抗性测试：仅转录"总结标题"开关的 payload 行为（composable 层）
 *
 * 设计决策：开关仅在 summaryMode === 'none' 时随提交显式发送
 * generate_topic（true/false）；standard/agent 模式不发送该字段
 * （后端默认 true，仅转录场景开关开启时前端显式 true 与其等价）。
 * 攻击点：
 * 1. 默认值：不设置时 generateTopic=true。
 * 2. 三条提交路径（URL / local-path / 文件上传 multipart）× none 开关开/关。
 * 3. standard/agent 模式不得携带 generate_topic。
 * 4. 分P提交 / 多文件批量提交同样遵循该规则。
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { __resetTaskContentCaches, useTaskViewModel } from '../composables/useTaskViewModel'

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

const mountViewModel = () => {
  let viewModel!: ReturnType<typeof useTaskViewModel>

  const TestComponent = defineComponent({
    setup() {
      viewModel = useTaskViewModel()
      return () => null
    },
  })

  const wrapper = mount(TestComponent)
  return { viewModel, wrapper }
}

const setup = () => {
  __resetTaskContentCaches()
  vi.clearAllMocks()
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})

  const wsInstances: Array<{ close: () => void }> = []
  const WebSocketMock = vi.fn(function MockWebSocket(this: { close: () => void }) {
    this.close = () => {}
    wsInstances.push(this)
  })
  vi.stubGlobal('WebSocket', WebSocketMock)
  vi.stubGlobal('navigator', {
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  })

  mockedAxios.get.mockResolvedValue({ data: [] })
  mockedAxios.post.mockResolvedValue({ data: {} })
  mockedAxios.patch.mockResolvedValue({ data: {} })
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
}

describe('对抗性：仅转录"总结标题"开关 payload', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setup)

  it('[G1] 默认 generateTopic=true；none + 开关开启 → URL 提交 payload 显式 generate_topic=true', async () => {
    const { viewModel, wrapper } = mountViewModel()
    expect(viewModel.generateTopic.value).toBe(true)

    viewModel.videoUrl.value = 'https://example.com/video.mp4'
    await viewModel.submitTask()

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none', generate_topic: true }),
      expect.anything(),
    )
    wrapper.unmount()
  })

  it('[G1] none + 开关关闭 → 三条路径 payload 均携带 generate_topic=false', async () => {
    const { viewModel, wrapper } = mountViewModel()
    viewModel.generateTopic.value = false

    // URL
    viewModel.videoUrl.value = 'https://example.com/video.mp4'
    await viewModel.submitTask()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none', generate_topic: false }),
      expect.anything(),
    )

    // local-path
    mockedAxios.post.mockClear()
    viewModel.localFilePath.value = '/data/video.mp4'
    viewModel.videoUrl.value = ''
    await viewModel.submitTask()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/upload/local-path',
      expect.objectContaining({ summary_mode: 'none', generate_topic: false }),
      expect.anything(),
    )

    // 文件上传 multipart
    mockedAxios.post.mockClear()
    viewModel.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
    viewModel.localFilePath.value = ''
    await viewModel.submitTask()
    const [, formData] = mockedAxios.post.mock.calls.find(([u]) => u === '/upload')!
    expect(formData).toBeInstanceOf(FormData)
    expect((formData as FormData).get('summary_mode')).toBe('none')
    expect((formData as FormData).get('generate_topic')).toBe('false')

    wrapper.unmount()
  })

  it('[G1] none + 开关开启 → 三条路径 payload 均携带 generate_topic=true（含 multipart 字符串 "true"）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    viewModel.generateTopic.value = true

    // URL
    viewModel.videoUrl.value = 'https://example.com/video.mp4'
    await viewModel.submitTask()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none', generate_topic: true }),
      expect.anything(),
    )

    // local-path
    mockedAxios.post.mockClear()
    viewModel.localFilePath.value = '/data/video.mp4'
    viewModel.videoUrl.value = ''
    await viewModel.submitTask()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/upload/local-path',
      expect.objectContaining({ summary_mode: 'none', generate_topic: true }),
      expect.anything(),
    )

    // 文件上传 multipart
    mockedAxios.post.mockClear()
    viewModel.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
    viewModel.localFilePath.value = ''
    await viewModel.submitTask()
    const [, formData] = mockedAxios.post.mock.calls.find(([u]) => u === '/upload')!
    expect((formData as FormData).get('summary_mode')).toBe('none')
    expect((formData as FormData).get('generate_topic')).toBe('true')

    wrapper.unmount()
  })

  it('[G2] standard/agent 模式：三条路径 payload 均不含 generate_topic（开关不生效）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    viewModel.generateTopic.value = false // 开关状态不得泄漏到非 none 模式

    for (const mode of ['standard', 'agent'] as const) {
      viewModel.summaryMode.value = mode

      // URL
      viewModel.videoUrl.value = 'https://example.com/video.mp4'
      await viewModel.submitTask()
      const urlPayload = mockedAxios.post.mock.calls.find(([u]) => u === '/tasks/')![1] as Record<string, unknown>
      expect(urlPayload).toEqual(expect.objectContaining({ summary_mode: mode }))
      expect('generate_topic' in urlPayload).toBe(false)

      // local-path
      mockedAxios.post.mockClear()
      viewModel.localFilePath.value = '/data/video.mp4'
      viewModel.videoUrl.value = ''
      await viewModel.submitTask()
      const localPayload = mockedAxios.post.mock.calls.find(([u]) => u === '/upload/local-path')![1] as Record<string, unknown>
      expect('generate_topic' in localPayload).toBe(false)

      // 文件上传 multipart
      mockedAxios.post.mockClear()
      viewModel.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
      viewModel.localFilePath.value = ''
      await viewModel.submitTask()
      const [, formData] = mockedAxios.post.mock.calls.find(([u]) => u === '/upload')!
      expect((formData as FormData).get('generate_topic')).toBeNull()
    }

    wrapper.unmount()
  })

  it('[G3] 分P提交（submitTaskWithParts）：none 时携带 generate_topic，standard 时不携带', async () => {
    const { viewModel, wrapper } = mountViewModel()
    viewModel.summaryMode.value = 'none'
    viewModel.generateTopic.value = false
    await viewModel.submitTaskWithParts('https://www.bilibili.com/video/BV1xx', {
      mode: 'separate',
      indices: [0],
    })
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none', generate_topic: false }),
      expect.anything(),
    )

    viewModel.summaryMode.value = 'standard'
    mockedAxios.post.mockClear()
    await viewModel.submitTaskWithParts('https://www.bilibili.com/video/BV1xx', {
      mode: 'merge',
      indices: [0, 1],
    })
    const standardPayload = mockedAxios.post.mock.calls[0]![1] as Record<string, unknown>
    expect('generate_topic' in standardPayload).toBe(false)

    wrapper.unmount()
  })

  it('[G3] 多文件批量提交（submitLocalPathTasks）：none 时每条均携带 generate_topic', async () => {
    const { viewModel, wrapper } = mountViewModel()
    viewModel.summaryMode.value = 'none'
    viewModel.generateTopic.value = true
    await viewModel.submitLocalPathTasks(['/a.mp3', '/b.mp3'], 'separate')

    const calls = mockedAxios.post.mock.calls.filter(([u]) => u === '/upload/local-path')
    expect(calls).toHaveLength(2)
    for (const [, payload] of calls) {
      expect(payload).toEqual(expect.objectContaining({ summary_mode: 'none', generate_topic: true }))
    }

    wrapper.unmount()
  })

  it('[G4] 无泄漏：updateTaskTopic（PATCH）/ reSummarize 等非创建请求不受开关影响', async () => {
    const { viewModel, wrapper } = mountViewModel()
    viewModel.generateTopic.value = false

    await viewModel.updateTaskTopic('task-a', '新主题')
    const patchPayload = mockedAxios.patch.mock.calls[0]![1] as Record<string, unknown>
    expect(patchPayload).toEqual({ topic: '新主题' })

    mockedAxios.post.mockClear()
    viewModel.summaryMode.value = 'none'
    await viewModel.reSummarize('task-a')
    const [, reSummarizePayload] = mockedAxios.post.mock.calls[0]!
    expect(reSummarizePayload).toEqual({ summary_mode: 'none' })
    expect('generate_topic' in (reSummarizePayload as Record<string, unknown>)).toBe(false)

    wrapper.unmount()
  })
})
