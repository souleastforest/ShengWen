import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { useTaskViewModel } from '../composables/useTaskViewModel'
import type { Task } from '../types'

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

const completedTask: Task = {
  id: 'task-1',
  video_url: 'https://example.com/video.mp4',
  status: 'COMPLETED',
  progress: 1.0,
  created_at: '2026-04-07T00:00:00Z',
  summary_mode: 'agent',
  summary_chunk_total: 1,
  summary_chunk_done: 1,
}

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

describe('useTaskViewModel transcript lazy loading', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    // 默认所有 GET 返回空数组（onMounted 的列表与设置请求）
    mockedAxios.get.mockResolvedValue({ data: [] })
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

  it('selectTask 详情请求使用 include_content=false，transcript 被后端剥离为 null', async () => {
    const { viewModel, wrapper } = mountViewModel()

    // 轻量详情响应：transcript 为 null（后端 pop 后经 response_model 序列化为 null）
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null } })
      }
      return Promise.resolve({ data: [] })
    })

    viewModel.selectTask(completedTask)
    await flushPromises()

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-1?include_content=false')
    expect(viewModel.selectedTask.value?.transcript).toBeNull()

    wrapper.unmount()
  })

  it('切换到“原文”tab 时按需加载 include_content=true 完整内容并填充 transcript', async () => {
    const { viewModel, wrapper } = mountViewModel()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null, summary: 'summary preview' } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...completedTask, transcript: '完整转录原文', summary: '完整总结' } })
      }
      return Promise.resolve({ data: [] })
    })

    viewModel.selectTask(completedTask)
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBeNull()

    viewModel.activeTab.value = 'transcript'
    await flushPromises()

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-1?include_content=true')
    expect(viewModel.selectedTask.value?.transcript).toBe('完整转录原文')

    wrapper.unmount()
  })

  it('已加载的 transcript 不会在选择同一任务时被轻量详情响应覆盖', async () => {
    const { viewModel, wrapper } = mountViewModel()

    let includeFull = false
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=true') {
        includeFull = true
        return Promise.resolve({ data: { ...completedTask, transcript: '完整转录原文' } })
      }
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null } })
      }
      return Promise.resolve({ data: [] })
    })

    viewModel.selectTask(completedTask)
    await flushPromises()
    viewModel.activeTab.value = 'transcript'
    await flushPromises()
    expect(includeFull).toBe(true)
    expect(viewModel.selectedTask.value?.transcript).toBe('完整转录原文')

    // 重新点击同一任务：轻量详情返回后不应丢失已加载的 transcript
    viewModel.selectTask(completedTask)
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('完整转录原文')

    wrapper.unmount()
  })

  it('copyContent(\'transcript\') 在转录未加载时先拉取完整内容再复制', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', {
      clipboard: { writeText: writeTextMock },
    })

    const { viewModel, wrapper } = mountViewModel()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null, summary: 'summary preview' } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...completedTask, transcript: '可复制的完整转录' } })
      }
      return Promise.resolve({ data: [] })
    })

    viewModel.selectTask(completedTask)
    await flushPromises()

    // 停留在“总结”tab 直接复制转录（悬浮工具栏场景）
    const result = await viewModel.copyContent('transcript')

    expect(result).toBe(true)
    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-1?include_content=true')
    expect(writeTextMock).toHaveBeenCalledWith('可复制的完整转录')

    wrapper.unmount()
  })

  it('任务确实没有转录时 copyContent 返回 false（不伪造内容）', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', {
      clipboard: { writeText: writeTextMock },
    })

    const { viewModel, wrapper } = mountViewModel()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url.includes('/tasks/task-1')) {
        return Promise.resolve({ data: { ...completedTask, transcript: null, summary: 'summary preview' } })
      }
      return Promise.resolve({ data: [] })
    })

    viewModel.selectTask(completedTask)
    await flushPromises()

    const result = await viewModel.copyContent('transcript')

    expect(result).toBe(false)
    expect(writeTextMock).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})
