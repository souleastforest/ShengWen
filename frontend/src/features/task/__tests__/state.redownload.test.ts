/**
 * P7 迁移：useTaskViewModel.redownload.test.ts → features/task/state.ts
 * 用例逻辑原样保留，仅改引用（useTaskViewModel → useTaskState）与 setup。
 */
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { useTaskState } from '../state'
import { useWebSocket } from '../../../shared/ws'
import type { TaskState } from '../state'

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

const mountTaskState = () => {
  let task!: TaskState
  let ws!: ReturnType<typeof useWebSocket>
  const TestComponent = defineComponent({
    setup() {
      task = useTaskState()
      ws = useWebSocket()
      task.syncWithWs(ws)
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  task.fetchTasks()
  ws.connect()
  return { task, ws, wrapper }
}

describe('task 域 re-download（useTaskViewModel 迁移）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})

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
    const { task, wrapper } = mountTaskState()
    const fetchSpy = vi.spyOn(task, 'fetchTasks')

    await task.reDownloadAudio('task-1')

    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).toHaveBeenCalledWith('/tasks/task-1/re-download')
    // 成功不手动刷新：等待 WS 广播状态更新
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(task.error.value).toBeNull()

    wrapper.unmount()
  })

  it('后端 detail 透传到 error（复刻 reTranscribe 的错误处理）', async () => {
    const { task, wrapper } = mountTaskState()

    mockedAxios.post.mockRejectedValue({
      isAxiosError: true,
      response: { data: { detail: '任务没有转录文本，请先重新转录' } },
    })

    await task.reDownloadAudio('task-1')

    expect(task.error.value).toBe('任务没有转录文本，请先重新转录')

    wrapper.unmount()
  })

  it('非 axios 错误回退到默认文案', async () => {
    const { task, wrapper } = mountTaskState()

    mockedAxios.post.mockRejectedValue(new Error('network down'))

    await task.reDownloadAudio('task-1')

    expect(task.error.value).toBe('重新下载音频失败')

    wrapper.unmount()
  })

  it('404 任务不存在时透传后端 detail', async () => {
    const { task, wrapper } = mountTaskState()

    mockedAxios.post.mockRejectedValue({
      isAxiosError: true,
      response: { status: 404, data: { detail: '任务不存在' } },
    })

    await task.reDownloadAudio('task-missing')

    expect(task.error.value).toBe('任务不存在')

    wrapper.unmount()
  })
})
