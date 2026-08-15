/**
 * P5 双 ToastContainer 单例（App.vue 装配层）
 *
 * 背景：App.vue 原渲染两个 ToastContainer（CSS `hidden md:block` / `block md:hidden`
 * 响应式切换），同一批 toast 挂载两份实例——隐藏实例虽无 hover 机会但纯属冗余渲染，
 * 且双实例与"单一渲染入口"冲突（P5 合并为单实例 + position 响应式切换）。
 *
 * 验收（行为保持）：
 * 1. App 挂载后 ToastContainer 仅 1 个实例（DOM 查询容器数量）——双实例时红；
 * 2. 桌面视口（≥768px）→ position=bottom-right（容器 class 含 bottom-4 right-4）；
 * 3. 切到移动视口（matchMedia change 触发）→ position=bottom-center
 *    （容器 class 含 bottom-4 left-1/2 -translate-x-1/2）。
 */
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import App from '../App.vue'
import ToastContainer from '../components/ToastContainer.vue'

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

// ---- matchMedia 可控桩：驱动响应式视口切换 ----
type MediaListener = (e: { matches: boolean }) => void
let isMobile = false
const mediaListeners: MediaListener[] = []

const installMatchMediaMock = () => {
  isMobile = false
  mediaListeners.length = 0
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: isMobile,
      media: query,
      addEventListener: (_type: string, cb: MediaListener) => mediaListeners.push(cb),
      removeEventListener: () => {},
      addListener: (cb: MediaListener) => mediaListeners.push(cb),
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    })),
  )
}

const setViewport = (mobile: boolean) => {
  isMobile = mobile
  // 广播给所有已注册的 matchMedia 监听器（App toast 定位 + 其他组件）
  mediaListeners.forEach((cb) => cb({ matches: mobile }))
}

type WsInstance = {
  close: () => void
  onopen: (() => void) | null
  onmessage: ((e: unknown) => void) | null
  onclose: (() => void) | null
  onerror: ((e: unknown) => void) | null
}

const installWebSocketMock = () => {
  const wsInstances: WsInstance[] = []
  const WebSocketMock = vi.fn(function MockWebSocket(this: WsInstance) {
    this.close = () => {}
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
    wsInstances.push(this)
  })
  vi.stubGlobal('WebSocket', WebSocketMock)
  return wsInstances
}

const mountApp = () => mount(App, { global: { stubs: { transition: false } } })

describe('P5 ToastContainer 单例（App.vue）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
    installMatchMediaMock()
    installWebSocketMock()
    mockedAxios.get.mockResolvedValue({ data: [] })
    mockedAxios.post.mockResolvedValue({ data: {} })
    mockedAxios.put.mockResolvedValue({ data: {} })
    mockedAxios.patch.mockResolvedValue({ data: {} })
    mockedAxios.isCancel.mockReturnValue(false)
    mockedAxios.isAxiosError.mockReturnValue(false)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('App 渲染后 ToastContainer 仅挂载 1 个实例', () => {
    const wrapper = mountApp()
    const containers = wrapper.findAllComponents(ToastContainer)
    expect(containers).toHaveLength(1)
    wrapper.unmount()
  })

  it('桌面视口下 position=bottom-right（容器 class 含 bottom-4 right-4）', () => {
    const wrapper = mountApp()
    const container = wrapper.findComponent(ToastContainer)
    expect(container.props('position')).toBe('bottom-right')
    const className = String(container.element.className)
    expect(className).toContain('bottom-4')
    expect(className).toContain('right-4')
    wrapper.unmount()
  })

  it('切到移动视口后 position=bottom-center（容器 class 含 bottom-4 left-1/2）', async () => {
    const wrapper = mountApp()
    setViewport(true)
    // 响应式 ref 更新经 Vue 调度器异步 flush，需等下一帧再断言
    await nextTick()
    const container = wrapper.findComponent(ToastContainer)
    expect(container.props('position')).toBe('bottom-center')
    const className = String(container.element.className)
    expect(className).toContain('bottom-4')
    expect(className).toContain('left-1/2')
    wrapper.unmount()
  })
})
