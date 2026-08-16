/**
 * P7 共享 WS 传输层 + 事件总线（shared/ws.ts）——无业务语义。
 *
 * 职责（语义与 useTaskViewModel 内嵌 WS 层逐条等价，见 p7-composable-spec.md §5）：
 * - 连接/重连指数退避：3s→6s→12s→24s→30s 封顶；onopen 复位；
 * - onerror 不主动 close；5s 兜底重连与 onclose 退避共用槽位互斥（后到作废）；
 * - wsDisposed 语义：dispose 后重连回调双检查（调用前 + 定时器到期时）；
 * - 畸形帧（非 JSON）console.warn 跳过，不影响后续帧；ping 分支在 parse 之后、
 *   OPEN 态回 pong；未知 type 帧静默忽略（协议扩展向后兼容）；
 * - 事件总线：subscribe(type, handler) → 退订函数；dispose 后订阅自动失效。
 *
 * 不含：墓碑过滤、内容合并守卫、列表/选中任务更新——这些是 task 域语义，
 * 留在 features/task/state.ts 的订阅回调里（§5.2）。
 * 禁止 import 任何 features/*。
 */
import { ref } from 'vue'
import type { Ref } from 'vue'
import type { Task, QueueSnapshot } from '../types'

export type WsConnectionStatus = 'connecting' | 'open' | 'closed' | 'error'

export interface WsTaskUpdateMessage {
  type: 'task_update'
  task: Task
  /** 可选字段向后兼容：旧版本后端广播无该字段时忽略 */
  queues?: QueueSnapshot[]
}
export interface WsProgressUpdateMessage {
  type: 'progress_update'
  task_id: string
  progress: number
}
export interface WsPingMessage {
  type: 'ping'
}
export type WsServerMessage = WsTaskUpdateMessage | WsProgressUpdateMessage | WsPingMessage

export interface WsClient {
  /** 连接状态（装配层可 watch；task/upload 域据此做重连对账） */
  readonly status: Ref<WsConnectionStatus>
  /** 退避步进（3s→6s→12s→24s→30s 封顶；onopen 复位为 0） */
  readonly reconnectAttempts: Ref<number>
  /**
   * 订阅事件：返回退订函数；dispose 后已注册订阅自动失效。
   * 仅分发 task_update / progress_update（ping 内部处理，不对外分发）。
   */
  subscribe<T extends WsServerMessage['type']>(
    type: T,
    handler: (msg: Extract<WsServerMessage, { type: T }>) => void,
  ): () => void
  /** 发送 payload（仅 OPEN 态；ping/pong 内部处理） */
  send(payload: unknown): void
  /** 幂等：已连接（open）时不新建连接；onopen 复位退避并广播 'open' 状态 */
  connect(): void
  /** 幂等：关连接、清退避/兜底定时器、清空订阅 */
  dispose(): void
}

const normalizeBase = (base?: string) => (base || '').trim().replace(/\/+$/, '')

