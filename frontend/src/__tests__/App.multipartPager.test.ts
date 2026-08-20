/**
 * 一P一页分页重构：App.vue 装配层接线
 *
 * - 分页器懒拉（评审阻塞项 1 修复）：watch([multipartPage, selectedTask.id])
 *   immediate 无条件 fetchTaskPart —— 打开任务即拉 P0 详情；◀/▶/输入跳转/
 *   分P列表 jump 均经 multipartPage 变化触发懒拉（state.ts 缓存语义：完成且
 *   有内容命中不重复请求，处理中/无内容视为过期重新请求）；
 * - multipartPageCount = parts 数传入 TaskContentArea；
 * - 任务切换 multipartPage 重置为 0；
 * - 详情已在 partDetails 缓存（完成且有内容）时翻页/跳转不再发请求。
 *
 * 真实 API 契约（对抗评审修正）：GET /tasks/{id}/parts 列表行不含 summary，
 * 分P内容只来自 GET /tasks/{id}/parts/{idx}（partDetails）。
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

/** parts 列表行（真实契约：无 summary/transcript，include_text=False 剥离） */
const partRow = (idx: number): TaskPart => ({
  task_id: 'task-a',
  part_index: idx,
  status: 'COMPLETED',
  progress: 1,
  duration: 60,
  title: `P${idx + 1}`,
})

const multipartParts = (count = 2): TaskPart[] =>
  Array.from({ length: count }, (_, idx) => partRow(idx))

