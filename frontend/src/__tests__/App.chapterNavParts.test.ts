/**
 * 章节胶囊分P章节项（App.vue 装配层接线）
 *
 * - headings 合并：章节胶囊 = 总览收集标题（update-markdown-headings 流）+ 分P章节项
 *   （每 parts 一项：{ id: 'part-' + part_index, text: 'P' + (part_index+1) + ' ' + title,
 *   level: 2 }，追加在总览标题之后）；
 * - 跳转分流：分P项 jump → changeMultipartPage + scrollContentIntoView（与分P面板
 *   handlePartJump 同路径）；普通标题 → 原 headingJumpRequest scrollTo 流不变；
 * - 高亮：多P任务 activeHeadingId = 'part-' + multipartPage；其余沿用
 *   update-active-heading-id 流（总览标题高亮逻辑保持）；
 * - 非多P任务完全回归（无分P项）；任务切换旧任务分P项不残留。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import App from '../App.vue'
import Sidebar from '../components/Sidebar.vue'
import FloatingToolbarChapterNav from '../components/FloatingToolbarChapterNav.vue'
import TaskContentArea from '../components/TaskContentArea.vue'
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
  created_at: '2026-08-20T00:00:00Z',
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
  title: `P${idx + 1} 标题`,
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

/** 多P 任务标准装配（总览含一个一级标题 + 每分P详情含一个二级标题） */
const installMultipartTask = (wrapper: ReturnType<typeof mountApp>, taskId = 'task-a', count = 2) => {
  const taskA = makeTask(taskId, { has_parts: true, summary: '# 总体概览\n这是总览内容' })
  const overrides: Record<string, unknown> = {
    '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '# 总体概览\n这是总览内容' },
    '/tasks/task-a/parts': multipartParts(count),
  }
  for (let i = 0; i < count; i++) {
    overrides[`/tasks/task-a/parts/${i}`] = partDetail(i, `## P${i + 1}小节\n分P${i + 1}内容`)
  }
  installDefaultAxios(overrides)
  selectTaskViaSidebar(wrapper, taskA)
  return taskA
}

