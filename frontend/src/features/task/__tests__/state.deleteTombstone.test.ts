/**
 * P7 迁移：useTaskViewModel.deleteTombstone.test.ts → features/task/state.ts
 *
 * 需求：删除成功后记录 deletedTaskIds（任务 id），WS task_update 合并/列表
 * unshift 命中该集合时忽略——防止已删任务被在途广播"复活"；条目 60s 后过期
 * 清理（防集合无限增长）。墓碑过滤语义随 task 订阅回调（syncWithWs）迁移。
 * 失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { __resetTaskContentCaches, useTaskState } from '../state'
import { useWebSocket } from '../../../shared/ws'
import type { TaskState } from '../state'

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    isAxiosError: vi.fn(() => false),
    isCancel: vi.fn(() => false),
  },
}))

const mockedAxios = vi.mocked(axios)

const TASK_T1 = {
  id: 't1',
  video_url: 'https://example.com/v.mp4',
  status: 'COMPLETED',
  progress: 100,
  created_at: '2026-08-13T00:00:00Z',
}

const taskUpdateBroadcast = (task: Record<string, unknown>) =>
  JSON.stringify({ type: 'task_update', task })

interface MockWebSocketInstance {
  send: ReturnType<typeof vi.fn>
  readyState: number
  close: ReturnType<typeof vi.fn>
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: ((err?: unknown) => void) | null
}

let wsInstance: MockWebSocketInstance | null = null

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
  // 模拟 App.vue onMounted：轮询兜底 + WS 连接
  task.startPolling()
  ws.connect()
  return { task, ws, wrapper }
}

describe('task 域 deleteTask tombstone（P2-D，useTaskViewModel 迁移）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    __resetTaskContentCaches()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('confirm', () => true)

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/') return Promise.resolve({ data: [TASK_T1] })
      return Promise.resolve({ data: [] })
    })
    mockedAxios.delete.mockResolvedValue({ data: null })
    mockedAxios.isCancel.mockReturnValue(false)
    mockedAxios.isAxiosError.mockReturnValue(false)

    wsInstance = null
    const WebSocketMock = vi.fn(function MockWebSocket(this: MockWebSocketInstance) {
      wsInstance = this
      this.send = vi.fn()
      this.readyState = 1 // WebSocket.OPEN
      this.close = vi.fn()
      this.onopen = null
      this.onmessage = null
      this.onclose = null
      this.onerror = null
    })
    Object.assign(WebSocketMock, { OPEN: 1 })
    vi.stubGlobal('WebSocket', WebSocketMock)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('D1 删除成功后，同任务 task_update 广播不复活（tombstone 命中）', async () => {
    const { task, wrapper } = mountTaskState()
    await flushPromises()
    expect(task.tasks.value.map((t) => t.id)).toEqual(['t1'])

    const deleted = await task.deleteTask('t1')
    expect(deleted).toBe(true)
    expect(task.tasks.value).toHaveLength(0)

    // 在途广播"复活攻击"：删除后到达的 task_update（列表 unshift 分支）
    wsInstance!.onmessage?.({ data: taskUpdateBroadcast({ ...TASK_T1, status: 'TRANSCRIBING' }) })
    expect(task.tasks.value).toHaveLength(0) // 不复活

    wrapper.unmount()
    task.dispose()
  })

  it('D2 60s 后 tombstone 过期清理：广播恢复处理', async () => {
    const { task, wrapper } = mountTaskState()
    await flushPromises()
    await task.deleteTask('t1')

    // 后端列表已无 t1（轮询兜底刷新也不含）：只有广播能"复活"
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/') return Promise.resolve({ data: [] })
      return Promise.resolve({ data: [] })
    })

    // 60s 未到：广播仍被忽略
    await vi.advanceTimersByTimeAsync(59_000)
    wsInstance!.onmessage?.({ data: taskUpdateBroadcast({ ...TASK_T1, status: 'PENDING' }) })
    expect(task.tasks.value).toHaveLength(0)

    // 过期后：广播恢复处理（unshift）
    await vi.advanceTimersByTimeAsync(1_001)
    wsInstance!.onmessage?.({ data: taskUpdateBroadcast({ ...TASK_T1, status: 'PENDING' }) })
    expect(task.tasks.value.map((t) => t.id)).toEqual(['t1'])

    wrapper.unmount()
    task.dispose()
  })

  it('D3 删除失败不记录 tombstone：广播仍可合并（"删除成功后"条件）', async () => {
    mockedAxios.delete.mockRejectedValue(new Error('delete failed'))
    const { task, wrapper } = mountTaskState()
    await flushPromises()

    const deleted = await task.deleteTask('t1')
    expect(deleted).toBe(false)
    expect(task.tasks.value.map((t) => t.id)).toEqual(['t1']) // 删除失败，任务仍在

    wsInstance!.onmessage?.({
      data: taskUpdateBroadcast({ ...TASK_T1, status: 'TRANSCRIBING', progress: 10 }),
    })
    expect(task.tasks.value.map((t) => t.id)).toEqual(['t1']) // 广播正常合并（未被墓碑屏蔽）

    wrapper.unmount()
    task.dispose()
  })

  it('S3 卸载清理墓碑过期清理定时器（无残留）', async () => {
    const { task, wrapper } = mountTaskState()
    await flushPromises()
    await task.deleteTask('t1')

    expect(vi.getTimerCount()).toBe(2) // 轮询 setInterval + 墓碑清理定时器

    wrapper.unmount()
    task.dispose() // 装配层 onUnmounted 语义：清理墓碑/轮询定时器
    expect(vi.getTimerCount()).toBe(0) // 墓碑清理定时器被统一清理
  })
})
