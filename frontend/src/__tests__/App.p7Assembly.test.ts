/**
 * P7 装配层 seam 契约测试：App.vue 三域 state + ws 接线（App.p1Defenses 风格）
 *
 * 规格（p7-composable-spec.md §6.2 / D2 / D4）：
 * 1. 挂载后 onMounted 并发装配：task fetchTasks/fetchQueueSnapshot +
 *    upload fetchUploadConfig + settings 三配置 fetch + ws.connect + 轮询启动；
 * 2. onopen → 三域重连对账联动：task fetchTasks/fetchQueueSnapshot +
 *    upload fetchUploadConfig 再次被调用（uploadConfigReconcile 语义上提）；
 * 3. D2 error→toast 聚合：任一域 error 置值 → ToastContainer 弹出对应文案。
 * 每个用例失败 = 实现缺陷。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import App from '../App.vue'
import ToastContainer from '../components/ToastContainer.vue'
import type { ToastItem } from '../composables/useToast'
import { useToast } from '../composables/useToast'

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    isAxiosError: vi.fn(),
    isCancel: vi.fn(),
  },
}))

const mockedAxios = vi.mocked(axios)

type WsInstance = {
  close: () => void
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: ((err: unknown) => void) | null
}

let wsInstances: WsInstance[] = []

const installDefaultAxios = (overrides?: Record<string, unknown>) => {
  mockedAxios.get.mockImplementation((url: string) => {
    const match = Object.entries(overrides ?? {}).find(([key]) => String(url).includes(key))
    if (match) {
      const v = match[1]
      if (v && typeof v === 'object' && 'promise' in v) return (v as { promise: Promise<{ data: unknown }> }).promise
      return Promise.resolve({ data: v })
    }
    return Promise.resolve({ data: [] })
  })
  mockedAxios.post.mockResolvedValue({ data: {} })
  mockedAxios.put.mockResolvedValue({ data: {} })
  mockedAxios.patch.mockResolvedValue({ data: {} })
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
}

const mountApp = () => mount(App, { global: { stubs: { transition: false } } })

describe('P7 装配层：三域 state + ws 接线', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')

    wsInstances.length = 0
    const WebSocketMock = vi.fn(function MockWebSocket(this: WsInstance) {
      this.close = () => {}
      this.onopen = null
      this.onmessage = null
      this.onclose = null
      this.onerror = null
      wsInstances.push(this)
    })
    vi.stubGlobal('WebSocket', WebSocketMock)

    installDefaultAxios({
      '/upload/config': { max_upload_mb: 2048 },
      '/tasks/queue': { queues: [] },
    })
  })

  it('A1 挂载后 onMounted 并发装配：三域 fetch 全部被调用 + WS 连接 + 轮询启动', async () => {
    const wrapper = mountApp()
    await Promise.resolve()

    const calledUrls = mockedAxios.get.mock.calls.map(([url]) => String(url))
    expect(calledUrls).toContain('/tasks/')
    expect(calledUrls).toContain('/tasks/queue')
    expect(calledUrls).toContain('/upload/config')
    expect(calledUrls).toContain('/llm/providers')
    expect(calledUrls).toContain('/llm/settings')
    expect(calledUrls).toContain('/transcription/settings')
    expect(calledUrls).toContain('/summarization/settings')
    expect(wsInstances).toHaveLength(1) // ws.connect() 于装配层 onMounted

    wrapper.unmount()
  })

  it('A2 onopen 重连对账：task/upload 两域订阅各自拉取（tasks/queue + upload config 第二次被调用）', async () => {
    const wrapper = mountApp()
    await Promise.resolve()

    const tasksCalls = () => mockedAxios.get.mock.calls.filter(([url]) => String(url) === '/tasks/').length
    const queueCalls = () => mockedAxios.get.mock.calls.filter(([url]) => String(url) === '/tasks/queue').length
    const configCalls = () => mockedAxios.get.mock.calls.filter(([url]) => String(url) === '/upload/config').length
    expect(tasksCalls()).toBe(1)
    expect(queueCalls()).toBe(1)
    expect(configCalls()).toBe(1)

    wsInstances[0]!.onopen?.()
    await Promise.resolve()
    await Promise.resolve()

    expect(tasksCalls()).toBe(2)
    expect(queueCalls()).toBe(2)
    expect(configCalls()).toBe(2)

    wrapper.unmount()
  })

  it('A3 D2 error→toast 聚合：任务列表拉取失败 → error toast 弹出对应文案', async () => {
    // 先安装失败 mock 再挂载：挂载期 fetchTasks 即失败 → task.error 置值
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url) === '/tasks/') return Promise.reject(new Error('network down'))
      return Promise.resolve({ data: [] })
    })
    const wrapper = mountApp()

    // fetchTasks 失败 → task.error = '获取任务列表失败' → 装配层聚合 watch → toast
    await new Promise((resolve) => setTimeout(resolve, 50))

    const container = wrapper.findComponent(ToastContainer)
    const currentToasts = (container.props('toasts') as ToastItem[]) ?? []
    expect(currentToasts.some((t) => t.message === '获取任务列表失败' && t.type === 'error')).toBe(true)
    // 模块级 toasts 同步（useToast 单例）
    const { toasts } = useToast()
    expect(toasts.value.some((t) => t.message === '获取任务列表失败')).toBe(true)

    wrapper.unmount()
  })
})
