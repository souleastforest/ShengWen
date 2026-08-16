/**
 * P7 迁移：useTaskViewModel.heartbeat.test.ts → shared/ws.ts（WS 心跳客户端侧）
 *
 * 服务端每 ~30s 发送 {"type": "ping"}；前端收到 ping 回发 {"type": "pong"}，
 * 使服务端 receive 循环确认连接活性（半开连接可被发现并移除）。
 * 未知 type 消息必须忽略不崩溃（走默认分支，不对外分发）。
 * 失败 = 实现缺陷。
 */
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useWebSocket } from '../../ws'
import type { WsClient } from '../../ws'

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

describe('shared/ws 心跳（ping/pong，useTaskViewModel 迁移）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})

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
    const ws: WsClient = useWebSocket()
    ws.connect()

    expect(wsInstance).not.toBeNull()
    wsInstance!.onmessage?.({ data: JSON.stringify({ type: 'ping' }) })
    await flushPromises()

    expect(wsInstance!.send).toHaveBeenCalledWith('{"type":"pong"}')
  })

  it('未知 type 消息忽略不崩溃，不对外分发', async () => {
    const ws: WsClient = useWebSocket()
    ws.connect()
    const handler = vi.fn()
    ws.subscribe('task_update', handler)

    // 未知/未来协议消息不得抛异常
    expect(() => {
      wsInstance!.onmessage?.({ data: JSON.stringify({ type: 'something_new', payload: {} }) })
    }).not.toThrow()
    expect(() => {
      wsInstance!.onmessage?.({ data: JSON.stringify({ type: 'ping' }) })
    }).not.toThrow()

    // 未知 type 不触发任何订阅
    expect(handler).not.toHaveBeenCalled()
    expect(wsInstance!.send).toHaveBeenCalledTimes(1) // 仅 pong
  })

  it('ping 处理不依赖连接是否已建立（readyState 未 OPEN 时静默忽略）', async () => {
    const ws: WsClient = useWebSocket()
    ws.connect()

    wsInstance!.readyState = 0 // CONNECTING
    expect(() => {
      wsInstance!.onmessage?.({ data: JSON.stringify({ type: 'ping' }) })
    }).not.toThrow()
    expect(wsInstance!.send).not.toHaveBeenCalled()
  })
})
