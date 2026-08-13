/**
 * 对抗性测试：useTaskViewModel 轮询兜底（WS 断流时前端仍能刷新）
 *
 * 生产事故（任务 93b857d0）：一条广播丢失 = 永久冻结，只能手动刷新。
 * 需求：低频 setInterval（~60s）并行调用 fetchTasks + fetchQueueSnapshot，
 * WS 正常时无害、WS 断流/丢广播时兜底刷新；onUnmounted 清理定时器。
 * 失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

describe('useTaskViewModel 轮询兜底', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mockedAxios.get.mockResolvedValue({ data: [] })
    mockedAxios.isCancel.mockReturnValue(false)
    mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
      return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
    })
    // onUnmounted 会调用 ws.close()：mock 需提供 close 方法（function 构造器）
    vi.stubGlobal('WebSocket', vi.fn(function () { return { close: vi.fn() } }))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('挂载后启动轮询：每 ~60s 调用 fetchTasks 与 fetchQueueSnapshot', async () => {
    const { wrapper } = mountViewModel()
    await flushPromises()

    // 挂载时各 1 次初始拉取
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
  })

  it('onUnmounted 清理轮询定时器（卸载后不再请求）', async () => {
    const { wrapper } = mountViewModel()
    await flushPromises()

    const tasksCalls = () =>
      mockedAxios.get.mock.calls.filter(([url]) => url === '/tasks/').length
    wrapper.unmount()

    await vi.advanceTimersByTimeAsync(60_000 * 5)
    expect(tasksCalls()).toBe(1)
  })
})
