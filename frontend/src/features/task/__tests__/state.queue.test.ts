/**
 * P7 迁移：useTaskViewModel.queue.test.ts → features/task/state.ts（队列快照）
 * 用例逻辑原样保留；onopen 对账现由订阅层 watch(status==='open') 驱动。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { useTaskState } from '../state'
import { useWebSocket } from '../../../shared/ws'
import type { TaskState } from '../state'
import type { QueueSnapshot } from '../../../types'

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
  // 模拟 App.vue onMounted：初始列表/队列拉取 + WS 连接
  task.fetchTasks()
  task.fetchQueueSnapshot()
  ws.connect()
  return { task, ws, wrapper }
}

const makeQueue = (overrides?: Partial<QueueSnapshot>): QueueSnapshot => ({
  name: 'VideoDownloaderWorker',
  active_task_id: null,
  queue_size: 0,
  waiting_task_ids: [],
  ...overrides,
})

const taskUpdateMessage = (taskId: string, queues?: QueueSnapshot[]) => {
  const payload: Record<string, unknown> = {
    type: 'task_update',
    task: {
      id: taskId,
      video_url: 'https://example.com/v.mp4',
      status: 'PENDING',
      progress: 0,
      created_at: '2026-08-08T00:00:00Z',
    },
  }
  if (queues) {
    payload.queues = queues
  }
  return JSON.stringify(payload)
}

describe('task 域队列快照（useTaskViewModel 迁移）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    // 初始拉取默认返回空数组
    mockedAxios.get.mockResolvedValue({ data: [] })
    mockedAxios.isCancel.mockReturnValue(false)
    mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
      return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
    })

    wsInstance = null
    const WebSocketMock = vi.fn(function MockWebSocket(this: MockWebSocketInstance) {
      wsInstance = this
      this.close = vi.fn()
      this.onopen = null
      this.onmessage = null
      this.onclose = null
      this.onerror = null
    })
    vi.stubGlobal('WebSocket', WebSocketMock)
  })

  it('fetchQueueSnapshot 拉取 /tasks/queue 并填充 queues', async () => {
    const { task, wrapper } = mountTaskState()

    const snapshot: QueueSnapshot[] = [
      makeQueue({ name: 'VideoDownloaderWorker', waiting_task_ids: ['task-1'] }),
      makeQueue({ name: 'FileUploadWorker', waiting_task_ids: ['task-2'] }),
    ]
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/queue') {
        return Promise.resolve({
          data: { queues: snapshot, timestamp: '2026-08-08T00:00:00Z' },
        })
      }
      return Promise.resolve({ data: [] })
    })

    await task.fetchQueueSnapshot()
    await flushPromises()

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/queue')
    expect(task.queues.value).toEqual(snapshot)

    wrapper.unmount()
  })

  it('ws onopen 时拉取 /tasks/queue 快照（与 fetchTasks 并列，订阅层对账）', async () => {
    const { task, wrapper } = mountTaskState()

    const snapshot: QueueSnapshot[] = [
      makeQueue({ name: 'TranscriberWorker', waiting_task_ids: ['task-1'] }),
    ]
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/queue') {
        return Promise.resolve({ data: { queues: snapshot, timestamp: '2026-08-08T00:00:00Z' } })
      }
      return Promise.resolve({ data: [] })
    })

    expect(wsInstance).not.toBeNull()
    wsInstance!.onopen?.()
    await flushPromises()

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/queue')
    expect(task.queues.value).toEqual(snapshot)

    wrapper.unmount()
  })

  it('task_update 携带 queues 字段时更新快照', async () => {
    const { task, wrapper } = mountTaskState()

    const initial: QueueSnapshot[] = [makeQueue()]
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/queue') {
        return Promise.resolve({ data: { queues: initial, timestamp: '2026-08-08T00:00:00Z' } })
      }
      return Promise.resolve({ data: [] })
    })
    wsInstance!.onopen?.()
    await flushPromises()
    expect(task.queues.value).toEqual(initial)

    const updated: QueueSnapshot[] = [
      makeQueue({
        name: 'FileUploadWorker',
        active_task_id: 'task-9',
        queue_size: 2,
        waiting_task_ids: ['task-1', 'task-2'],
      }),
    ]
    wsInstance!.onmessage?.({ data: taskUpdateMessage('task-1', updated) })
    await flushPromises()

    expect(task.queues.value).toEqual(updated)

    wrapper.unmount()
  })

  it('旧版 task_update 无 queues 字段时保留现有快照（向后兼容）', async () => {
    const { task, wrapper } = mountTaskState()

    const initial: QueueSnapshot[] = [
      makeQueue({ name: 'LLMWorker', waiting_task_ids: ['task-1'] }),
    ]
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/queue') {
        return Promise.resolve({ data: { queues: initial, timestamp: '2026-08-08T00:00:00Z' } })
      }
      return Promise.resolve({ data: [] })
    })
    wsInstance!.onopen?.()
    await flushPromises()
    expect(task.queues.value).toEqual(initial)

    // 不带 queues 字段的旧消息
    wsInstance!.onmessage?.({ data: taskUpdateMessage('task-2') })
    await flushPromises()

    expect(task.queues.value).toEqual(initial)

    wrapper.unmount()
  })

  it('畸形帧跳过不影响后续正常帧：task_update 广播仍进入任务列表（wsLifecycle A4 迁移）', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { task, wrapper } = mountTaskState()
    await flushPromises()

    // 畸形帧不得抛异常、不得影响后续处理
    expect(() => {
      wsInstance!.onmessage?.({ data: 'not json' })
    }).not.toThrow()

    // 后续正常帧（task_update）：任务进入列表（unshift）
    wsInstance!.onmessage?.({ data: taskUpdateMessage('t1') })
    expect(task.tasks.value.map((t) => t.id)).toEqual(['t1'])

    wrapper.unmount()
  })

  it('/tasks/queue 请求失败时保留上一次快照，不影响主流程', async () => {
    const { task, wrapper } = mountTaskState()

    const initial: QueueSnapshot[] = [makeQueue({ waiting_task_ids: ['task-1'] })]
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/queue') {
        return Promise.resolve({ data: { queues: initial, timestamp: '2026-08-08T00:00:00Z' } })
      }
      return Promise.resolve({ data: [] })
    })
    wsInstance!.onopen?.()
    await flushPromises()

    mockedAxios.get.mockRejectedValueOnce(new Error('network down'))
    await task.fetchQueueSnapshot()
    await flushPromises()

    expect(task.queues.value).toEqual(initial)

    wrapper.unmount()
  })
})
