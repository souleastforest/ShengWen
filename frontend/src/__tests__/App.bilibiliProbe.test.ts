/**
 * TDD: B 站分P探针失败/降级时前端不静默提交（P0-3 + P0-5 前端侧）。
 *
 * 根因（2026-08-19 缺陷）：DNS 故障时 /bilibili/video-info 降级为
 * is_multi_part=false 空响应，前端 `info && info.is_multi_part` 为 false →
 * else 分支静默 submitTask() 盲建单P任务；探针 HTTP 异常时
 * checkBilibiliVideoInfo 吞错返回 null → 同样静默提交。
 *
 * 修复契约：
 * ⑦ 探针失败（HTTP 异常）→ 不静默提交：toast 提示 + 二次确认
 *    （confirm=false 不提交；confirm=true 仍按单P提交）；
 * ⑧ 降级响应（status='degraded' 或启发式特征 is_multi_part=false &&
 *    title==='' && duration===0 && parts===null）→ 同 ⑦ 提示路径；
 * ⑨ 单P（status='ok'，含旧后端无 status 字段）→ 直接提交；
 *    正常多P → 打开分P选择器，不提交。
 * 失败 = 实现缺陷。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import App from '../App.vue'
import ToastContainer from '../components/ToastContainer.vue'
import BilibiliPartsSelector from '../components/BilibiliPartsSelector.vue'
import type { ToastItem } from '../composables/useToast'

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
let postCalls: Array<{ url: string; body: unknown }> = []
// 探针响应：resolve 数据 或 reject 异常
let probeResponse: { data: Record<string, unknown> } | { reject: Error } = {
  data: { is_multi_part: false, title: 't', bvid: 'BV1xx', duration: 10, status: 'ok' },
}

const installDefaultAxios = () => {
  mockedAxios.get.mockImplementation((url: string) => {
    if (String(url).includes('/upload/config')) {
      return Promise.resolve({ data: { max_upload_mb: 2048 } })
    }
    return Promise.resolve({ data: [] })
  })
  mockedAxios.post.mockImplementation((url: string, body?: unknown) => {
    if (String(url) === '/bilibili/video-info') {
      if ('reject' in probeResponse) return Promise.reject(probeResponse.reject)
      return Promise.resolve({ data: probeResponse.data })
    }
    postCalls.push({ url: String(url), body })
    return Promise.resolve({ data: {} })
  })
  mockedAxios.put.mockResolvedValue({ data: {} })
  mockedAxios.patch.mockResolvedValue({ data: {} })
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
}

const mountApp = () => mount(App, { global: { stubs: { transition: false } } })

const fillUrlAndSubmit = async (wrapper: ReturnType<typeof mountApp>, url: string) => {
  await wrapper.find('input[placeholder*="粘贴视频 URL"]').setValue(url)
  await wrapper.find('input[placeholder*="粘贴视频 URL"]').trigger('keydown.enter')
  // 等待 axios 链 + toast 更新
  await new Promise((resolve) => setTimeout(resolve, 30))
}

const findToastMessages = (wrapper: ReturnType<typeof mountApp>): string[] => {
  const container = wrapper.findComponent(ToastContainer)
  const toasts = (container.props('toasts') as ToastItem[]) ?? []
  return toasts.map((t) => t.message)
}

describe('P0-3/P0-5：B 站分P探针失败/降级不静默提交', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
    postCalls = []
    probeResponse = {
      data: { is_multi_part: false, title: 't', bvid: 'BV1xx', duration: 10, status: 'ok' },
    }

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

    installDefaultAxios()
  })

  it('⑦ 探针 HTTP 失败：不静默提交；toast 提示；二次确认 false 时不提交', async () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    probeResponse = { reject: new Error('network down') }

    const wrapper = mountApp()
    await fillUrlAndSubmit(wrapper, 'https://b23.tv/CD6M1qC')

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(postCalls.filter((c) => c.url === '/tasks/')).toHaveLength(0)
    expect(findToastMessages(wrapper).some((m) => m.includes('无法确认视频分P信息'))).toBe(true)

    wrapper.unmount()
  })

  it('⑦ 探针 HTTP 失败：二次确认 true → 仍按单P提交（后端下载器预检测兜底拦截多P）', async () => {
    const confirm = vi.fn(() => true)
    vi.stubGlobal('confirm', confirm)
    probeResponse = { reject: new Error('network down') }

    const wrapper = mountApp()
    await fillUrlAndSubmit(wrapper, 'https://b23.tv/CD6M1qC')

    expect(confirm).toHaveBeenCalledTimes(1)
    const taskPosts = postCalls.filter((c) => c.url === '/tasks/')
    expect(taskPosts).toHaveLength(1)
    expect((taskPosts[0]!.body as { video_url: string }).video_url).toBe('https://b23.tv/CD6M1qC')

    wrapper.unmount()
  })

  it('⑧ 降级响应（status=degraded）：识别为降级，走提示路径不静默提交', async () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    probeResponse = {
      data: {
        is_multi_part: false,
        title: '',
        bvid: 'BV1eh411h7xH',
        duration: 0,
        parts: null,
        status: 'degraded',
      },
    }

    const wrapper = mountApp()
    await fillUrlAndSubmit(wrapper, 'https://b23.tv/CD6M1qC')

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(postCalls.filter((c) => c.url === '/tasks/')).toHaveLength(0)
    expect(findToastMessages(wrapper).some((m) => m.includes('无法确认视频分P信息'))).toBe(true)

    wrapper.unmount()
  })

  it('⑧ 旧后端无 status 字段的启发式降级特征（空 title/duration/parts）→ 同样走提示路径', async () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    probeResponse = {
      data: {
        is_multi_part: false,
        title: '',
        bvid: 'BV1eh411h7xH',
        duration: 0,
        parts: null,
      },
    }

    const wrapper = mountApp()
    await fillUrlAndSubmit(wrapper, 'https://www.bilibili.com/video/BV1eh411h7xH')

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(postCalls.filter((c) => c.url === '/tasks/')).toHaveLength(0)

    wrapper.unmount()
  })

  it('⑨ 单P status=ok：直接提交，无二次确认', async () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    probeResponse = {
      data: {
        is_multi_part: false,
        title: '单P标题',
        bvid: 'BV1eh411h7xH',
        duration: 60,
        parts: null,
        status: 'ok',
      },
    }

    const wrapper = mountApp()
    await fillUrlAndSubmit(wrapper, 'https://www.bilibili.com/video/BV1eh411h7xH')

    expect(confirm).not.toHaveBeenCalled()
    const taskPosts = postCalls.filter((c) => c.url === '/tasks/')
    expect(taskPosts).toHaveLength(1)

    wrapper.unmount()
  })

  it('⑨ 旧后端单P响应（无 status 字段，正常特征）→ 直接提交不回归', async () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    probeResponse = {
      data: {
        is_multi_part: false,
        title: '单P标题',
        bvid: 'BV1eh411h7xH',
        duration: 60,
        parts: null,
      },
    }

    const wrapper = mountApp()
    await fillUrlAndSubmit(wrapper, 'https://www.bilibili.com/video/BV1eh411h7xH')

    expect(confirm).not.toHaveBeenCalled()
    expect(postCalls.filter((c) => c.url === '/tasks/')).toHaveLength(1)

    wrapper.unmount()
  })

  it('⑨ 正常多P：打开分P选择器，不提交任务', async () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    probeResponse = {
      data: {
        is_multi_part: true,
        title: '多P标题',
        bvid: 'BV1eh411h7xH',
        duration: 120,
        parts: [
          { index: 0, cid: 1001, title: 'P1', duration: 60 },
          { index: 1, cid: 1002, title: 'P2', duration: 60 },
        ],
        status: 'ok',
      },
    }

    const wrapper = mountApp()
    await fillUrlAndSubmit(wrapper, 'https://www.bilibili.com/video/BV1eh411h7xH')

    expect(confirm).not.toHaveBeenCalled()
    expect(postCalls.filter((c) => c.url === '/tasks/')).toHaveLength(0)
    const selector = wrapper.findComponent(BilibiliPartsSelector)
    expect(selector.exists()).toBe(true)
    expect(selector.props('isOpen')).toBe(true)

    wrapper.unmount()
  })
})