const partDetail = (idx: number, summary: string): TaskPart => ({
  ...partRow(idx),
  summary,
})

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

  it('打开多P任务即懒拉 P0 详情（watch immediate 首拉），分页器 ◀/▶/输入跳转均触发 fetchTaskPart', async () => {
    const wrapper = mountApp()
    const taskA = makeTask('task-a', { has_parts: true, summary: '总览' })
    const parts = multipartParts(4)

    installDefaultAxios({
      // 更具体的分P详情路由排在通用 '/tasks/task-a/parts' 之前
      // （installDefaultAxios 按 key 前缀匹配，先到先得）
      '/tasks/task-a/parts/0': partDetail(0, 'P1完整总结'),
      '/tasks/task-a/parts/1': partDetail(1, 'P2完整总结'),
      '/tasks/task-a/parts/2': partDetail(2, 'P3完整总结'),
      '/tasks/task-a/parts/3': partDetail(3, 'P4完整总结'),
      '/tasks/task-a/parts': parts,
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '总览' },
    })

    selectTaskViaSidebar(wrapper, taskA)
    await waitForMarkdownCompile()

    const partGets = (suffix: string) =>
      mockedAxios.get.mock.calls.filter(([url]) => String(url).endsWith(suffix)).length

    // 打开任务首屏：immediate watch 拉 P0 详情（消除"打开任务首屏空白"）
    expect(partGets('/tasks/task-a/parts/0')).toBe(1)
    expect(wrapper.findComponent(TaskContentArea).props('pageCompiledMarkdown')).toContain('P1完整总结')

    // ▶ → P1
    await wrapper.find('[data-testid="multipart-pager-next"]').trigger('click')
    await waitForMarkdownCompile()
    expect(wrapper.findComponent(TaskContentArea).props('multipartPage')).toBe(1)
    expect(partGets('/tasks/task-a/parts/1')).toBe(1)
    expect(wrapper.findComponent(TaskContentArea).props('pageCompiledMarkdown')).toContain('P2完整总结')

    // ▶ → P2
    await wrapper.find('[data-testid="multipart-pager-next"]').trigger('click')
    await waitForMarkdownCompile()
    expect(partGets('/tasks/task-a/parts/2')).toBe(1)

    // 输入跳转 4 → P3
    const input = wrapper.find('[data-testid="multipart-pager-input"]')
    await input.setValue('4')
    await input.trigger('keydown.enter')
    await waitForMarkdownCompile()
    expect(wrapper.findComponent(TaskContentArea).props('multipartPage')).toBe(3)
    expect(partGets('/tasks/task-a/parts/3')).toBe(1)
    expect(wrapper.findComponent(TaskContentArea).props('pageCompiledMarkdown')).toContain('P4完整总结')

    // ◀ 回 P2：已缓存（完成且有内容）→ 不再请求
    await wrapper.find('[data-testid="multipart-pager-prev"]').trigger('click')
    await waitForMarkdownCompile()
    expect(wrapper.findComponent(TaskContentArea).props('multipartPage')).toBe(2)
    expect(partGets('/tasks/task-a/parts/2')).toBe(1)

    wrapper.unmount()
  })

  it('处理中占位自愈：缓存 TRANSCRIBING 详情视为过期，翻页回跳重新请求后完成内容渲染、占位消失', async () => {
    const wrapper = mountApp()
    const taskA = makeTask('task-a', { has_parts: true, summary: '总览' })
    const parts = multipartParts()
    // 详情端点可变：初始返回 TRANSCRIBING（无 summary），"完成后"返回 COMPLETED+summary
    let part0Detail: TaskPart = { ...partRow(0), status: 'TRANSCRIBING' }

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '总览' },
      '/tasks/task-a/parts': parts,
    })
    const defaultGet = mockedAxios.get.getMockImplementation()!
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).endsWith('/tasks/task-a/parts/0')) {
        return Promise.resolve({ data: part0Detail })
      }
      return defaultGet(url)
    })

    selectTaskViaSidebar(wrapper, taskA)
    await waitForMarkdownCompile()

    // 首拉 P0：详情为处理中 → 占位"正在处理中"
    expect(wrapper.text()).toContain('该分P总结正在处理中...')

    // 分P完成：WS 只刷新 parts 列表不写 partDetails（详情端点状态已更新）
    part0Detail = partDetail(0, 'P1完整总结')

    // 翻页 ▶ → P1，再 ◀ → P0：处理中缓存视为必然过期 → 重新请求 → 自愈
    await wrapper.find('[data-testid="multipart-pager-next"]').trigger('click')
    await waitForMarkdownCompile()
    await wrapper.find('[data-testid="multipart-pager-prev"]').trigger('click')
    await waitForMarkdownCompile()

    // P0 被重新请求（处理中缓存未命中）
    const part0Calls = mockedAxios.get.mock.calls.filter(([url]) => String(url).endsWith('/tasks/task-a/parts/0'))
    expect(part0Calls.length).toBeGreaterThanOrEqual(2)

    // 完成内容渲染、占位消失
    expect(wrapper.findComponent(TaskContentArea).props('pageCompiledMarkdown')).toContain('P1完整总结')
    expect(wrapper.text()).not.toContain('该分P总结正在处理中...')

    wrapper.unmount()
  })

  it('点击分P行（jump）→ 切换 multipartPage（watch 驱动懒拉）+ 滚动进入视野', async () => {
    const wrapper = mountApp()
    const taskA = makeTask('task-a', { has_parts: true, summary: '总览' })
    const detail1 = partDetail(1, 'P2详情完整版')

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

    // 模拟 TaskPartsPanel 分P行点击 → jump(1)：multipartPage 变化 → watch 懒拉
    wrapper.findComponent(TaskPartsPanel).vm.$emit('jump', 1)
    await sleep(50)

    const contentArea = wrapper.findComponent(TaskContentArea)
    expect(contentArea.props('multipartPage')).toBe(1)

    // 详情缺失 → watch 懒拉 fetchTaskPart
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
    const detail0 = partDetail(0, 'P1详情完整版')
    const detail1 = partDetail(1, 'P2详情完整版')

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

    // 打开任务已首拉 P0；jump(1)：详情缺失 → watch 发请求
    expect(partGets().filter(([url]) => String(url).endsWith('/parts/0')).length).toBe(1)
    wrapper.findComponent(TaskPartsPanel).vm.$emit('jump', 1)
    await waitForMarkdownCompile()
    expect(partGets().filter(([url]) => String(url).endsWith('/parts/1')).length).toBe(1)

    // 跳回 0（缓存命中 → 不再请求），再跳 1（缓存命中 → 不再请求）
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

    // 切到任务 B（非多P）：watch 守卫（has_parts）不拉详情
    selectTaskViaSidebar(wrapper, taskB)
    await waitForMarkdownCompile()

    const contentArea = wrapper.findComponent(TaskContentArea)
    expect(contentArea.props('multipartPage')).toBe(0)
    expect(contentArea.props('multipartPageCount')).toBe(0)
    expect(contentArea.props('overviewCompiledMarkdown')).toContain('B的总结')

    wrapper.unmount()
  })

  it('懒拉失败：fetchTaskPart 的 catch 日志被消费（禁止静默失败）', async () => {
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
