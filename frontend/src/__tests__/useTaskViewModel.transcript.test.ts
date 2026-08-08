import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { __resetTaskContentCaches, useTaskViewModel } from '../composables/useTaskViewModel'
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
    // module 级 per-task 缓存跨测试实例共享，必须重置，否则内容会被上个测试污染
    __resetTaskContentCaches()
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

  it('切换任务时（activeTab 已是 transcript）自动加载新任务的完整转录，不残留旧任务内容', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = { ...completedTask, id: 'task-a' }
    const taskB = { ...completedTask, id: 'task-b' }

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-a?include_content=false') {
        return Promise.resolve({ data: { ...taskA, transcript: null } })
      }
      if (url === '/tasks/task-a?include_content=true') {
        return Promise.resolve({ data: { ...taskA, transcript: 'A的完整转录' } })
      }
      if (url === '/tasks/task-b?include_content=false') {
        return Promise.resolve({ data: { ...taskB, transcript: null } })
      }
      if (url === '/tasks/task-b?include_content=true') {
        return Promise.resolve({ data: { ...taskB, transcript: 'B的完整转录' } })
      }
      return Promise.resolve({ data: [] })
    })

    viewModel.selectTask(taskA)
    await flushPromises()
    viewModel.activeTab.value = 'transcript'
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('A的完整转录')

    // 核心回归：activeTab 已是 'transcript'，切换任务时 watch 必须重新触发加载
    viewModel.selectTask(taskB)
    await flushPromises()

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-b?include_content=true')
    expect(viewModel.selectedTask.value?.id).toBe('task-b')
    expect(viewModel.selectedTask.value?.transcript).toBe('B的完整转录')

    wrapper.unmount()
  })

  it('同任务重选/WS 重连时完整 summary 不被轻量响应的截断版覆盖', async () => {
    const { viewModel, wrapper } = mountViewModel()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null, summary: '截断版总结' } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...completedTask, transcript: '完整转录', summary: '完整总结' } })
      }
      return Promise.resolve({ data: [] })
    })

    viewModel.selectTask(completedTask)
    await flushPromises()
    expect(viewModel.selectedTask.value?.summary).toBe('截断版总结')

    viewModel.activeTab.value = 'transcript'
    await flushPromises()
    expect(viewModel.selectedTask.value?.summary).toBe('完整总结')
    expect(viewModel.selectedTask.value?.transcript).toBe('完整转录')

    // 模拟 WS 重连 / 重新点击同一任务：轻量详情返回后不得覆盖已加载的完整内容
    viewModel.selectTask(completedTask)
    await flushPromises()
    expect(viewModel.selectedTask.value?.summary).toBe('完整总结')
    expect(viewModel.selectedTask.value?.transcript).toBe('完整转录')

    wrapper.unmount()
  })

  it('downloadContent 在转录未加载时先请求完整内容再下载', async () => {
    const createObjectURLMock = vi.fn().mockReturnValue('blob:mock')
    const revokeObjectURLMock = vi.fn()
    // 可构造的 URL stub：happy-dom 的 anchor click 会执行 new URL(...)，纯对象会抛错
    class MockURL {}
    Object.assign(MockURL, {
      createObjectURL: createObjectURLMock,
      revokeObjectURL: revokeObjectURLMock,
    })
    vi.stubGlobal('URL', MockURL as unknown as typeof URL)

    const { viewModel, wrapper } = mountViewModel()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null, summary: '截断版总结' } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...completedTask, transcript: '可下载的完整转录' } })
      }
      return Promise.resolve({ data: [] })
    })

    viewModel.selectTask(completedTask)
    await flushPromises()

    await viewModel.downloadContent('transcript')

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-1?include_content=true')
    expect(createObjectURLMock).toHaveBeenCalled()
    expect(revokeObjectURLMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('A→B→A 往返切换时从 per-task 缓存恢复 A 的完整转录，不重复请求', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = { ...completedTask, id: 'task-a' }
    const taskB = { ...completedTask, id: 'task-b' }

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-a?include_content=false') {
        return Promise.resolve({ data: { ...taskA, transcript: null } })
      }
      if (url === '/tasks/task-a?include_content=true') {
        return Promise.resolve({ data: { ...taskA, transcript: 'A的完整转录', summary: 'A的完整总结' } })
      }
      if (url === '/tasks/task-b?include_content=false') {
        return Promise.resolve({ data: { ...taskB, transcript: null } })
      }
      if (url === '/tasks/task-b?include_content=true') {
        return Promise.resolve({ data: { ...taskB, transcript: 'B的完整转录', summary: 'B的完整总结' } })
      }
      return Promise.resolve({ data: [] })
    })

    viewModel.selectTask(taskA)
    await flushPromises()
    viewModel.activeTab.value = 'transcript'
    await flushPromises()

    viewModel.selectTask(taskB)
    await flushPromises()

    // 回到 A：应立即恢复 A 的完整内容（缓存补齐），无需再次请求完整内容
    viewModel.selectTask(taskA)
    await flushPromises()

    expect(viewModel.selectedTask.value?.id).toBe('task-a')
    expect(viewModel.selectedTask.value?.transcript).toBe('A的完整转录')
    expect(viewModel.selectedTask.value?.summary).toBe('A的完整总结')

    const fullRequests = mockedAxios.get.mock.calls.filter(([url]) => String(url).includes('include_content=true'))
    expect(fullRequests).toHaveLength(2) // 仅 A 首次与 B 首次各一次

    wrapper.unmount()
  })

  it('转录加载请求并发去重：tab watch 与 copyContent 共享同一次 GET', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', {
      clipboard: { writeText: writeTextMock },
    })

    const { viewModel, wrapper } = mountViewModel()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...completedTask, transcript: '完整转录' } })
      }
      return Promise.resolve({ data: [] })
    })

    viewModel.selectTask(completedTask)
    await flushPromises()

    // 切到“原文”tab（watch 已排队）后立刻复制：两者触发同一按需加载
    viewModel.activeTab.value = 'transcript'
    const copyPromise = viewModel.copyContent('transcript')
    await flushPromises()
    const result = await copyPromise

    const fullRequests = mockedAxios.get.mock.calls.filter(([url]) => String(url).includes('include_content=true'))
    expect(fullRequests).toHaveLength(1)
    expect(result).toBe(true)
    expect(writeTextMock).toHaveBeenCalledWith('完整转录')

    wrapper.unmount()
  })
})
