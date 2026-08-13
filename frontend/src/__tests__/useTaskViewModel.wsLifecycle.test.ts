/**
 * 对抗性测试：WS 生命周期剩余加固（P2-A）
 *
 * 1. disposed 标志：组件卸载（onUnmounted）后，重连定时器/回调不得再创建新连接
 *    （当前因 App 永不卸载而潜伏；卸载后每 3s 泄漏新连接 = 实现缺陷）；
 * 2. 指数退避：重连间隔 3s→6s→12s→24s→封顶 30s；onopen 成功后复位到 3s；
 * 3. onerror 不主动 close：onerror 触发后不得再调用 ws.close()；重连定时器
 *    与 onerror 兜底（S1，~5s）共用同一槽位互斥，无双调度；
 * 4. onmessage 畸形帧（非 JSON）console.warn 并跳过该帧，不影响后续正常帧
 *    （ping 分支在 parse 之后——parse 防护包裹整个消息处理）；
 * 5. 定时器清理（S3）：重连/轮询/墓碑定时器 id 留存，onUnmounted 统一清理，
 *    卸载后无残留定时器。
 * 心跳 ping 分支、60s 轮询兜底保持不动（协议不变）。
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { __resetTaskContentCaches, useTaskViewModel } from '../composables/useTaskViewModel'

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

let wsInstances: MockWebSocketInstance[] = []
let WebSocketMock: ReturnType<typeof vi.fn>

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

describe('useTaskViewModel WS 生命周期加固（P2-A）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    __resetTaskContentCaches()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    mockedAxios.get.mockResolvedValue({ data: [] })
    mockedAxios.isCancel.mockReturnValue(false)
    mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
      return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
    })

    wsInstances = []
    WebSocketMock = vi.fn(function MockWebSocket(this: MockWebSocketInstance) {
      wsInstances.push(this)
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

  it('A1 disposed：卸载后到达的 onclose 不再排定重连（不泄漏新连接）', async () => {
    const { wrapper } = mountViewModel()
    await flushPromises()
    expect(WebSocketMock).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    // 模拟卸载后才到达的 close 事件（真实场景：unmount 内 ws.close() 触发 onclose）
    wsInstances[0]!.onclose?.()

    await vi.advanceTimersByTimeAsync(60_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(1)
  })

  it('A1 disposed：卸载前已排定的重连定时器，卸载后到期也不触发', async () => {
    const { wrapper } = mountViewModel()
    await flushPromises()

    wsInstances[0]!.onclose?.() // 排定 3s 重连
    wrapper.unmount() // 定时器未到即卸载

    await vi.advanceTimersByTimeAsync(10_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(1)
  })

  it('A2 指数退避：3s→6s→12s→24s→封顶 30s；onopen 成功后复位到 3s', async () => {
    const { wrapper } = mountViewModel()
    await flushPromises()
    expect(WebSocketMock).toHaveBeenCalledTimes(1) // t=0 首次连接

    // 第一次断线 → 3s 后重连（t=3）
    wsInstances[0]!.onclose?.()
    await vi.advanceTimersByTimeAsync(2_999)
    expect(WebSocketMock).toHaveBeenCalledTimes(1) // 3s 未到
    await vi.advanceTimersByTimeAsync(1)
    expect(WebSocketMock).toHaveBeenCalledTimes(2)

    // 第二次断线 → 6s 后重连（t=9）
    wsInstances[1]!.onclose?.()
    await vi.advanceTimersByTimeAsync(3_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(2) // 6s 未到
    await vi.advanceTimersByTimeAsync(3_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(3)

    // 第三次断线 → 12s 后重连（t=21）
    wsInstances[2]!.onclose?.()
    await vi.advanceTimersByTimeAsync(11_999)
    expect(WebSocketMock).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(1)
    expect(WebSocketMock).toHaveBeenCalledTimes(4)

    // 第四次断线 → 24s 后重连（t=45）
    wsInstances[3]!.onclose?.()
    await vi.advanceTimersByTimeAsync(23_999)
    expect(WebSocketMock).toHaveBeenCalledTimes(4)
    await vi.advanceTimersByTimeAsync(1)
    expect(WebSocketMock).toHaveBeenCalledTimes(5)

    // 第五次断线 → 封顶 30s（t=75，而非 48s：t=69 时仍未重连，t=75 恰好重连）
    wsInstances[4]!.onclose?.()
    await vi.advanceTimersByTimeAsync(24_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(5)
    await vi.advanceTimersByTimeAsync(6_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(6)

    // 后续恒为 30s（t=105）
    wsInstances[5]!.onclose?.()
    await vi.advanceTimersByTimeAsync(29_999)
    expect(WebSocketMock).toHaveBeenCalledTimes(6)
    await vi.advanceTimersByTimeAsync(1)
    expect(WebSocketMock).toHaveBeenCalledTimes(7)

    // onopen 成功复位：下次断线回到 3s（t=108）
    wsInstances[6]!.onopen?.()
    wsInstances[6]!.onclose?.()
    await vi.advanceTimersByTimeAsync(3_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(8)

    wrapper.unmount()
  })

  it('A3 onerror 不主动 close；onclose 退避与 onerror 兜底互斥（无双调度）', async () => {
    const { wrapper } = mountViewModel()
    await flushPromises()

    const first = wsInstances[0]!
    // 顺序：onerror 先行（排定 5s 兜底）→ onclose 到达（退避排定作废）
    first.onerror?.(new Error('boom'))
    expect(first.close).not.toHaveBeenCalled() // 不得主动 close

    first.onclose?.()
    await vi.advanceTimersByTimeAsync(3_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(1) // 3s 退避已被作废（不重连）
    await vi.advanceTimersByTimeAsync(2_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(2) // 5s 兜底恰好重连一次
    await vi.advanceTimersByTimeAsync(60_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(2) // 无双调度

    wrapper.unmount()
  })

  it('S1 onerror 兜底：只发 error 不发 close 时 5s 后重连一次', async () => {
    const { wrapper } = mountViewModel()
    await flushPromises()

    const first = wsInstances[0]!
    first.onerror?.(new Error('boom')) // 无 onclose

    await vi.advanceTimersByTimeAsync(4_999)
    expect(WebSocketMock).toHaveBeenCalledTimes(1) // 5s 未到
    await vi.advanceTimersByTimeAsync(1)
    expect(WebSocketMock).toHaveBeenCalledTimes(2) // 兜底重连一次

    await vi.advanceTimersByTimeAsync(60_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(2) // 不重复排定

    wrapper.unmount()
  })

  it('S1 close-first：onclose 已先行排定退避时 onerror 兜底作废', async () => {
    const { wrapper } = mountViewModel()
    await flushPromises()

    const first = wsInstances[0]!
    first.onclose?.() // 3s 退避
    first.onerror?.(new Error('boom')) // 兜底应作废

    await vi.advanceTimersByTimeAsync(3_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(2) // 仅退避重连
    await vi.advanceTimersByTimeAsync(60_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(2) // 兜底未再触发

    wrapper.unmount()
  })

  it('A4 畸形帧（非 JSON）console.warn 并跳过，后续正常帧仍处理', async () => {
    const { viewModel, wrapper } = mountViewModel()
    await flushPromises()

    const warnSpy = vi.mocked(console.warn)
    const ws = wsInstances[0]!

    // 畸形帧不得抛异常
    expect(() => ws.onmessage?.({ data: 'not json' })).not.toThrow()
    expect(warnSpy).toHaveBeenCalledTimes(1)

    // 后续正常帧（task_update）仍被处理：任务进入列表
    ws.onmessage?.({
      data: JSON.stringify({
        type: 'task_update',
        task: {
          id: 't1',
          video_url: 'https://example.com/v.mp4',
          status: 'PENDING',
          progress: 0,
          created_at: '2026-08-13T00:00:00Z',
        },
      }),
    })
    expect(viewModel.tasks.value.map((t) => t.id)).toEqual(['t1'])
    expect(warnSpy).toHaveBeenCalledTimes(1) // 正常帧不新增 warn

    wrapper.unmount()
  })

  it('S3 卸载清理全部定时器（重连/轮询无残留）', async () => {
    const { wrapper } = mountViewModel()
    await flushPromises()

    expect(vi.getTimerCount()).toBe(1) // 仅轮询 setInterval
    wsInstances[0]!.onclose?.() // 排定重连退避定时器
    expect(vi.getTimerCount()).toBe(2)

    wrapper.unmount()
    expect(vi.getTimerCount()).toBe(0) // 重连 + 轮询定时器均被清理
  })
})
