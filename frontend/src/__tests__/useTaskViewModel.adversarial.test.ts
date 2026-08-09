/**
 * 对抗性测试：转录切换修复（watch 依赖任务 id + per-task 缓存 + 按需加载）
 *
 * 从需求出发验证，不信任实现自测：
 * 1. 核心回归：A（已加载转录）→ 切 B（原文 tab 保持激活）→ B 必须加载并显示自己的转录；A→B→A 往返恢复。
 * 2. 缓存正确性：A 的转录被 WS task_update 更新（re-transcribe 链路）后缓存不得返回旧内容；
 *    缓存命中不得把 A 的内容错误并给 B。
 * 3. 竞态：include_content=true/false 乱序、selectTask(A) 后快速 selectTask(B)、
 *    copyContent await 期间切任务。
 * 4. 边界：'' 空串转录按需加载一次即止（不无限循环）；进行中任务切换 activeTab 重置；FAILED/PARTIAL 正常加载。
 * 5. 不回归：summary tab 下切任务不请求 include_content=true。
 * 6. 残留缺陷修复：re-transcribe 重置 '' 后切走再切回自愈重载；过期在途完整响应
 *    不得覆盖 selectedTask 新内容；reTranscribe 清空总结前需确认。
 *
 * 每个用例失败 = 实现缺陷（用例均为需求允许的行为）。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { __resetTaskContentCaches, useTaskViewModel } from '../composables/useTaskViewModel'
import type { Task } from '../types'

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
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

// WS 实例捕获：connectWebSocket 在 onMounted 时 new WebSocket
const wsInstances: WsInstance[] = []

const emitWsTaskUpdate = (task: Task) => {
  const ws = wsInstances[wsInstances.length - 1]
  if (!ws) return
  ws.onmessage?.({ data: JSON.stringify({ type: 'task_update', task }) })
}

// 可延迟 resolve 的 GET 应答
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
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
}

const fullRequests = () =>
  mockedAxios.get.mock.calls.filter(([url]) => String(url).includes('include_content=true'))

describe('对抗性：转录切换修复（需求 A）', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
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
  })

  it('[A1] 核心回归：A 已加载 → 切 B（原文 tab 保持激活）→ B 加载并显示自己的转录，A→B→A 往返恢复', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: 'A的完整转录' },
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    viewModel.selectTask(taskA)
    viewModel.activeTab.value = 'transcript'
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('A的完整转录')

    // 原文 tab 保持激活时切到 B：必须触发 B 的 include_content=true 且显示 B 自己的转录
    viewModel.selectTask(taskB)
    await flushPromises()
    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-b?include_content=true')
    expect(viewModel.selectedTask.value?.id).toBe('task-b')
    expect(viewModel.selectedTask.value?.transcript).toBe('B的完整转录')
    expect(viewModel.selectedTask.value?.transcript).not.toContain('A的')

    // A→B→A 往返：A 的转录恢复，且不重新请求完整内容（缓存命中）
    viewModel.selectTask(taskA)
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('A的完整转录')
    const fullCalls = fullRequests().map(([url]) => String(url))
    expect(fullCalls.filter((u) => u.includes('task-a')).length).toBe(1)

    wrapper.unmount()
  })

  it('[A2] WS 仅广播最终 COMPLETED（新转录）时缓存不得返回旧内容', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: '旧转录内容' },
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    viewModel.selectTask(taskA)
    viewModel.activeTab.value = 'transcript'
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('旧转录内容')

    // 任务内容更新（如 re-transcribe 完成 / 内容被替换），仅收到最终 COMPLETED 广播
    // （中间 PENDING/TRANSCRIBING 广播可能因 WS 断开等原因缺失）
    emitWsTaskUpdate({ ...taskA, status: 'COMPLETED', transcript: '新转录内容' })
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('新转录内容')

    // A→B→A 后不得回退到缓存的旧内容
    viewModel.selectTask(taskB)
    await flushPromises()
    viewModel.selectTask(taskA)
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('新转录内容')

    wrapper.unmount()
  })

  it('[A2] 竞态：re-transcribe 广播清理缓存后，在途的旧完整响应不得重新污染缓存（A→B→A 显示新内容）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    // A 的 include_content=true 为慢请求（在途）
    const fullA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': fullA,
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA)
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    // re-transcribe：PENDING / TRANSCRIBING 广播清理缓存
    emitWsTaskUpdate({ ...taskA, status: 'PENDING', transcript: '' })
    emitWsTaskUpdate({ ...taskA, status: 'TRANSCRIBING', transcript: '' })
    await flushPromises()

    // 旧完整响应此刻才返回 → 不得把旧内容写回缓存（此时任务内容已确定变化）
    fullA.resolve({ data: { ...taskA, transcript: '旧转录内容' } })
    await flushPromises()

    // 完成广播携带新内容
    emitWsTaskUpdate({ ...taskA, status: 'COMPLETED', transcript: '新转录内容' })
    await flushPromises()

    viewModel.selectTask(taskB)
    await flushPromises()
    viewModel.selectTask(taskA)
    await flushPromises()

    expect(viewModel.selectedTask.value?.transcript).toBe('新转录内容')

    wrapper.unmount()
  })

  it('[A2] 缓存命中不得把 A 的内容错误并给 B（各自显示自己的转录）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: 'AAA' },
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'BBB' },
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA)
    await flushPromises()
    viewModel.selectTask(taskB)
    await flushPromises()
    viewModel.selectTask(taskA)
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('AAA')
    viewModel.selectTask(taskB)
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('BBB')

    wrapper.unmount()
  })

  it('[A3] 竞态：include_content=true 先返回，轻量 include_content=false 后到不得覆盖完整转录', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')

    const lightA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': lightA,
      '/tasks/task-a?include_content=true': { ...taskA, transcript: '完整转录原文' },
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA)
    await flushPromises()
    // 完整内容已加载
    expect(viewModel.selectedTask.value?.transcript).toBe('完整转录原文')

    // 轻量响应此时才到（transcript 被剥离为 null）
    lightA.resolve({ data: { ...taskA, transcript: null, summary: '截断版总结' } })
    await flushPromises()

    expect(viewModel.selectedTask.value?.transcript).toBe('完整转录原文')

    wrapper.unmount()
  })

  it('[A3] 竞态：selectTask(A) 后快速 selectTask(B)，A 的慢轻量响应不得污染 B', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    const lightA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': lightA,
      '/tasks/task-a?include_content=true': { ...taskA, transcript: 'A的完整转录' },
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA) // A 的轻量响应慢
    viewModel.selectTask(taskB) // 立即切到 B
    await flushPromises()
    expect(viewModel.selectedTask.value?.id).toBe('task-b')

    // A 的慢响应此刻才到：不得覆盖当前选中的 B
    lightA.resolve({ data: { ...taskA, transcript: null, summary: 'A的截断总结' } })
    await flushPromises()

    expect(viewModel.selectedTask.value?.id).toBe('task-b')
    expect(viewModel.selectedTask.value?.transcript).toBe('B的完整转录')

    wrapper.unmount()
  })

  it('[A3] 竞态：copyContent await 期间切任务 → 不复制、不弹错误 toast', async () => {
    const writeTextMock = vi.mocked(navigator.clipboard.writeText)
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    const fullA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': fullA,
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    viewModel.selectTask(taskA)
    await flushPromises()

    const copyPromise = viewModel.copyContent('transcript') // A 的完整内容在途
    viewModel.selectTask(taskB) // 等待期间切到 B
    await flushPromises()

    fullA.resolve({ data: { ...taskA, transcript: 'A的完整转录' } })
    const result = await copyPromise
    await flushPromises()

    expect(result).toBe(false)
    expect(writeTextMock).not.toHaveBeenCalled()
    expect(viewModel.error.value).toBeNull() // 不得弹错误 toast
    expect(viewModel.selectedTask.value?.id).toBe('task-b')

    wrapper.unmount()
  })

  it('[A3] 竞态：downloadContent await 期间切任务 → 不触发下载（await 后任务 id 复查）', async () => {
    const createObjectURLMock = vi.fn().mockReturnValue('blob:mock')
    const revokeObjectURLMock = vi.fn()
    class MockURL {}
    Object.assign(MockURL, {
      createObjectURL: createObjectURLMock,
      revokeObjectURL: revokeObjectURLMock,
    })
    vi.stubGlobal('URL', MockURL as unknown as typeof URL)

    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    const fullA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': fullA,
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    viewModel.selectTask(taskA)
    await flushPromises()

    const downloadPromise = viewModel.downloadContent('transcript') // A 的完整内容在途
    viewModel.selectTask(taskB) // 等待期间切到 B
    await flushPromises()

    fullA.resolve({ data: { ...taskA, transcript: 'A的完整转录' } })
    await downloadPromise
    await flushPromises()

    // 已切到 B：不得创建/点击下载（A 的内容不得在 B 下被下载）
    expect(createObjectURLMock).not.toHaveBeenCalled()
    expect(revokeObjectURLMock).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('[A3] 竞态：copyContent await 期间切到 B 再切回 A → 复制 A 自己的内容（非 B）', async () => {
    const writeTextMock = vi.mocked(navigator.clipboard.writeText)
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    const fullA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': fullA,
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    viewModel.selectTask(taskA)
    await flushPromises()

    const copyPromise = viewModel.copyContent('transcript')
    viewModel.selectTask(taskB)
    await flushPromises()
    viewModel.selectTask(taskA) // 又切回 A
    await flushPromises()

    fullA.resolve({ data: { ...taskA, transcript: 'A的完整转录' } })
    const result = await copyPromise
    await flushPromises()

    expect(result).toBe(true)
    expect(writeTextMock).toHaveBeenCalledWith('A的完整转录')

    wrapper.unmount()
  })

  it('[A4] 边界：空串转录按需加载一次即止——重选同一任务不重发（依赖不变），不无限循环', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = { ...makeTask('task-a'), transcript: '' }

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: '' },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: '' },
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA)
    await flushPromises()

    // '' 视为"未加载"：原文 tab 触发一次按需加载（不再是 0 次）
    expect(fullRequests()).toHaveLength(1)

    viewModel.selectTask(taskA) // 重选同一任务：依赖（activeTab, id）不变，不重发
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    // 请求返回仍为空串：不会因响应写入触发新的 watch 循环
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    wrapper.unmount()
  })

  it.each(['PENDING', 'DOWNLOADING', 'TRANSCRIBING', 'SUMMARIZING'] as const)(
    '[A4] 边界：进行中任务（%s）切换时 activeTab 重置为 summary 且不触发原文加载',
    async (status) => {
      const { viewModel, wrapper } = mountViewModel()
      const task = { ...makeTask('task-x'), status, transcript: null } as unknown as Task

      installDefaultAxios({
        [`/tasks/task-x?include_content=false`]: { ...task, transcript: null },
      })

      viewModel.activeTab.value = 'transcript'
      viewModel.selectTask(task)
      await flushPromises()

      expect(viewModel.activeTab.value).toBe('summary')
      expect(fullRequests()).toHaveLength(0)

      wrapper.unmount()
    },
  )

  it.each(['FAILED', 'PARTIAL'] as const)('[A4] 边界：%s 任务同 COMPLETED 一样正常加载原文', async (status) => {
    const { viewModel, wrapper } = mountViewModel()
    const task = { ...makeTask('task-x'), status, transcript: null } as unknown as Task

    installDefaultAxios({
      '/tasks/task-x?include_content=false': { ...task, transcript: null },
      '/tasks/task-x?include_content=true': { ...task, transcript: '失败任务的转录' },
    })

    viewModel.selectTask(task)
    viewModel.activeTab.value = 'transcript'
    await flushPromises()

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-x?include_content=true')
    expect(viewModel.selectedTask.value?.transcript).toBe('失败任务的转录')

    wrapper.unmount()
  })

  it('[A5] 不回归：summary tab 下切任务不请求 include_content=true（保持轻量）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: 'A的截断总结' },
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null, summary: 'B的截断总结' },
    })

    viewModel.selectTask(taskA)
    await flushPromises()
    viewModel.selectTask(taskB)
    await flushPromises()
    viewModel.activeTab.value = 'transcript'
    viewModel.activeTab.value = 'summary'
    await flushPromises()
    viewModel.selectTask(taskA)
    await flushPromises()

    expect(fullRequests()).toHaveLength(0)

    wrapper.unmount()
  })

  it('[A3] 竞态：B 的慢完整响应在切回 A 后才返回，不得覆盖 A 的展示', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    const fullB = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: 'A的完整转录' },
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': fullB,
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA)
    await flushPromises()

    viewModel.selectTask(taskB) // B 的完整内容在途
    await flushPromises()
    viewModel.selectTask(taskA) // 切回 A（缓存恢复）
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('A的完整转录')

    fullB.resolve({ data: { ...taskB, transcript: 'B的完整转录' } })
    await flushPromises()

    expect(viewModel.selectedTask.value?.id).toBe('task-a')
    expect(viewModel.selectedTask.value?.transcript).toBe('A的完整转录')

    wrapper.unmount()
  })
})

describe('对抗性：re-transcribe 重置自愈 + 过期在途响应（残留缺陷修复）', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
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
  })

  it('[B6] re-transcribe 重置（transcript=""）写入 selectedTask 后，切走再切回会重新请求 include_content=true 自愈', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: '初始转录内容' },
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA)
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('初始转录内容')

    // WS 广播 re-transcribe 重置：transcript 清空
    emitWsTaskUpdate({ ...taskA, status: 'PENDING', transcript: '' })
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('')

    // 重新转录已完成：服务端已有新的完整内容
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: '重新转录后的新内容' },
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    // 切走再切回：原文 tab 重新触发按需加载 → 重新请求 include_content=true（自愈）
    viewModel.selectTask(taskB)
    await flushPromises()
    viewModel.selectTask(taskA)
    await flushPromises()

    const fullA = fullRequests().filter(([url]) => String(url).includes('task-a'))
    expect(fullA).toHaveLength(2)
    expect(viewModel.selectedTask.value?.transcript).toBe('重新转录后的新内容')

    wrapper.unmount()
  })

  it('[B7] 过期在途响应：fullA 晚于 COMPLETED 广播解析时，selectedTask 的 transcript 不被旧内容覆盖', async () => {
    const { viewModel, wrapper } = mountViewModel()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    const fullA = deferred()
    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': fullA,
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    viewModel.activeTab.value = 'transcript'
    viewModel.selectTask(taskA)
    await flushPromises()
    expect(fullRequests()).toHaveLength(1)

    // 期间收到携带新内容的 COMPLETED 广播（递增内容版本）
    emitWsTaskUpdate({ ...taskA, status: 'COMPLETED', transcript: '新转录内容' })
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('新转录内容')

    // 在途的旧完整响应此刻才返回：不得覆盖 selectedTask 的 transcript
    fullA.resolve({ data: { ...taskA, transcript: '旧转录内容' } })
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('新转录内容')

    // A→B→A：缓存同样不得回退旧内容
    viewModel.selectTask(taskB)
    await flushPromises()
    viewModel.selectTask(taskA)
    await flushPromises()
    expect(viewModel.selectedTask.value?.transcript).toBe('新转录内容')

    wrapper.unmount()
  })
})

describe('对抗性：reTranscribe 清空总结确认（残留缺陷修复）', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
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
  })

  it('[B8] summaryMode=none 且目标任务已有总结：先 confirm；取消不提交，确认才提交', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    vi.stubGlobal('confirm', vi.fn())
    const confirmSpy = vi.mocked(confirm).mockReturnValue(false)
    viewModel.summaryMode.value = 'none'
    viewModel.selectedTask.value = { ...makeTask('task-a'), summary: '已有总结' }

    // 取消：不发请求
    await viewModel.reTranscribe('task-a')
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).not.toHaveBeenCalled()

    // 确认：正常提交，payload 携带 summary_mode='none'
    confirmSpy.mockReturnValue(true)
    await viewModel.reTranscribe('task-a')
    expect(confirmSpy).toHaveBeenCalledTimes(2)
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-transcribe',
      expect.objectContaining({ summary_mode: 'none' }),
    )

    wrapper.unmount()
  })

  it('[B8] 无总结内容或非 none 模式：不弹确认直接提交', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    vi.stubGlobal('confirm', vi.fn())
    const confirmSpy = vi.mocked(confirm).mockReturnValue(false)
    // none 任务本身无总结（summary 空 + summary_mode='none'，无 content/模式信号）→ 不弹
    viewModel.selectedTask.value = { ...makeTask('task-a'), summary: '', summary_mode: 'none' }
    viewModel.summaryMode.value = 'none'
    await viewModel.reTranscribe('task-a')
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-transcribe',
      expect.objectContaining({ summary_mode: 'none' }),
    )

    mockedAxios.post.mockClear()
    viewModel.selectedTask.value = { ...makeTask('task-a'), summary: '已有总结' }
    viewModel.summaryMode.value = 'standard'
    await viewModel.reTranscribe('task-a')
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/task-a/re-transcribe',
      expect.objectContaining({ summary_mode: 'standard' }),
    )

    wrapper.unmount()
  })
})

describe('对抗性：仅转录模式（需求 B）前端 payload', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
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
  })

  it('[B5] 开关置 none 后 URL 提交 payload 携带 summary_mode="none"', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    viewModel.summaryMode.value = 'none'
    viewModel.videoUrl.value = 'https://example.com/video.mp4'
    await viewModel.submitTask()

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ video_url: 'https://example.com/video.mp4', summary_mode: 'none' }),
      expect.anything(),
    )

    wrapper.unmount()
  })

  it('[B5] 开关置 none 后本地路径提交 payload 携带 summary_mode="none"', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    viewModel.summaryMode.value = 'none'
    viewModel.localFilePath.value = '/data/video.mp4'
    await viewModel.submitTask()

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/upload/local-path',
      expect.objectContaining({ file_path: '/data/video.mp4', summary_mode: 'none' }),
      expect.anything(),
    )

    wrapper.unmount()
  })

  it('[B5] 开关置 none 后文件上传 FormData 携带 summary_mode="none"', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    viewModel.summaryMode.value = 'none'
    viewModel.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
    await viewModel.submitTask()

    expect(mockedAxios.post).toHaveBeenCalled()
    const [, formData] = mockedAxios.post.mock.calls.find(([url]) => url === '/upload')!
    expect(formData).toBeInstanceOf(FormData)
    expect((formData as FormData).get('summary_mode')).toBe('none')

    wrapper.unmount()
  })

  it('[B5] 开关置 none 后分P提交 payload 携带 summary_mode="none"', async () => {
    const { viewModel, wrapper } = mountViewModel()
    installDefaultAxios()

    viewModel.summaryMode.value = 'none'
    await viewModel.submitTaskWithParts('https://www.bilibili.com/video/BV1xx', {
      mode: 'merge',
      indices: [0, 1],
    })

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none', bilibili_parts: { mode: 'merge', indices: [0, 1] } }),
      expect.anything(),
    )

    wrapper.unmount()
  })
})
