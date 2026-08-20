/**
 * 一P一页分页重构：App.vue 装配层接线
 *
 * - TaskPartsPanel @jump → handlePartJump：切 multipartPage + 详情缺失懒拉
 *   fetchTaskPart（复用 state.ts 缓存语义，禁止静默失败）+ 滚动内容区进入视野；
 * - multipartPageCount = parts 数传入 TaskContentArea；
 * - 任务切换 multipartPage 重置为 0；
 * - 详情已在 partDetails 缓存时再次 jump 不再发请求。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import App from '../App.vue'
import Sidebar from '../components/Sidebar.vue'
import TaskContentArea from '../components/TaskContentArea.vue'
import TaskPartsPanel from '../components/TaskPartsPanel.vue'
import type { Task, TaskPart } from '../types'

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

vi.mock('dompurify', () => {
  const stripEventHandlers = (html: string) =>
    String(html)
      .replace(/\son\w+=["'][^"']*["']/g, '')
      .replace(/\sstyle=(["'])[^"']*\1/g, '')
  return {
    default: { sanitize: vi.fn(stripEventHandlers) },
  }
})

const mockedAxios = vi.mocked(axios)

const completedTask: Task = {
  id: 'task-1',
  video_url: 'https://example.com/video.mp4',
  status: 'COMPLETED',
  progress: 1.0,
  created_at: '2026-04-07T00:00:00Z',
  summary_mode: 'agent',
}

const makeTask = (id: string, extra?: Partial<Task>): Task => ({ ...completedTask, id, ...extra })

type WsInstance = {
  close: () => void
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: ((err: unknown) => void) | null
}

const wsInstances: WsInstance[] = []

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

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
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
}

const mountApp = () => mount(App, { global: { stubs: { transition: false } } })

const selectTaskViaSidebar = (wrapper: ReturnType<typeof mountApp>, task: Task) => {
  wrapper.findComponent(Sidebar).vm.$emit('selectTask', task)
}

const waitForMarkdownCompile = async () => {
  // scheduleMarkdownCompile 使用 120ms 定时器
  await sleep(250)
}

const multipartParts = (): TaskPart[] => [
  { task_id: 'task-a', part_index: 0, status: 'COMPLETED', progress: 1, duration: 60, title: 'P1', summary: 'P1总结' },
  { task_id: 'task-a', part_index: 1, status: 'COMPLETED', progress: 1, duration: 120, title: 'P2', summary: 'P2总结' },
]

describe('App：一P一页分页器接线', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
    Element.prototype.scrollIntoView = vi.fn()

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

  it('multipartPageCount 传给 TaskContentArea = parts 数（一P一页）', async () => {
    const wrapper = mountApp()
    const taskA = makeTask('task-a', { has_parts: true, summary: '总览' })

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '总览' },
      '/tasks/task-a/parts': multipartParts(),
    })

    selectTaskViaSidebar(wrapper, taskA)
    await sleep(50)
    await waitForMarkdownCompile()

    const contentArea = wrapper.findComponent(TaskContentArea)
    expect(contentArea.props('multipartPageCount')).toBe(2)
    expect(contentArea.props('multipartPage')).toBe(0)
    expect(contentArea.props('overviewCompiledMarkdown')).toContain('总览')

    wrapper.unmount()
  })

  it('点击分P行（jump）→ 切换 multipartPage + 详情缺失懒拉 fetchTaskPart + 滚动进入视野', async () => {
    const wrapper = mountApp()
    const taskA = makeTask('task-a', { has_parts: true, summary: '总览' })
    const detail1: TaskPart = { ...multipartParts()[1]!, summary: 'P2详情完整版' }

    installDefaultAxios({
      // 注意：更具体的分P详情路由必须排在 '/tasks/task-a/parts' 之前
      // （installDefaultAxios 按 key 前缀匹配，先到先得）
      '/tasks/task-a/parts/1': detail1,
      '/tasks/task-a/parts': multipartParts(),
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '总览' },
    })

    selectTaskViaSidebar(wrapper, taskA)
    await waitForMarkdownCompile()

    const scrollSpy = vi.fn()
    Element.prototype.scrollIntoView = scrollSpy

    // 模拟 TaskPartsPanel 分P行点击 → jump(1)
    wrapper.findComponent(TaskPartsPanel).vm.$emit('jump', 1)
    await sleep(50)

    const contentArea = wrapper.findComponent(TaskContentArea)
    expect(contentArea.props('multipartPage')).toBe(1)

    // 详情缺失 → 懒拉 fetchTaskPart
    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-a/parts/1')

    // 滚动内容区进入视野
    expect(scrollSpy).toHaveBeenCalled()

    // 详情返回后当前页内容重编译为详情完整版
    await waitForMarkdownCompile()
    expect(contentArea.props('pageCompiledMarkdown')).toContain('P2详情完整版')

    wrapper.unmount()
  })

  it('jump 目标分P详情已在 partDetails 缓存：不重复发请求', async () => {
    const wrapper = mountApp()
    const taskA = makeTask('task-a', { has_parts: true, summary: '总览' })
    const detail0: TaskPart = { ...multipartParts()[0]!, summary: 'P1详情完整版' }
    const detail1: TaskPart = { ...multipartParts()[1]!, summary: 'P2详情完整版' }

    installDefaultAxios({
      // 更具体的分P详情路由排在通用 '/tasks/task-a/parts' 之前
      '/tasks/task-a/parts/0': detail0,
      '/tasks/task-a/parts/1': detail1,
      '/tasks/task-a/parts': multipartParts(),
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '总览' },
    })

    selectTaskViaSidebar(wrapper, taskA)
    await waitForMarkdownCompile()

    const partGets = () => mockedAxios.get.mock.calls.filter(([url]) => String(url).includes('/parts/'))

    // 首次 jump：详情缺失 → 发请求
    wrapper.findComponent(TaskPartsPanel).vm.$emit('jump', 1)
    await waitForMarkdownCompile()
    expect(partGets().filter(([url]) => String(url).endsWith('/parts/1')).length).toBe(1)

    // 跳回 0（详情缺失 → 请求），再跳 1：缓存命中 → 不再请求
    wrapper.findComponent(TaskPartsPanel).vm.$emit('jump', 0)
    await waitForMarkdownCompile()
    wrapper.findComponent(TaskPartsPanel).vm.$emit('jump', 1)
    await waitForMarkdownCompile()

    expect(partGets().filter(([url]) => String(url).endsWith('/parts/1')).length).toBe(1)
    expect(partGets().filter(([url]) => String(url).endsWith('/parts/0')).length).toBe(1)

    wrapper.unmount()
  })

  it('任务切换：multipartPage 重置为 0', async () => {
    const wrapper = mountApp()
    const taskA = makeTask('task-a', { has_parts: true, summary: '总览' })
    const taskB = makeTask('task-b', { summary: 'B的总结' })

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '总览' },
      '/tasks/task-a/parts': multipartParts(),
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null, summary: 'B的总结' },
    })

    selectTaskViaSidebar(wrapper, taskA)
    await waitForMarkdownCompile()
    wrapper.findComponent(TaskPartsPanel).vm.$emit('jump', 1)
    await waitForMarkdownCompile()
    expect(wrapper.findComponent(TaskContentArea).props('multipartPage')).toBe(1)

    // 切到任务 B（非多P）
    selectTaskViaSidebar(wrapper, taskB)
    await waitForMarkdownCompile()

    const contentArea = wrapper.findComponent(TaskContentArea)
    expect(contentArea.props('multipartPage')).toBe(0)
    expect(contentArea.props('multipartPageCount')).toBe(0)
    expect(contentArea.props('overviewCompiledMarkdown')).toContain('B的总结')

    wrapper.unmount()
  })

  it('jump 懒拉失败：fetchTaskPart 的 catch 日志被消费（禁止静默失败）', async () => {
    const wrapper = mountApp()
    const taskA = makeTask('task-a', { has_parts: true, summary: '总览' })

    installDefaultAxios({
      '/tasks/task-a/parts': multipartParts(),
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '总览' },
    })
    // 懒构造的拒绝 Promise（避免对象字面量构造期即产生未处理 rejection）
    const defaultGet = mockedAxios.get.getMockImplementation()!
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).includes('/tasks/task-a/parts/1')) {
        return Promise.reject(new Error('network down'))
      }
      return defaultGet(url)
    })

    selectTaskViaSidebar(wrapper, taskA)
    await waitForMarkdownCompile()

    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    wrapper.findComponent(TaskPartsPanel).vm.$emit('jump', 1)
    await sleep(50)

    // 错误被 catch 并记录日志（未抛为未处理 rejection）
    expect(consoleError).toHaveBeenCalledWith(
      expect.stringContaining('Failed to fetch task part'),
      expect.anything(),
    )
    // 页面仍切到对应页
    expect(wrapper.findComponent(TaskContentArea).props('multipartPage')).toBe(1)

    wrapper.unmount()
  })
})
