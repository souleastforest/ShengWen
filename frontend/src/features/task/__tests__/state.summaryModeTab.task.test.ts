/**
 * P7 迁移：useTaskViewModel.summaryModeTab.adversarial.test.ts 的 task 域部分
 * （确认窗口/版本计数/无泄漏/reSummarize 模式语义；Sidebar 层与 TaskMetaCard
 * 渲染断言随组件层留在 src/__tests__/summaryModeTab.adversarial.test.ts）。
 *
 * D1 执行：summaryMode 归 upload/state；reTranscribe/reSummarize 显式 mode 参数，
 * 测试中 task.reTranscribe(taskId, upload.summaryMode.value) 显式传参。
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { __resetTaskContentCaches, useTaskState } from '../state'
import { useWebSocket } from '../../../shared/ws'
import { useUploadState } from '../../upload/state'
import type { TaskState } from '../state'
import type { Task } from '../../../types'

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

// 装配模拟：task 域 + upload 域（D1：summaryMode 显式传参）+ ws 接线
const mountStates = () => {
  let task!: TaskState
  let upload!: ReturnType<typeof useUploadState>
  let ws!: ReturnType<typeof useWebSocket>
  const TestComponent = defineComponent({
    setup() {
      task = useTaskState()
      upload = useUploadState()
      ws = useWebSocket()
      task.syncWithWs(ws)
      upload.syncWithWs(ws)
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  task.fetchTasks()
  ws.connect()
  return { task, upload, ws, wrapper }
}

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

describe('对抗性：模式语义无泄漏与确认窗口（需求 2c）— task 域', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setupWsAndEnv)

  it('[S6] 无泄漏：updateTaskTopic（PATCH）payload 不含 summary_mode；reDownloadAudio 不含', async () => {
    const { task, upload, wrapper } = mountStates()
    installDefaultAxios()

    upload.summaryMode.value = 'agent'
    await task.updateTaskTopic('task-a', '新主题')
    expect(mockedAxios.patch).toHaveBeenCalledWith(
      '/tasks/task-a',
      expect.objectContaining({ topic: '新主题' }),
    )
    const patchPayload = mockedAxios.patch.mock.calls[0]![1] as Record<string, unknown>
    expect('summary_mode' in patchPayload).toBe(false)

    mockedAxios.post.mockClear()
    await task.reDownloadAudio('task-a')
    const [, reDownloadPayload] = mockedAxios.post.mock.calls[0]!
    expect(reDownloadPayload).toBeUndefined() // re-download 无请求体

    wrapper.unmount()
  })

  it('[S6] reSummarize 语义内受模式影响：none 时 payload summary_mode="none"（后端 auto 兜底），standard 时 standard', async () => {
    const { task, upload, wrapper } = mountStates()
    installDefaultAxios()

    upload.summaryMode.value = 'none'
    await task.reSummarize('task-a', upload.summaryMode.value)
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-summarize',
      expect.objectContaining({ summary_mode: 'none' }),
    )

    upload.summaryMode.value = 'standard'
    mockedAxios.post.mockClear()
    await task.reSummarize('task-a', upload.summaryMode.value)
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-summarize',
      expect.objectContaining({ summary_mode: 'standard' }),
    )

    wrapper.unmount()
  })

  it('[S7] reSummarize 显式模式参数优先：传 agent/standard 时 payload 用显式值，覆盖全局；不传沿用装配层传入值（向后兼容）', async () => {
    const { task, upload, wrapper } = mountStates()
    installDefaultAxios()

    // 显式模式优先于全局（即使全局是 none）
    upload.summaryMode.value = 'none'
    await task.reSummarize('task-a', 'agent')
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-summarize',
      expect.objectContaining({ summary_mode: 'agent' }),
    )

    mockedAxios.post.mockClear()
    await task.reSummarize('task-a', 'standard')
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-summarize',
      expect.objectContaining({ summary_mode: 'standard' }),
    )

    // 不传模式：装配层传 upload.summaryMode.value（S6 语义不回归）
    mockedAxios.post.mockClear()
    await task.reSummarize('task-a', upload.summaryMode.value)
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-summarize',
      expect.objectContaining({ summary_mode: 'none' }),
    )

    wrapper.unmount()
  })

  it.each([
    ['undefined（轻量列表项）', undefined],
    ['null', null],
    ['空串', ''],
  ] as const)('[B1] transcript=%s 视为未加载：原文 tab 触发按需加载且仅一次', async (_label, transcript) => {
    const { task, wrapper } = mountStates()
    const taskA = { ...makeTask('task-a'), transcript } as unknown as Task

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: null },
    })

    task.activeTab.value = 'transcript'
    task.selectTask(taskA)
    await flushPromises()

    // 三种"无内容"形态都必须触发一次加载
    expect(fullRequests()).toHaveLength(1)
    // 响应仍为空：依赖（activeTab, id）不变，不无限重发
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    wrapper.unmount()
  })

  it('[B2] 同 id 并发 fetchTaskFullContent 去重：tab watch 与 copyContent 并发只发 1 次 GET', async () => {
    const { task, wrapper } = mountStates()
    const taskA = makeTask('task-a')

    const fullA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': fullA,
    })

    task.activeTab.value = 'transcript'
    task.selectTask(taskA)
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    // 并发第二次调用（如 copyContent 路径）：复用同一 Promise，不新增 GET
    const p2 = task.fetchTaskFullContent('task-a')
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    fullA.resolve({ data: { ...taskA, transcript: '内容' } })
    await p2
    await flushPromises()

    // pending 已清：再次调用重新发请求
    await task.fetchTaskFullContent('task-a')
    expect(fullRequests()).toHaveLength(2)

    wrapper.unmount()
  })

  it('[B3] 过期在途完整响应（summary 方向）：新总结不被旧总结覆盖；status/progress 照常合并', async () => {
    const { task, wrapper } = mountStates()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    const fullA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: null },
      '/tasks/task-a?include_content=true': fullA,
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null, summary: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录', summary: 'B的总结' },
    })

    task.activeTab.value = 'transcript'
    task.selectTask(taskA)
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    // 期间收到携带新内容的 COMPLETED 广播（版本递增）
    emitWsTaskUpdate({ ...taskA, status: 'COMPLETED', transcript: '新转录', summary: '新总结' })
    await flushPromises()
    expect(task.selectedTask.value?.summary).toBe('新总结')

    // 在途旧完整响应此刻返回：transcript/summary 不得被旧值覆盖
    fullA.resolve({
      data: { ...taskA, status: 'FAILED', progress: 42, transcript: '旧转录', summary: '旧总结' },
    })
    await flushPromises()

    expect(task.selectedTask.value?.transcript).toBe('新转录')
    expect(task.selectedTask.value?.summary).toBe('新总结')
    // 需求 b：status/progress 照常合并
    expect(task.selectedTask.value?.status).toBe('FAILED')
    expect(task.selectedTask.value?.progress).toBe(42)

    // A→B→A：缓存不得回退旧总结
    task.selectTask(taskB)
    await flushPromises()
    task.selectTask(taskA)
    await flushPromises()
    expect(task.selectedTask.value?.summary).toBe('新总结')
    expect(task.selectedTask.value?.transcript).toBe('新转录')

    wrapper.unmount()
  })
})

describe('对抗性：reTranscribe 确认窗口（需求 2c，D1 显式 mode）— task 域', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setupWsAndEnv)

  it('[B4] 攻击窗口：轻量详情在途（summary 尚未加载）时 reTranscribe 仍须弹 confirm，取消则中止提交', async () => {
    const { task, upload, wrapper } = mountStates()
    const taskA = makeTask('task-a')

    // 详情接口（include_content=false）慢：selectedTask 停留在轻量对象（无 summary）
    const lightA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': lightA,
    })

    vi.stubGlobal('confirm', vi.fn())
    const confirmSpy = vi.mocked(confirm).mockReturnValue(false)

    upload.summaryMode.value = 'none'
    task.selectTask(taskA) // 同步阶段 selectedTask = 轻量对象
    // 不等待轻量详情返回，立即 re-transcribe（模拟慢网络下用户的即时操作）
    await task.reTranscribe('task-a', upload.summaryMode.value)

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
    const { task, upload, wrapper } = mountStates()
    installDefaultAxios()

    vi.stubGlobal('confirm', vi.fn())
    const confirmSpy = vi.mocked(confirm).mockReturnValue(false)

    upload.summaryMode.value = 'none'
    task.selectedTask.value = { ...makeTask('task-a'), summary: '已有总结' }
    await task.reTranscribe('task-a', upload.summaryMode.value)
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('[B5] 防御路径：reTranscribe 传入非选中任务 id 时 target 取列表轻量对象（summary 被后端剥离）→ 模式信号兜底弹 confirm，取消则中止', async () => {
    const { task, upload, wrapper } = mountStates()
    installDefaultAxios()

    vi.stubGlobal('confirm', vi.fn())
    const confirmSpy = vi.mocked(confirm).mockReturnValue(false)

    upload.summaryMode.value = 'none'
    task.tasks.value = [{ ...makeTask('task-a') }] // 列表条目：transcript/summary 被 list_tasks 剥离
    task.selectedTask.value = makeTask('task-b')
    await task.reTranscribe('task-a', upload.summaryMode.value)

    // 列表条目 summary 缺失，但 summary_mode='agent' 模式信号兜底：必须弹确认
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})
