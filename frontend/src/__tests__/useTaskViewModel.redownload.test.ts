import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { useTaskViewModel } from '../composables/useTaskViewModel'

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

describe('useTaskViewModel reDownloadAudio', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    // 默认所有 GET 返回空数组（onMounted 的列表与设置请求）
    mockedAxios.get.mockResolvedValue({ data: [] })
    mockedAxios.post.mockResolvedValue({ data: {} })
    mockedAxios.isCancel.mockReturnValue(false)
    mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
      return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
    })

    const WebSocketMock = vi.fn(function MockWebSocket(this: Record<string, unknown>) {
      this.close = vi.fn()
      this.onopen = null
      this.onmessage = null
      this.onclose = null
      this.onerror = null
    })
    vi.stubGlobal('WebSocket', WebSocketMock)
  })

  it('POST /tasks/{taskId}/re-download（无请求体），成功后不手动刷新任务列表', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const fetchSpy = vi.spyOn(viewModel, 'fetchTasks')

    await viewModel.reDownloadAudio('task-1')

    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).toHaveBeenCalledWith('/tasks/task-1/re-download')
    // 成功不手动刷新：等待 WS 广播状态更新
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(viewModel.error.value).toBeNull()

    wrapper.unmount()
  })

  it('后端 detail 透传到 error（复刻 reTranscribe 的错误处理）', async () => {
    const { viewModel, wrapper } = mountViewModel()

    mockedAxios.post.mockRejectedValue({
      isAxiosError: true,
      response: { data: { detail: '任务没有转录文本，请先重新转录' } },
    })

    await viewModel.reDownloadAudio('task-1')

    expect(viewModel.error.value).toBe('任务没有转录文本，请先重新转录')

    wrapper.unmount()
  })

  it('非 axios 错误回退到默认文案', async () => {
    const { viewModel, wrapper } = mountViewModel()

    mockedAxios.post.mockRejectedValue(new Error('network down'))

    await viewModel.reDownloadAudio('task-1')

    expect(viewModel.error.value).toBe('重新下载音频失败')

    wrapper.unmount()
  })

  it('404 任务不存在时透传后端 detail', async () => {
    const { viewModel, wrapper } = mountViewModel()

    mockedAxios.post.mockRejectedValue({
      isAxiosError: true,
      response: { status: 404, data: { detail: '任务不存在' } },
    })

    await viewModel.reDownloadAudio('task-missing')

    expect(viewModel.error.value).toBe('任务不存在')

    wrapper.unmount()
  })
})
