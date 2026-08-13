/**
 * 对抗性测试：useTaskViewModel WS 心跳客户端侧
 *
 * 服务端每 ~30s 发送 {"type": "ping"}；前端收到 ping 回发 {"type": "pong"}，
 * 使服务端 receive 循环确认连接活性（半开连接可被发现并移除）。
 * 未知 type 消息必须忽略不崩溃（走默认分支）。
 * 失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
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

interface MockWebSocketInstance {
  send: ReturnType<typeof vi.fn>
  readyState: number
  close: ReturnType<typeof vi.fn>
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: (() => void) | null
}

let wsInstance: MockWebSocketInstance | null = null

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

describe('useTaskViewModel WS 心跳（ping/pong）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    mockedAxios.get.mockResolvedValue({ data: [] })
    mockedAxios.isCancel.mockReturnValue(false)
    mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
      return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
    })

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

  it('收到 {"type":"ping"} 时回发 {"type":"pong"}', async () => {
    const { wrapper } = mountViewModel()

    expect(wsInstance).not.toBeNull()
    wsInstance!.onmessage?.({ data: JSON.stringify({ type: 'ping' }) })
    await flushPromises()

    expect(wsInstance!.send).toHaveBeenCalledWith('{"type":"pong"}')

    wrapper.unmount()
  })

  it('未知 type 消息忽略不崩溃，任务列表不受影响', async () => {
    const { wrapper } = mountViewModel()

    const tasks = [
      {
        id: 't1',
        video_url: 'https://example.com/v.mp4',
        status: 'PENDING',
        progress: 0,
        created_at: '2026-08-08T00:00:00Z',
      },
    ]
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/') return Promise.resolve({ data: tasks })
      return Promise.resolve({ data: [] })
    })
    wsInstance!.onopen?.()
    await flushPromises()
    expect(wrapper.vm).toBeTruthy()

    // 未知/未来协议消息不得抛异常
    expect(() => {
      wsInstance!.onmessage?.({ data: JSON.stringify({ type: 'something_new', payload: {} }) })
    }).not.toThrow()
    expect(() => {
      wsInstance!.onmessage?.({ data: JSON.stringify({ type: 'ping' }) })
    }).not.toThrow()

    // 既有 task_update / progress_update 处理不受影响
    wsInstance!.onmessage?.({
      data: JSON.stringify({
        type: 'task_update',
        task: { id: 't1', status: 'TRANSCRIBING', progress: 10 },
      }),
    })
    expect(wsInstance!.send).toHaveBeenCalledTimes(1) // 仅 pong

    wrapper.unmount()
  })

  it('ping 处理不依赖连接是否已建立（readyState 未 OPEN 时静默忽略）', async () => {
    const { wrapper } = mountViewModel()

    wsInstance!.readyState = 0 // CONNECTING
    expect(() => {
      wsInstance!.onmessage?.({ data: JSON.stringify({ type: 'ping' }) })
    }).not.toThrow()
    expect(wsInstance!.send).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})
