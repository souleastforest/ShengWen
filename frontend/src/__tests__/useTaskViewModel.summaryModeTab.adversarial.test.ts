/**
 * 对抗性测试：模式 Tab 三选一（需求 1）+ 残留缺陷修复（需求 2）的攻击向验证
 *
 * 攻击点（从需求出发，不信任实现自测）：
 * 1. 默认值：不传 prop / composable 默认必须是 'none'；三种提交路径 × 三态共 9 组 payload。
 * 2. thumb 边界：三态互斥 + 宽度类存在 + 历史值 'auto' 不得 fall-through 到 agent 样式。
 * 3. 旧 UI 残留：'仅转录原文' toggle 文本不得存在于渲染结果。
 * 4. 泄漏检查：模式不得影响 re-summarize 语义之外的提交（PATCH topic 等）。
 * 5. hasContent 三态边界：undefined / null / '' 均触发按需加载且响应空值后不循环。
 * 6. 版本计数边界：同 id 并发请求去重；过期在途完整响应（summary 方向）不得覆盖新内容，
 *    但 status/progress 必须照常合并。
 * 7. confirm 触发条件：summaryMode='none' 且任务有总结 → 必须先 confirm；信号判定
 *    覆盖 content（summary 有内容）与模式信号（summary_mode ∈ {standard, agent}），
 *    堵住轻量详情在途/失败、列表条目剥离 summary 两个静默跳过窗口。
 * 8. TaskMetaCard：'none' → "仅转录"，'auto' → "自动模式"。
 *
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { __resetTaskContentCaches, useTaskViewModel } from '../composables/useTaskViewModel'
import Sidebar from '../components/Sidebar.vue'
import TaskMetaCard from '../components/TaskMetaCard.vue'
import type { SummaryMode, Task } from '../types'

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

const baseTask: Task = {
  id: 'task-1',
  video_url: 'https://example.com/video.mp4',
  status: 'COMPLETED',
  progress: 1.0,
  created_at: '2026-04-07T00:00:00Z',
  summary_mode: 'agent',
  summary_chunk_total: 1,
  summary_chunk_done: 1,
}

const makeTask = (id: string): Task => ({ ...baseTask, id })

// ---- composable 层辅助（与 useTaskViewModel.adversarial.test.ts 同构） ----

const mountViewModel = () => {
  let viewModel!: ReturnType<typeof useTaskViewModel>

  const TestComponent = defineComponent({
    setup() {
      viewModel = useTaskViewModel()
      return () => null
    },
  })

  const wrapper = mount(TestComponent)
  return { viewModel, wrapper }
}

type WsInstance = {
  close: () => void
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: ((err: unknown) => void) | null
}

const wsInstances: WsInstance[] = []

const emitWsTaskUpdate = (task: Task) => {
  const ws = wsInstances[wsInstances.length - 1]
  if (!ws) return
  ws.onmessage?.({ data: JSON.stringify({ type: 'task_update', task }) })
}

type Deferred = { promise: Promise<{ data: unknown }>; resolve: (v: { data: unknown }) => void }

const deferred = (): Deferred => {
  let resolve!: (v: { data: unknown }) => void
  const promise = new Promise<{ data: unknown }>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

const installDefaultAxios = (overrides?: Record<string, unknown>) => {
  mockedAxios.get.mockImplementation((url: string) => {
    const match = Object.entries(overrides ?? {}).find(([key]) => String(url).includes(key))
    if (match) {
      const v = match[1]
      if (v && typeof v === 'object' && 'promise' in v) return (v as Deferred).promise
      return Promise.resolve({ data: v })
    }
    return Promise.resolve({ data: [] })
  })
  mockedAxios.post.mockResolvedValue({ data: {} })
  mockedAxios.patch.mockResolvedValue({ data: {} })
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
}

const fullRequests = () =>
  mockedAxios.get.mock.calls.filter(([url]) => String(url).includes('include_content=true'))

const setupWsAndEnv = () => {
  __resetTaskContentCaches()
  vi.clearAllMocks()
  vi.spyOn(console, 'error').mockImplementation(() => {})
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
  vi.stubGlobal('navigator', {
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  })
}

// ---- Sidebar 层辅助 ----

const sidebarBaseProps = {
  isLocalClient: false,
  tasks: [] as Task[],
  queues: [],
  selectedTask: null as Task | null,
  isSubmitting: false,
  llmProviders: [],
  llmSettings: null,
  isUpdatingLlmSettings: false,
  isTestingLlm: false,
  transcriptionSettings: null,
  isUpdatingTranscriptionSettings: false,
  summarizationSettings: null,
  isUpdatingSummarizationSettings: false,
}

const mountSidebar = (summaryMode?: SummaryMode) => {
  return mount(Sidebar, {
    props: {
      ...sidebarBaseProps,
      videoUrl: '',
      selectedFile: null,
      localFilePath: '',
      isSidebarOpen: true,
      // 'auto' 为历史兼容值（[S7] 用例验证其 thumb 不 fall-through）：运行时透传，
      // 仅类型收窄到组件声明的 UI 三态
      ...(summaryMode !== undefined
        ? { summaryMode: summaryMode as Exclude<SummaryMode, 'auto'> }
        : {}),
    },
    global: {
      stubs: {
        ThemeSelector: true,
      },
    },
  })
}

const findTab = (wrapper: ReturnType<typeof mountSidebar>, label: string) =>
  wrapper.findAll('button').find((b) => b.text().includes(label))

const findThumb = (wrapper: ReturnType<typeof mountSidebar>) =>
  wrapper.findAll('div').find((d) =>
    d.classes().some((c) => c.includes('w-[calc(33.333%'))
  )

const thumbClass = (wrapper: ReturnType<typeof mountSidebar>) =>
  findThumb(wrapper)?.classes().join(' ') ?? ''

const helperVisible = (wrapper: ReturnType<typeof mountSidebar>) =>
  wrapper.findAll('p').some((p) => p.text().includes('提交后不生成 AI 总结'))

describe('对抗性：模式 Tab 三选一（需求 1）— composable 层 payload', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setupWsAndEnv)

  it('[S1] 不传任何设置时 composable 默认 summaryMode="none"，URL 提交 payload 默认 none', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    expect(viewModel.summaryMode.value).toBe('none')
    viewModel.videoUrl.value = 'https://example.com/video.mp4'
    await viewModel.submitTask()

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none' }),
      expect.anything(),
    )

    wrapper.unmount()
  })

  it.each([
    ['URL 提交', 'url'],
    ['本地路径提交', 'local'],
    ['文件上传', 'file'],
  ] as const)('[S1] 三态 × %s：none/standard/agent 各自写入正确 payload', async (_label, path) => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    for (const mode of ['none', 'standard', 'agent'] as const) {
      viewModel.summaryMode.value = mode
      mockedAxios.post.mockClear()
      if (path === 'url') {
        viewModel.videoUrl.value = 'https://example.com/video.mp4'
        viewModel.localFilePath.value = ''
        viewModel.selectedFile.value = null
        await viewModel.submitTask()
        expect(mockedAxios.post).toHaveBeenCalledWith(
          '/tasks/',
          expect.objectContaining({ summary_mode: mode }),
          expect.anything(),
        )
      } else if (path === 'local') {
        viewModel.localFilePath.value = '/data/video.mp4'
        viewModel.videoUrl.value = ''
        viewModel.selectedFile.value = null
        await viewModel.submitTask()
        expect(mockedAxios.post).toHaveBeenCalledWith(
          '/upload/local-path',
          expect.objectContaining({ file_path: '/data/video.mp4', summary_mode: mode }),
          expect.anything(),
        )
      } else {
        viewModel.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
        viewModel.videoUrl.value = ''
        viewModel.localFilePath.value = ''
        await viewModel.submitTask()
        const [, formData] = mockedAxios.post.mock.calls.find(([u]) => u === '/upload')!
        expect(formData).toBeInstanceOf(FormData)
        expect((formData as FormData).get('summary_mode')).toBe(mode)
      }
    }

    wrapper.unmount()
  })

  it('[S1] 分P提交（submitTaskWithParts）三态 payload 正确', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    viewModel.summaryMode.value = 'standard'
    await viewModel.submitTaskWithParts('https://www.bilibili.com/video/BV1xx', {
      mode: 'merge',
      indices: [0, 1],
    })
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'standard' }),
      expect.anything(),
    )

    viewModel.summaryMode.value = 'agent'
    mockedAxios.post.mockClear()
    await viewModel.submitTaskWithParts('https://www.bilibili.com/video/BV1xx', {
      mode: 'separate',
      indices: [0],
    })
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'agent' }),
      expect.anything(),
    )

    wrapper.unmount()
  })

  it('[S1] 多文件批量提交（submitLocalPathTasks）也携带当前三态模式', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    viewModel.summaryMode.value = 'agent'
    await viewModel.submitLocalPathTasks(['/a.mp3', '/b.mp3'], 'separate')

    const calls = mockedAxios.post.mock.calls.filter(([u]) => u === '/upload/local-path')
    expect(calls).toHaveLength(2)
    for (const [, payload] of calls) {
      expect(payload).toEqual(expect.objectContaining({ summary_mode: 'agent' }))
    }

    wrapper.unmount()
  })

  it('[S6] 无泄漏：updateTaskTopic（PATCH）payload 不含 summary_mode；reDownloadAudio 不含', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    viewModel.summaryMode.value = 'agent'
    await viewModel.updateTaskTopic('task-a', '新主题')
    expect(mockedAxios.patch).toHaveBeenCalledWith(
      '/tasks/task-a',
      expect.objectContaining({ topic: '新主题' }),
    )
    const patchPayload = mockedAxios.patch.mock.calls[0]![1] as Record<string, unknown>
    expect('summary_mode' in patchPayload).toBe(false)

    mockedAxios.post.mockClear()
    await viewModel.reDownloadAudio('task-a')
    const [, reDownloadPayload] = mockedAxios.post.mock.calls[0]!
    expect(reDownloadPayload).toBeUndefined() // re-download 无请求体

    wrapper.unmount()
  })

  it('[S6] reSummarize 语义内受模式影响：none 时 payload summary_mode="none"（后端 auto 兜底），standard 时 standard', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    viewModel.summaryMode.value = 'none'
    await viewModel.reSummarize('task-a')
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-summarize',
      expect.objectContaining({ summary_mode: 'none' }),
    )

    viewModel.summaryMode.value = 'standard'
    mockedAxios.post.mockClear()
    await viewModel.reSummarize('task-a')
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-summarize',
      expect.objectContaining({ summary_mode: 'standard' }),
    )

    wrapper.unmount()
  })

  it('[S7] reSummarize 显式模式参数优先：传 agent/standard 时 payload 用显式值，覆盖全局；不传沿用全局（向后兼容）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    // 显式模式优先于全局（即使全局是 none）
    viewModel.summaryMode.value = 'none'
    await viewModel.reSummarize('task-a', 'agent')
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-summarize',
      expect.objectContaining({ summary_mode: 'agent' }),
    )

    mockedAxios.post.mockClear()
    await viewModel.reSummarize('task-a', 'standard')
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-summarize',
      expect.objectContaining({ summary_mode: 'standard' }),
    )

    // 不传模式：沿用全局（S6 语义不回归）
    mockedAxios.post.mockClear()
    await viewModel.reSummarize('task-a')
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-summarize',
      expect.objectContaining({ summary_mode: 'none' }),
    )

    wrapper.unmount()
  })
})

describe('对抗性：模式 Tab 三选一（需求 1）— Sidebar 层', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
  })

  it('[S3] 连续点击循环 none→standard→agent→none：emit 序列正确、最终状态一致、thumb 随动', async () => {
    const wrapper = mountSidebar()

    const clicks: Array<[string, string]> = [
      ['标准模式', 'standard'],
      ['Agent 模式', 'agent'],
      ['仅转录', 'none'],
    ]
    const expectedThumbs = [
      'left-[calc(33.333%)]',
      'left-[calc(66.667%)]',
      'left-1',
    ]

    for (let i = 0; i < clicks.length; i++) {
      const [label, mode] = clicks[i]!
      const tab = findTab(wrapper, label)!
      await tab.trigger('click')
      const emits = wrapper.emitted('update:summaryMode')!
      expect(emits[emits.length - 1]).toEqual([mode])
      expect(thumbClass(wrapper)).toContain(expectedThumbs[i])
    }

    // 回到 none：helper 文案恢复可见
    expect(helperVisible(wrapper)).toBe(true)
    wrapper.unmount()
  })

  it('[S4] thumb 宽度类 w-[calc(33.333%-6px)] 三态下均存在（钉住宽度回归）', () => {
    for (const mode of ['none', 'standard', 'agent'] as const) {
      const cls = thumbClass(mountSidebar(mode))
      expect(cls).toContain('w-[calc(33.333%-6px)]')
    }
  })

  it('[S7] 历史值 auto 传入时 thumb 不 fall-through 到 agent 样式（Record 显式兜底）', () => {
    const wrapper = mountSidebar('auto')
    const cls = thumbClass(wrapper)
    // auto → none 位置（Record 显式映射），绝不落到 agent 样式
    expect(cls).toContain('left-1')
    expect(cls).not.toContain('left-[calc(66.667%)]')
    expect(cls).not.toContain('agent-gradient')
    wrapper.unmount()
  })

  it('[S5] 旧 UI 残留检查：渲染结果不含"仅转录原文"按钮/文案；none 时新 helper 文案存在', () => {
    const wrapper = mountSidebar('none')
    expect(wrapper.html()).not.toContain('仅转录原文')
    expect(helperVisible(wrapper)).toBe(true)
    // 非 none 模式不得出现该 helper（需求：仅 none 时提示）
    expect(helperVisible(mountSidebar('standard'))).toBe(false)
    expect(helperVisible(mountSidebar('agent'))).toBe(false)
    wrapper.unmount()
  })
})

describe('对抗性：hasContent 三态边界 + 版本计数（需求 2a/2b）', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setupWsAndEnv)

  it.each([
    ['undefined（轻量列表项）', undefined],
    ['null', null],
    ['空串', ''],
  ] as const)('[B1] transcript=%s 视为未加载：原文 tab 触发按需加载且仅一次', async (_label, transcript) => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = { ...makeTask('task-a'), transcript } as unknown as Task

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: null },
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA)
    await flushPromises()

    // 三种"无内容"形态都必须触发一次加载
    expect(fullRequests()).toHaveLength(1)
    // 响应仍为空：依赖（activeTab, id）不变，不无限重发
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    wrapper.unmount()
  })

  it('[B2] 同 id 并发 fetchTaskFullContent 去重：tab watch 与 copyContent 并发只发 1 次 GET', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')

    const fullA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': fullA,
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA)
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    // 并发第二次调用（如 copyContent 路径）：复用同一 Promise，不新增 GET
    const p2 = viewModel.fetchTaskFullContent('task-a')
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    fullA.resolve({ data: { ...taskA, transcript: '内容' } })
    await p2
    await flushPromises()

    // pending 已清：再次调用重新发请求
    await viewModel.fetchTaskFullContent('task-a')
    expect(fullRequests()).toHaveLength(2)

    wrapper.unmount()
  })

  it('[B3] 过期在途完整响应（summary 方向）：新总结不被旧总结覆盖；status/progress 照常合并', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    const fullA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: null },
      '/tasks/task-a?include_content=true': fullA,
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null, summary: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录', summary: 'B的总结' },
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA)
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    // 期间收到携带新内容的 COMPLETED 广播（版本递增）
    emitWsTaskUpdate({ ...taskA, status: 'COMPLETED', transcript: '新转录', summary: '新总结' })
    await flushPromises()
    expect(viewModel.selectedTask.value?.summary).toBe('新总结')

    // 在途旧完整响应此刻返回：transcript/summary 不得被旧值覆盖
    fullA.resolve({
      data: { ...taskA, status: 'FAILED', progress: 42, transcript: '旧转录', summary: '旧总结' },
    })
    await flushPromises()

    expect(viewModel.selectedTask.value?.transcript).toBe('新转录')
    expect(viewModel.selectedTask.value?.summary).toBe('新总结')
    // 需求 b：status/progress 照常合并
    expect(viewModel.selectedTask.value?.status).toBe('FAILED')
    expect(viewModel.selectedTask.value?.progress).toBe(42)

    // A→B→A：缓存不得回退旧总结
    viewModel.selectTask(taskB)
    await flushPromises()
    viewModel.selectTask(taskA)
    await flushPromises()
    expect(viewModel.selectedTask.value?.summary).toBe('新总结')
    expect(viewModel.selectedTask.value?.transcript).toBe('新转录')

    wrapper.unmount()
  })
})

describe('对抗性：reTranscribe 确认窗口（需求 2c）', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setupWsAndEnv)

  it('[B4] 攻击窗口：轻量详情在途（summary 尚未加载）时 reTranscribe 仍须弹 confirm，取消则中止提交', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')

    // 详情接口（include_content=false）慢：selectedTask 停留在轻量对象（无 summary）
    const lightA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': lightA,
    })

    vi.stubGlobal('confirm', vi.fn())
    const confirmSpy = vi.mocked(confirm).mockReturnValue(false)

    viewModel.summaryMode.value = 'none'
    viewModel.selectTask(taskA) // 同步阶段 selectedTask = 轻量对象
    // 不等待轻量详情返回，立即 re-transcribe（模拟慢网络下用户的即时操作）
    await viewModel.reTranscribe('task-a')

    // 需求 c：任务实际有总结（服务端）时必须先 confirm——summary 缺失的轻量窗口
    // 不得静默跳过确认；模式信号（summary_mode ∈ {standard, agent}）兜底命中，
    // 取消则请求不得发出，避免后端不可恢复地清空 summary。失败 = 实现缺陷。
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).not.toHaveBeenCalled()

    lightA.resolve({ data: { ...taskA, summary: '已有总结' } })
    await flushPromises()
    wrapper.unmount()
  })

  it('[B4] 对照：轻量详情已返回（summary 已知）时 reTranscribe 正常弹 confirm，取消则中止', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    vi.stubGlobal('confirm', vi.fn())
    const confirmSpy = vi.mocked(confirm).mockReturnValue(false)

    viewModel.summaryMode.value = 'none'
    viewModel.selectedTask.value = { ...makeTask('task-a'), summary: '已有总结' }
    await viewModel.reTranscribe('task-a')
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('[B5] 防御路径：reTranscribe 传入非选中任务 id 时 target 取列表轻量对象（summary 被后端剥离）→ 模式信号兜底弹 confirm，取消则中止', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    vi.stubGlobal('confirm', vi.fn())
    const confirmSpy = vi.mocked(confirm).mockReturnValue(false)

    viewModel.summaryMode.value = 'none'
    viewModel.tasks.value = [{ ...makeTask('task-a') }] // 列表条目：transcript/summary 被 list_tasks 剥离
    viewModel.selectedTask.value = makeTask('task-b')
    await viewModel.reTranscribe('task-a')

    // 列表条目 summary 缺失，但 summary_mode='agent' 模式信号兜底：必须弹确认
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})

describe('对抗性：TaskMetaCard summary_mode 渲染（需求 2e）', () => {
  it('[B7] none → "仅转录"；auto → "自动模式"；standard/agent 保持既有文案；undefined 不显示该行', () => {
    const mountCard = (summary_mode?: SummaryMode) =>
      mount(TaskMetaCard, {
        props: {
          task: { ...baseTask, summary_mode },
          topic: '主题',
        },
      })

    const noneHtml = mountCard('none').text()
    expect(noneHtml).toContain('仅转录')
    expect(noneHtml).not.toContain('自动模式')
    expect(noneHtml).not.toContain('标准模式')

    const autoHtml = mountCard('auto').text()
    expect(autoHtml).toContain('自动模式')
    expect(autoHtml).not.toContain('仅转录')

    expect(mountCard('standard').text()).toContain('标准模式')
    expect(mountCard('agent').text()).toContain('Agent 增强模式')

    const undefinedText = mountCard(undefined).text()
    expect(undefinedText).not.toContain('总结模式')
  })
})
