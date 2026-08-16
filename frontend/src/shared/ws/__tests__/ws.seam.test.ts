/**
 * P7 seam 契约测试：shared/ws.ts（WS 传输层 + 事件总线，无业务语义）
 *
 * 规格（p7-composable-spec.md §5）逐键断言：
 * - 导出面：useWebSocket / WsClient（status / reconnectAttempts refs、
 *   subscribe / send / connect / dispose）；
 * - 订阅/退订：subscribe 返回退订函数；dispose 后订阅自动失效；
 * - 重连指数退避 3s→6s→12s→24s→30s 封顶；onopen 复位；
 * - onerror 不主动 close；5s 兜底与 onclose 退避共用槽位互斥（后到作废）；
 * - dispose 后不重连（wsDisposed 语义，含"已排定定时器到期不触发"）；
 * - 畸形帧（非 JSON）console.warn 跳过，不影响后续帧；
 * - ping 在 parse 之后、OPEN 态回 pong；非 OPEN 静默忽略；
 * - 未知 type 帧静默忽略（协议扩展向后兼容）；
 * - connect 幂等：已连接（open）时不新建连接。
 * 每个用例失败 = 实现缺陷。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { useWebSocket } from '../../ws'

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

const installWebSocketMock = () => {
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
}

const emitRaw = (raw: string) => {
  wsInstances[wsInstances.length - 1]!.onmessage?.({ data: raw })
}

describe('P7 seam：shared/ws.ts 导出面与契约', () => {
  beforeEach(() => {
    installWebSocketMock()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('导出面形状：connect/subscribe/send/dispose 为函数，status/reconnectAttempts 为 Ref', () => {
    const ws = useWebSocket()
    expect(typeof ws.connect).toBe('function')
    expect(typeof ws.subscribe).toBe('function')
    expect(typeof ws.send).toBe('function')
    expect(typeof ws.dispose).toBe('function')
    // Ref 语义：.value 可读，status 初始 closed、退避步进 0
    expect(ws.status.value).toBe('closed')
    expect(ws.reconnectAttempts.value).toBe(0)
  })

  it('订阅/退订：subscribe 返回退订函数，退订后不再收到消息；dispose 后订阅自动失效', () => {
    const ws = useWebSocket()
    ws.connect()
    const handler = vi.fn()
    const unsubscribe = ws.subscribe('task_update', handler)

    emitRaw(JSON.stringify({ type: 'task_update', task: { id: 't1' } }))
    expect(handler).toHaveBeenCalledTimes(1)
    expect(handler.mock.calls[0]![0]).toMatchObject({ type: 'task_update' })

    unsubscribe()
    emitRaw(JSON.stringify({ type: 'task_update', task: { id: 't2' } }))
    expect(handler).toHaveBeenCalledTimes(1)

    // dispose 后已注册订阅自动失效
    const handler2 = vi.fn()
    ws.subscribe('task_update', handler2)
    ws.dispose()
    emitRaw(JSON.stringify({ type: 'task_update', task: { id: 't3' } }))
    expect(handler2).not.toHaveBeenCalled()
  })

  it('未知 type 帧静默忽略：不抛异常、不调用任何订阅', () => {
    const ws = useWebSocket()
    ws.connect()
    const handler = vi.fn()
    ws.subscribe('task_update', handler)

    expect(() => emitRaw(JSON.stringify({ type: 'something_new', payload: {} }))).not.toThrow()
    expect(handler).not.toHaveBeenCalled()
  })

  it('ping 心跳：OPEN 态回发 pong；非 OPEN 态静默忽略', () => {
    const ws = useWebSocket()
    ws.connect()

    emitRaw(JSON.stringify({ type: 'ping' }))
    expect(wsInstances[0]!.send).toHaveBeenCalledWith('{"type":"pong"}')

    // 非 OPEN（CONNECTING）：不回复
    wsInstances[0]!.readyState = 0
    emitRaw(JSON.stringify({ type: 'ping' }))
    expect(wsInstances[0]!.send).toHaveBeenCalledTimes(1)
  })

  it('畸形帧（非 JSON）console.warn 跳过，后续正常帧仍分发', () => {
    const ws = useWebSocket()
    ws.connect()
    const handler = vi.fn()
    ws.subscribe('task_update', handler)
    const warnSpy = vi.mocked(console.warn)

    expect(() => emitRaw('not json')).not.toThrow()
    expect(warnSpy).toHaveBeenCalledTimes(1)

    emitRaw(JSON.stringify({ type: 'task_update', task: { id: 't1' } }))
    expect(handler).toHaveBeenCalledTimes(1)
    expect(warnSpy).toHaveBeenCalledTimes(1) // 正常帧不新增 warn
  })

  it('重连指数退避 3s→6s→12s→24s→30s 封顶；onopen 复位到 3s', async () => {
    vi.useFakeTimers()
    const ws = useWebSocket()
    ws.connect()
    expect(WebSocketMock).toHaveBeenCalledTimes(1)

    // 1st close → 3s
    wsInstances[0]!.onclose?.()
    await vi.advanceTimersByTimeAsync(2_999)
    expect(WebSocketMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(WebSocketMock).toHaveBeenCalledTimes(2)

    // 2nd close → 6s
    wsInstances[1]!.onclose?.()
    await vi.advanceTimersByTimeAsync(6_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(3)

    // 3rd close → 12s
    wsInstances[2]!.onclose?.()
    await vi.advanceTimersByTimeAsync(12_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(4)

    // 4th close → 24s
    wsInstances[3]!.onclose?.()
    await vi.advanceTimersByTimeAsync(24_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(5)

    // 5th close → 封顶 30s
    wsInstances[4]!.onclose?.()
    await vi.advanceTimersByTimeAsync(29_999)
    expect(WebSocketMock).toHaveBeenCalledTimes(5)
    await vi.advanceTimersByTimeAsync(1)
    expect(WebSocketMock).toHaveBeenCalledTimes(6)

    // onopen 复位：下次断线回到 3s
    wsInstances[5]!.onopen?.()
    wsInstances[5]!.onclose?.()
    await vi.advanceTimersByTimeAsync(3_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(7)

    ws.dispose()
    vi.useRealTimers()
  })

  it('onerror 不主动 close；onclose 退避与 onerror 兜底共用槽位互斥（无双调度）', async () => {
    vi.useFakeTimers()
    const ws = useWebSocket()
    ws.connect()

    // onerror 先行（排定 5s 兜底）→ onclose 到达（退避作废兜底）
    wsInstances[0]!.onerror?.(new Error('boom'))
    expect(wsInstances[0]!.close).not.toHaveBeenCalled()

    wsInstances[0]!.onclose?.()
    await vi.advanceTimersByTimeAsync(5_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(2) // 仅 5s 兜底重连一次
    await vi.advanceTimersByTimeAsync(60_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(2) // 无双调度

    ws.dispose()
    vi.useRealTimers()
  })

  it('S1 只发 error 不发 close 时 5s 兜底重连一次；dispose 后不再排定', async () => {
    vi.useFakeTimers()
    const ws = useWebSocket()
    ws.connect()

    wsInstances[0]!.onerror?.(new Error('boom'))
    await vi.advanceTimersByTimeAsync(4_999)
    expect(WebSocketMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(WebSocketMock).toHaveBeenCalledTimes(2)

    // dispose 后 onclose 不再排定重连
    ws.dispose()
    wsInstances[1]!.onclose?.()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(2)

    vi.useRealTimers()
  })

  it('dispose 前已排定的重连定时器，dispose 后到期也不触发', async () => {
    vi.useFakeTimers()
    const ws = useWebSocket()
    ws.connect()

    wsInstances[0]!.onclose?.() // 排定 3s 重连
    ws.dispose() // 定时器未到即 dispose

    await vi.advanceTimersByTimeAsync(10_000)
    expect(WebSocketMock).toHaveBeenCalledTimes(1)

    vi.useRealTimers()
  })

  it('connect 幂等：已连接（open）时不新建连接；onopen 广播 open 状态', () => {
    const ws = useWebSocket()
    ws.connect()
    expect(ws.status.value).toBe('connecting')

    wsInstances[0]!.onopen?.()
    expect(ws.status.value).toBe('open')

    ws.connect()
    expect(WebSocketMock).toHaveBeenCalledTimes(1)
  })

  it('send 仅 OPEN 态发送 payload（JSON 序列化）', () => {
    const ws = useWebSocket()
    ws.connect()
    wsInstances[0]!.readyState = 0 // CONNECTING
    ws.send({ type: 'pong' })
    expect(wsInstances[0]!.send).not.toHaveBeenCalled()

    wsInstances[0]!.readyState = 1
    ws.send({ type: 'pong' })
    expect(wsInstances[0]!.send).toHaveBeenCalledWith('{"type":"pong"}')
  })
})
