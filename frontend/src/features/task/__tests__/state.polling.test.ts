/**
 * P7 迁移：useTaskViewModel.polling.test.ts → features/task/state.ts
 *
 * 生产事故（任务 93b857d0）：一条广播丢失 = 永久冻结，只能手动刷新。
 * 需求：低频 setInterval（~60s）并行调用 fetchTasks + fetchQueueSnapshot，
 * WS 正常时无害、WS 断流/丢广播时兜底刷新；装配层 onUnmounted（task.dispose()）
 * 清理定时器。startPolling 由装配层 onMounted 调用（D4），此处显式模拟。
 * 失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
  task.fetchQueueSnapshot()
  ws.connect()
  return { task, ws, wrapper }
}

describe('task 域轮询兜底（useTaskViewModel 迁移）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mockedAxios.get.mockResolvedValue({ data: [] })
    mockedAxios.isCancel.mockReturnValue(false)
    mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
      return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
    })
    // ws.connect 需要 close 方法（function 构造器）
    vi.stubGlobal('WebSocket', vi.fn(function () { return { close: vi.fn() } }))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('装配层启动轮询：每 ~60s 调用 fetchTasks 与 fetchQueueSnapshot', async () => {
    const { task, wrapper } = mountTaskState()
    task.startPolling() // 模拟 App.vue onMounted 的 startPolling
    await flushPromises()

    // 启动时各 1 次初始拉取
    const tasksCalls = () =>
      mockedAxios.get.mock.calls.filter(([url]) => url === '/tasks/').length
    const queueCalls = () =>
      mockedAxios.get.mock.calls.filter(([url]) => url === '/tasks/queue').length
    expect(tasksCalls()).toBe(1)
    expect(queueCalls()).toBe(1)

    // 60s 后轮询触发
    await vi.advanceTimersByTimeAsync(60_000)
    expect(tasksCalls()).toBe(2)
    expect(queueCalls()).toBe(2)

    // 再 60s 继续
    await vi.advanceTimersByTimeAsync(60_000)
    expect(tasksCalls()).toBe(3)
    expect(queueCalls()).toBe(3)

    wrapper.unmount()
    task.stopPolling()
  })

  it('装配层卸载（task.dispose）清理轮询定时器（卸载后不再请求）', async () => {
    const { task, wrapper } = mountTaskState()
    task.startPolling()
    await flushPromises()

    const tasksCalls = () =>
      mockedAxios.get.mock.calls.filter(([url]) => url === '/tasks/').length
    wrapper.unmount()
    task.dispose() // App.vue onBeforeUnmount 语义

    await vi.advanceTimersByTimeAsync(60_000 * 5)
    expect(tasksCalls()).toBe(1)
  })
})