export function useWebSocket(options?: { baseUrl?: string }): WsClient {
  // 与 useTaskViewModel 内嵌实现同源：显式 baseUrl > VITE_WS_BASE_URL > host 推导
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const wsBaseUrl = options?.baseUrl
    || normalizeBase(import.meta.env.VITE_WS_BASE_URL)
    || `${wsProtocol}://${window.location.host}/ws`

  const status = ref<WsConnectionStatus>('closed')
  const reconnectAttempts = ref(0)

  // 重连指数退避：3s→6s→12s→24s→封顶 30s；onopen 成功后复位到 3s。
  const RECONNECT_BASE_DELAY_MS = 3_000
  const RECONNECT_MAX_DELAY_MS = 30_000
  // onerror 兜底（S1）：仅发 error 不发 close 的环境 ~5s 后重连一次
  const RECONNECT_ERROR_FALLBACK_MS = 5_000

  let ws: WebSocket | null = null
  // 组件已卸载标志：dispose 置位；重连定时器回调先检查——防止 dispose 后
  // 每 3s 泄漏新连接。
  let wsDisposed = false
  // 重连定时器 id（S1/S3）：onclose 退避与 onerror 兜底共用同一槽位互斥——
  // 后到者作废（防双调度）；dispose 统一 clearTimeout 清理。
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  // 事件总线：type → handler 集合
  const listeners = new Map<string, Set<(msg: unknown) => void>>()

  const dispatch = (type: string, msg: unknown) => {
    const set = listeners.get(type)
    if (!set) return
    for (const handler of [...set]) handler(msg)
  }

  const subscribe = <T extends WsServerMessage['type']>(
    type: T,
    handler: (msg: Extract<WsServerMessage, { type: T }>) => void,
  ): (() => void) => {
    let set = listeners.get(type)
    if (!set) {
      set = new Set()
      listeners.set(type, set)
    }
    set.add(handler as (msg: unknown) => void)
    let active = true
    return () => {
      if (!active) return
      active = false
      set.delete(handler as (msg: unknown) => void)
    }
  }

  const send = (payload: unknown) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload))
    }
  }

  const scheduleReconnect = () => {
    if (wsDisposed) return
    if (reconnectTimer != null) return // 已排定（含 onerror 兜底）：不重复排定
    const delay = Math.min(
      RECONNECT_BASE_DELAY_MS * 2 ** reconnectAttempts.value,
      RECONNECT_MAX_DELAY_MS,
    )
    reconnectAttempts.value += 1
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      // dispose 前已排定的定时器：到期时可能已 dispose，须再检查一次
      if (wsDisposed) return
      connect()
    }, delay)
  }

  const scheduleReconnectErrorFallback = () => {
    if (wsDisposed) return
    if (reconnectTimer != null) return // onclose 已先行排定重连 → 兜底作废
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      if (wsDisposed) return
      connect()
    }, RECONNECT_ERROR_FALLBACK_MS)
  }

  const connect = () => {
    if (wsDisposed) return
    // 幂等：已连接（status=open）时不新建连接
    if (status.value === 'open' && ws) return
    status.value = 'connecting'
    ws = new WebSocket(wsBaseUrl)

    ws.onopen = () => {
      // WS 连接生命周期日志仅 DEV 输出（生产构建 import.meta.env.DEV 为 false，被摇树）
      if (import.meta.env.DEV) {
        console.debug('WebSocket connected')
      }
      // 重连成功：退避复位到 3s，并广播 'open' 状态（各域订阅层自行对账）
      reconnectAttempts.value = 0
      status.value = 'open'
    }

    ws.onmessage = (event) => {
      // 畸形帧（非 JSON）防护（P2-A）：console.warn + 跳过该帧，
      // 不影响后续消息；ping 分支在 parse 之后——防护包裹整个消息处理。
      // 协议边界：WS 帧为宽松结构，沿用原 any 语义（无隐式类型契约）。
      let data: any
      try {
        data = JSON.parse(event.data)
      } catch (parseErr) {
        console.warn('忽略非 JSON 的 WS 消息:', event.data, parseErr)
        return
      }
      if (data.type === 'ping') {
        // 双向心跳：服务端每 ~30s 发 ping，回 pong 保持连接活性；
        // 非 OPEN 状态（连接建立中/关闭中）静默忽略。
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'pong' }))
        }
        return
      }
      // 未知 type 消息（旧客户端不认识的新协议扩展）走默认分支忽略，不崩溃。
      dispatch(data.type, data)
    }

    ws.onclose = () => {
      // WS 连接生命周期日志仅 DEV 输出（生产构建 import.meta.env.DEV 为 false，被摇树）
      if (import.meta.env.DEV) {
        console.debug('WebSocket disconnected, retrying...')
      }
      status.value = 'closed'
      // 重连统一由 onclose 排定（指数退避）；dispose 后不再重连
      scheduleReconnect()
    }

    ws.onerror = (err) => {
      // 不主动 close（P2-A）：浏览器在 error 后必发 close 事件，由 onclose
      // 统一排定重连；onerror 里 close 会再触发 onclose → 双调度。
      console.error('WebSocket error:', err)
      status.value = 'error'
      // 兜底重连（S1）：部分环境只发 error 不发 close（重连永久停滞风险）——
      // 排定 ~5s 兜底定时器；onclose 已先行排定重连时作废（槽位互斥）。
      scheduleReconnectErrorFallback()
    }
  }

  const dispose = () => {
    if (wsDisposed) return // 幂等
    // 先置位卸载标志：ws.close() 触发的 onclose 不得再排定重连（P2-A）
    wsDisposed = true
    // 主动清理排定的重连定时器（S3）：即使回调已有 wsDisposed 双检查
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    // 清空订阅：dispose 后已注册订阅自动失效
    listeners.clear()
    if (ws) {
      ws.close()
    }
    ws = null
    status.value = 'closed'
  }

  return {
    status,
    reconnectAttempts,
    subscribe,
    send,
    connect,
    dispose,
  }
}
