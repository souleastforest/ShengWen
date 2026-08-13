/**
 * P1 防线加固（安全 + 一致性守卫）——App.vue 装配层
 *
 * 覆盖：
 * 1. [P1-1] XSS 单点净化：markdown 编译管线出口（compiledMarkdown 赋值处）调用
 *    DOMPurify.sanitize；含 <img onerror> 的 summary 渲染产物被白名单剥离。
 *    注：happy-dom 与 DOMPurify 3.4 全量算法不兼容（见实现注释），此处以
 *    "行为双 + 调用点 spy" 验证管线契约（sanitize 在 marked 输出后、postProcess
 *    前被调用，且其结果流入 v-html 输入）；真实剥离行为在真实浏览器中验证过。
 * 2. [P1-3] 任务切换视图状态全重置：selectTask 后 isEditingTopic=false、
 *    editingTopicValue=''（防任务 A 编辑文本 PATCH 到任务 B）。
 * 3. [P1-3] expandMultipartSummary 在 await fetchTaskFullContent 后做任务身份
 *    重校验（等待期间切换任务不得展开新任务的分P总结）。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import DOMPurify from 'dompurify'
import App from '../App.vue'
import Sidebar from '../components/Sidebar.vue'
import TaskContentArea from '../components/TaskContentArea.vue'
import TaskPartsPanel from '../components/TaskPartsPanel.vue'
import type { Task } from '../types'

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

// DOMPurify 行为双：strip 事件处理器属性（on*）与 style 属性，保留正常内容。
// 真实 DOMPurify 的剥离（onerror/style 等）已在真实浏览器验证；此处验证的是
// "管线在出口调用 sanitize（含 FORBID_ATTR/USE_PROFILES 配置）且结果流入
// 渲染层"这一契约。
vi.mock('dompurify', () => {
  const stripEventHandlers = (html: string) =>
    String(html)
      .replace(/\son\w+=["'][^"']*["']/g, '')
      .replace(/\sstyle=(["'])[^"']*\1/g, '')
  return {
    default: {
      sanitize: vi.fn(stripEventHandlers),
    },
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
  summary_chunk_total: 1,
  summary_chunk_done: 1,
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

describe('P1 防线加固：App.vue 装配层', () => {
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

  describe('[P1-1] XSS 单点净化', () => {
    it('含 <img onerror>/style 的 summary 经编译管线后被净化（DOMPurify 白名单 + FORBID_ATTR 配置）', async () => {
      const wrapper = mountApp()
      const xssTask = makeTask('task-xss', {
        summary: '# 标题\n\n<img src="x" onerror="alert(1)">\n\n<p style="position:fixed">追踪层</p>\n\n正常链接 [B站](https://bilibili.com)',
        transcript: '转录文本',
      })

      installDefaultAxios({
        '/tasks/task-xss?include_content=false': { ...xssTask, transcript: null, summary: xssTask.summary },
      })

      selectTaskViaSidebar(wrapper, xssTask)
      await waitForMarkdownCompile()

      // 管线契约：marked 编译输出（含 onerror 原始 HTML）必须经过 DOMPurify.sanitize，
      // 且配置 FORBID_ATTR: ['style']（LLM 输出可携带追踪/遮罩 CSS）+
      // USE_PROFILES: { html: true }（剔除 svg/mathML 面，本管线不需要）
      expect(DOMPurify.sanitize).toHaveBeenCalledWith(
        expect.stringContaining('onerror'),
        { FORBID_ATTR: ['style'], USE_PROFILES: { html: true } },
      )

      // 渲染输入（compiledMarkdown）不得包含事件处理器与 style 属性
      const compiled = wrapper.findComponent(TaskContentArea).props('compiledMarkdown') as string
      expect(compiled).not.toContain('onerror')
      expect(compiled).not.toContain('style=')
      // 正常 markdown 内容保留（链接、标题）
      expect(compiled).toContain('bilibili.com')
      expect(compiled).toContain('标题')

      wrapper.unmount()
    })
  })

  describe('[P1-3] 任务切换视图状态全重置', () => {
    it('selectTask 后编辑态重置：任务 A 的主题编辑文本不得残留到任务 B', async () => {
      const wrapper = mountApp()
      const taskA = makeTask('task-a', { title: '任务A', summary: 'A的总结', transcript: 'A的转录' })
      const taskB = makeTask('task-b', { title: '任务B', summary: 'B的总结', transcript: 'B的转录' })

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: 'A的总结' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: 'A的转录', summary: 'A的总结' },
        '/tasks/task-b?include_content=false': { ...taskB, transcript: null, summary: 'B的总结' },
        '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的转录', summary: 'B的总结' },
      })

      // 选中任务 A 并进入主题编辑态（编辑框里是 A 的标题文本）
      selectTaskViaSidebar(wrapper, taskA)
      await sleep(50)
      wrapper.findComponent(TaskContentArea).vm.$emit('start-edit-topic')
      await sleep(0)
      expect(wrapper.findComponent(TaskContentArea).props('isEditingTopic')).toBe(true)
      expect(wrapper.findComponent(TaskContentArea).props('editingTopicValue')).toBe('任务A')

      // 切换到任务 B：编辑态必须重置，防止编辑文本 PATCH 到 B
      selectTaskViaSidebar(wrapper, taskB)
      await sleep(50)

      expect(wrapper.findComponent(TaskContentArea).props('isEditingTopic')).toBe(false)
      expect(wrapper.findComponent(TaskContentArea).props('editingTopicValue')).toBe('')

      wrapper.unmount()
    })

    it('expandMultipartSummary 身份守卫：等待完整内容期间切换任务，不展开新任务的完整分P总结', async () => {
      const wrapper = mountApp()
      const taskA = makeTask('task-a', { has_parts: true, summary: 'A的截断总结' })
      const taskB = makeTask('task-b', { summary: 'B的总结' })

      let resolveFullA!: (v: { data: Task }) => void
      const fullAPromise = new Promise<{ data: Task }>((resolve) => {
        resolveFullA = resolve
      })

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: 'A的截断总结' },
        '/tasks/task-a?include_content=true': fullAPromise,
        '/tasks/task-b?include_content=false': { ...taskB, transcript: null, summary: 'B的总结' },
        '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的转录', summary: 'B的总结' },
      })

      // 选中任务 A（分P任务）并点击"展开完整分P总结"
      selectTaskViaSidebar(wrapper, taskA)
      await sleep(50)
      wrapper.findComponent(TaskContentArea).vm.$emit('expand-multipart-summary')

      // 完整内容请求在途时切换到任务 B
      selectTaskViaSidebar(wrapper, taskB)
      await sleep(50)

      // A 的完整内容返回：任务已切换，不得展开 B 的完整分P总结
      resolveFullA({ data: { ...taskA, summary: 'A的完整总结', transcript: 'A的完整转录' } })
      await sleep(50)

      expect(wrapper.findComponent(TaskContentArea).props('showFullMultipartSummary')).toBe(false)

      wrapper.unmount()
    })

    it('expandMultipartSummary 正常路径：同一任务下完整内容返回后展开', async () => {
      const wrapper = mountApp()
      const taskA = makeTask('task-a', { has_parts: true, summary: 'A的截断总结' })

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: 'A的截断总结' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: 'A的转录', summary: 'A的完整总结' },
      })

      selectTaskViaSidebar(wrapper, taskA)
      await sleep(50)
      wrapper.findComponent(TaskContentArea).vm.$emit('expand-multipart-summary')
      await sleep(50)

      expect(wrapper.findComponent(TaskContentArea).props('showFullMultipartSummary')).toBe(true)

      wrapper.unmount()
    })
  })

  describe('[S-4] 重试失败分P后收起残留展开（refreshKey 联动）', () => {
    it('重试触发后向 TaskPartsPanel 传递递增的 refreshKey', async () => {
      const wrapper = mountApp()
      const taskA = makeTask('task-a', { has_parts: true, summary: 'A的总结' })

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: 'A的总结' },
        '/tasks/task-a/parts': [
          { task_id: 'task-a', part_index: 0, status: 'FAILED', progress: 0, title: 'P1' },
        ],
      })

      selectTaskViaSidebar(wrapper, taskA)
      await sleep(50)

      const panel = wrapper.findComponent(TaskPartsPanel)
      expect(panel.exists()).toBe(true)
      expect(panel.props('refreshKey')).toBe(0)

      panel.vm.$emit('retry')
      await sleep(50)

      expect(wrapper.findComponent(TaskPartsPanel).props('refreshKey')).toBe(1)

      wrapper.unmount()
    })
  })

  describe('[S-5] saveTopic 竞态守卫', () => {
    it('任务 A 保存在途时切到 B 并开始编辑，A 保存完成不得误关 B 的编辑态', async () => {
      const wrapper = mountApp()
      const taskA = makeTask('task-a', { title: '任务A', summary: 'A的总结' })
      const taskB = makeTask('task-b', { title: '任务B', summary: 'B的总结' })

      let resolvePatch!: (v: unknown) => void
      const patchPromise = new Promise((resolve) => {
        resolvePatch = resolve
      })

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: 'A的总结' },
        '/tasks/task-b?include_content=false': { ...taskB, transcript: null, summary: 'B的总结' },
      })
      mockedAxios.patch.mockReturnValueOnce(patchPromise)

      // 选中 A 并进入主题编辑态
      selectTaskViaSidebar(wrapper, taskA)
      await sleep(50)
      wrapper.findComponent(TaskContentArea).vm.$emit('start-edit-topic')
      await sleep(0)
      expect(wrapper.findComponent(TaskContentArea).props('isEditingTopic')).toBe(true)

      // 保存 A 的主题（PATCH 在途）
      wrapper.findComponent(TaskContentArea).vm.$emit('save-topic')

      // 等待期间切到 B 并开始编辑 B
      selectTaskViaSidebar(wrapper, taskB)
      await sleep(50)
      wrapper.findComponent(TaskContentArea).vm.$emit('start-edit-topic')
      await sleep(0)
      expect(wrapper.findComponent(TaskContentArea).props('isEditingTopic')).toBe(true)
      expect(wrapper.findComponent(TaskContentArea).props('editingTopicValue')).toBe('任务B')

      // A 的保存完成：不得误关 B 的编辑态
      resolvePatch({ data: {} })
      await sleep(50)

      expect(wrapper.findComponent(TaskContentArea).props('isEditingTopic')).toBe(true)
      expect(wrapper.findComponent(TaskContentArea).props('editingTopicValue')).toBe('任务B')

      wrapper.unmount()
    })

    it('saveTopic 正常路径：同一任务下保存完成后退出编辑态', async () => {
      const wrapper = mountApp()
      const taskA = makeTask('task-a', { title: '任务A', summary: 'A的总结' })

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: 'A的总结' },
      })
      mockedAxios.patch.mockResolvedValue({ data: {} })

      selectTaskViaSidebar(wrapper, taskA)
      await sleep(50)
      wrapper.findComponent(TaskContentArea).vm.$emit('start-edit-topic')
      await sleep(0)
      expect(wrapper.findComponent(TaskContentArea).props('isEditingTopic')).toBe(true)

      wrapper.findComponent(TaskContentArea).vm.$emit('save-topic')
      await sleep(50)

      expect(wrapper.findComponent(TaskContentArea).props('isEditingTopic')).toBe(false)

      wrapper.unmount()
    })
  })
})
