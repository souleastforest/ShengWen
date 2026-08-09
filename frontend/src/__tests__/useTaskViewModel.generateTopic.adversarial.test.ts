/**
 * 对抗性测试：仅转录"总结标题"开关的状态持久性与泄漏
 *
 * 需求：
 * 1. 开关状态在模式切换间保留（none 关闭 → standard → 切回 none 仍为关闭）；
 * 2. 开关状态不泄漏到非创建请求（reTranscribe / reDownloadAudio payload
 *    不得携带 generate_topic——它们应只携带 summary_mode 或无 body）；
 * 3. reTranscribe 携带的是 UI 当前 summary_mode（覆盖任务已存模式，与
 *    后端"fallback 到已存模式"仅当 body 缺省时生效的语义对齐）。
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
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

describe('对抗性：generate_topic 状态持久性与非创建请求泄漏', () => {
  beforeEach(setup)

  it('开关关闭后切到 standard 再切回 none：关闭状态保留，payload 仍为 false', async () => {
    const { viewModel, wrapper } = mountViewModel()
    viewModel.summaryMode.value = 'none'
    viewModel.generateTopic.value = false
    viewModel.videoUrl.value = 'https://example.com/persist.mp4'

    // standard 模式提交：不带 generate_topic
    viewModel.summaryMode.value = 'standard'
    await viewModel.submitTask()
    const standardPayload = mockedAxios.post.mock.calls[0]![1] as Record<string, unknown>
    expect(standardPayload.summary_mode).toBe('standard')
    expect('generate_topic' in standardPayload).toBe(false)

    // 切回 none：开关仍为关闭，提交携带 false（submitTask 成功后清空 videoUrl，需重设）
    mockedAxios.post.mockClear()
    viewModel.summaryMode.value = 'none'
    viewModel.videoUrl.value = 'https://example.com/persist.mp4'
    await viewModel.submitTask()
    const nonePayload = mockedAxios.post.mock.calls[0]![1] as Record<string, unknown>
    expect(nonePayload.generate_topic).toBe(false)

    wrapper.unmount()
  })

  it('reTranscribe payload 只含 summary_mode，不携带 generate_topic（开关开着也不泄漏）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    viewModel.generateTopic.value = true
    viewModel.summaryMode.value = 'none'
    await viewModel.reTranscribe('task-a')

    const [, reTranscribePayload] = mockedAxios.post.mock.calls[0]!
    const payload = reTranscribePayload as Record<string, unknown>
    expect(payload).toEqual({ summary_mode: 'none' })
    expect('generate_topic' in payload).toBe(false)

    wrapper.unmount()
  })

  it('reTranscribe 携带 UI 当前 summary_mode（standard 时不发 none，不按任务已存模式）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    // 用户当前在 standard 模式对某 FAILED 任务重跑
    viewModel.summaryMode.value = 'standard'
    viewModel.generateTopic.value = false
    await viewModel.reTranscribe('task-a')

    const [, reTranscribePayload] = mockedAxios.post.mock.calls[0]!
    expect((reTranscribePayload as Record<string, unknown>).summary_mode).toBe('standard')
    expect('generate_topic' in (reTranscribePayload as Record<string, unknown>)).toBe(false)

    wrapper.unmount()
  })

  it('reDownloadAudio 无 body（不含 generate_topic）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    viewModel.generateTopic.value = false
    await viewModel.reDownloadAudio('task-a')

    const [url, body] = mockedAxios.post.mock.calls[0]!
    expect(String(url)).toContain('/re-download')
    expect(body ?? null).toBeNull()

    wrapper.unmount()
  })
})