describe('App：章节胶囊分P章节项', () => {
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

  it('多P任务：章节胶囊含 P1~PN 分P项（总览标题之后，level 2）', async () => {
    const wrapper = mountApp()
    installMultipartTask(wrapper, 'task-a', 3)
    await waitForMarkdownCompile()

    const nav = wrapper.findComponent(FloatingToolbarChapterNav)
    const headings = nav.props('headings') as Array<{ id: string; text: string; level: number }>
    const partItems = headings.filter((h) => h.id.startsWith('part-'))

    // 分P项生成契约
    expect(partItems.map((h) => h.id)).toEqual(['part-0', 'part-1', 'part-2'])
    expect(partItems.map((h) => h.text)).toEqual(['P1 P1 标题', 'P2 P2 标题', 'P3 P3 标题'])
    expect(partItems.map((h) => h.level)).toEqual([2, 2, 2])

    // 分P项追加在总览收集标题之后（总览标题仍存在）
    const firstPartIndex = headings.findIndex((h) => h.id.startsWith('part-'))
    const overviewIds = headings.slice(0, firstPartIndex).map((h) => h.id)
    expect(overviewIds).toContain('总体概览')
    expect(partItems.length).toBe(3)

    wrapper.unmount()
  })

  it('点击分P章节项 → 切换 multipartPage + 滚动内容区进入视野（与分P面板 jump 一致）', async () => {
    const wrapper = mountApp()
    installMultipartTask(wrapper, 'task-a', 2)
    await waitForMarkdownCompile()

    const scrollSpy = vi.fn()
    Element.prototype.scrollIntoView = scrollSpy

    const nav = wrapper.findComponent(FloatingToolbarChapterNav)
    const partButton = nav
      .findAll('button')
      .find((b) => b.text().includes('P2 标题'))
    expect(partButton).toBeTruthy()
    await partButton!.trigger('click')

    expect(wrapper.findComponent(TaskContentArea).props('multipartPage')).toBe(1)
    expect(scrollSpy).toHaveBeenCalled()

    // 详情懒拉仍由 multipartPage watch 驱动（复用分P面板 jump 同一条路径）
    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-a/parts/1')

    wrapper.unmount()
  })

  it('多P任务：章节胶囊高亮当前分P页（part-x），翻页同步', async () => {
    const wrapper = mountApp()
    installMultipartTask(wrapper, 'task-a', 2)
    await waitForMarkdownCompile()

    // 打开任务在第 0 页 → 高亮 part-0
    expect(wrapper.findComponent(FloatingToolbarChapterNav).props('activeHeadingId')).toBe('part-0')

    // 分页器翻页 → 高亮 part-1
    await wrapper.find('[data-testid="multipart-pager-next"]').trigger('click')
    await waitForMarkdownCompile()
    expect(wrapper.findComponent(FloatingToolbarChapterNav).props('activeHeadingId')).toBe('part-1')

    wrapper.unmount()
  })

  it('点击总览标题 → 原 headingJumpRequest scrollTo 流不变（不切分P页）', async () => {
    const wrapper = mountApp()
    installMultipartTask(wrapper, 'task-a', 2)
    await waitForMarkdownCompile()

    const nav = wrapper.findComponent(FloatingToolbarChapterNav)
    const overviewButton = nav.findAll('button').find((b) => b.text().includes('总体概览'))
    expect(overviewButton).toBeTruthy()
    await overviewButton!.trigger('click')

    const contentArea = wrapper.findComponent(TaskContentArea)
    // 不切换分P页
    expect(contentArea.props('multipartPage')).toBe(0)
    // 进入 scrollTo 流：headingJumpRequest 透传（requestId 递增）
    expect(contentArea.props('headingJumpRequest')?.id).toBe('总体概览')

    wrapper.unmount()
  })

  it('非多P任务：完全回归（无分P项；高亮沿用 update-active-heading-id 流）', async () => {
    const wrapper = mountApp()
    const taskB = makeTask('task-b', { summary: '# 总体概览\n单P内容' })

    installDefaultAxios({
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null, summary: '# 总体概览\n单P内容' },
    })
    selectTaskViaSidebar(wrapper, taskB)
    await waitForMarkdownCompile()

    const nav = wrapper.findComponent(FloatingToolbarChapterNav)
    const headings = nav.props('headings') as Array<{ id: string; text: string; level: number }>
    expect(headings.some((h) => h.id.startsWith('part-'))).toBe(false)
    expect(headings.map((h) => h.id)).toContain('总体概览')
    // 总览标题高亮逻辑保持：流报告的首个收集标题
    expect(nav.props('activeHeadingId')).toBe('总体概览')

    wrapper.unmount()
  })

  it('任务切换：多P → 单P，旧任务分P项不残留', async () => {
    const wrapper = mountApp()
    installMultipartTask(wrapper, 'task-a', 2)
    await waitForMarkdownCompile()

    const navOf = () => wrapper.findComponent(FloatingToolbarChapterNav)
    expect(navOf().props('headings').some((h: { id: string }) => h.id.startsWith('part-'))).toBe(true)

    const taskB = makeTask('task-b', { summary: '# 总体概览\n单P内容' })
    installDefaultAxios({
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null, summary: '# 总体概览\n单P内容' },
    })
    selectTaskViaSidebar(wrapper, taskB)
    await waitForMarkdownCompile()

    const headings = navOf().props('headings') as Array<{ id: string }>
    expect(headings.some((h) => h.id.startsWith('part-'))).toBe(false)

    wrapper.unmount()
  })

  it('parts 列表变化：章节胶囊分P项同步（新增分P后旧项不残留）', async () => {
    const wrapper = mountApp()
    const taskA = makeTask('task-a', { has_parts: true, summary: '# 总体概览\n这是总览内容' })
    let parts = multipartParts(2)

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '# 总体概览\n这是总览内容' },
    })
    const defaultGet = mockedAxios.get.getMockImplementation()!
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).endsWith('/tasks/task-a/parts')) {
        return Promise.resolve({ data: parts })
      }
      const detailMatch = String(url).match(/\/tasks\/task-a\/parts\/(\d+)$/)
      if (detailMatch) {
        const idx = Number(detailMatch[1])
        return Promise.resolve({ data: partDetail(idx, `## P${idx + 1}小节\n分P${idx + 1}内容`) })
      }
      return defaultGet(url)
    })

    selectTaskViaSidebar(wrapper, taskA)
    await waitForMarkdownCompile()

    const navOf = () => wrapper.findComponent(FloatingToolbarChapterNav)
    const partIdsOf = () =>
      (navOf().props('headings') as Array<{ id: string }>)
        .filter((h) => h.id.startsWith('part-'))
        .map((h) => h.id)
    expect(partIdsOf()).toEqual(['part-0', 'part-1'])

    // parts 列表新增到 3 项（重新选择同任务触发重拉）→ 分P项同步为 3 项
    parts = multipartParts(3)
    selectTaskViaSidebar(wrapper, taskA)
    await waitForMarkdownCompile()
    expect(partIdsOf()).toEqual(['part-0', 'part-1', 'part-2'])

    wrapper.unmount()
  })
})
