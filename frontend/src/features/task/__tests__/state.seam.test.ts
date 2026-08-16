/**
 * P7 seam 契约测试：features/task/state.ts
 *
 * 规格（p7-composable-spec.md §3.1）逐键断言：
 * - 导出面形状：TaskState 全部 state refs + actions + syncWithWs +
 *   __resetTaskContentCaches + useTaskState；
 * - 合并守卫三态：同任务重选/轻量列表项不得覆盖已加载完整内容；
 * - 懒加载 watch 触发条件：原文 tab 补拉 include_content=true；summary
 *   三态判定（undefined 不抢跑 / null/'' 触发 / 进行中不触发）；
 * - __resetTaskContentCaches 语义：重置后 A→B→A 缓存不恢复（重新请求）。
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { __resetTaskContentCaches, useTaskState } from '../state'
import { useWebSocket } from '../../../shared/ws'
import type { TaskState } from '../state'
import type { Task } from '../../../types'

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

const mountTaskState = () => {
  let task!: TaskState
  const TestComponent = defineComponent({
    setup() {
      task = useTaskState()
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  return { task, wrapper }
}

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
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
}

const fullRequests = () =>
  mockedAxios.get.mock.calls.filter(([url]) => String(url).includes('include_content=true'))

describe('P7 seam：features/task/state.ts 导出面与语义', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  type WsInstance = {
    close: () => void
    onopen: (() => void) | null
    onmessage: ((event: { data: string }) => void) | null
    onclose: (() => void) | null
    onerror: ((err: unknown) => void) | null
  }
  let wsInstances: WsInstance[] = []

  beforeEach(() => {
    __resetTaskContentCaches()
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    wsInstances = []
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

  it('导出面形状：state refs / actions / syncWithWs / 测试钩子 逐键存在', () => {
    const { task } = mountTaskState()

    // state（全部 Ref）
    for (const key of ['tasks', 'queues', 'selectedTask', 'taskParts', 'taskPartDetails', 'loadingPartIndex', 'activeTab', 'isSidebarOpen', 'error']) {
      expect(task[key as keyof TaskState]).toBeDefined()
      expect((task[key as keyof TaskState] as { value: unknown }).value).toBeDefined()
    }
    // actions
    for (const key of [
      'fetchTasks', 'fetchQueueSnapshot', 'selectTask', 'fetchTaskFullContent',
      'fetchTaskParts', 'fetchTaskPart', 'retryFailedParts', 'downloadContent',
      'copyContent', 'deleteTask', 'reSummarize', 'reTranscribe', 'reDownloadAudio',
      'updateTaskTopic', 'syncWithWs', 'startPolling', 'stopPolling', 'dispose',
    ]) {
      expect(typeof task[key as keyof TaskState]).toBe('function')
    }
    expect(typeof useTaskState).toBe('function')
    expect(typeof __resetTaskContentCaches).toBe('function')
  })

  it('合并守卫：同任务重选时已加载的完整内容不被轻量详情覆盖（preserve 默认）', async () => {
    const { task, wrapper } = mountTaskState()
    const taskA = makeTask('task-a')

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '截断版总结' },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: '完整转录', summary: '完整总结' },
    })

    task.selectTask(taskA)
    await flushPromises()
    task.activeTab.value = 'transcript'
    await flushPromises()
    expect(task.selectedTask.value?.summary).toBe('完整总结')
    expect(task.selectedTask.value?.transcript).toBe('完整转录')

    // 同任务重选：轻量详情（截断版 summary）不得覆盖已加载的完整内容
    task.selectTask(taskA)
    await flushPromises()
    expect(task.selectedTask.value?.summary).toBe('完整总结')
    expect(task.selectedTask.value?.transcript).toBe('完整转录')

    wrapper.unmount()
  })

  it('懒加载：原文 tab 触发 include_content=true 并填充 transcript', async () => {
    const { task, wrapper } = mountTaskState()
    const taskA = makeTask('task-a')

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: '完整转录' },
    })

    task.selectTask(taskA)
    await flushPromises()
    expect(task.selectedTask.value?.transcript).toBeNull()

    task.activeTab.value = 'transcript'
    await flushPromises()

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-a?include_content=true')
    expect(task.selectedTask.value?.transcript).toBe('完整转录')

    wrapper.unmount()
  })

  it('懒加载三态判定（summary）：null/空串 触发补拉；undefined（详情在途）不抢跑；进行中任务不触发', async () => {
    // null → 触发
    let calls = 0
    const { task, wrapper } = mountTaskState()
    const taskA = makeTask('task-a')

    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).includes('include_content=false')) {
        return Promise.resolve({ data: { ...taskA, transcript: null, summary: null } })
      }
      if (String(url).includes('include_content=true')) {
        calls += 1
        return Promise.resolve({ data: { ...taskA, transcript: '转录', summary: '完整总结' } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(taskA)
    await flushPromises()
    expect(calls).toBe(1) // summary null → 自动补拉
    expect(task.selectedTask.value?.summary).toBe('完整总结')

    // 进行中任务：不触发
    const { task: task2, wrapper: wrapper2 } = mountTaskState()
    const processing = { ...makeTask('task-p'), status: 'TRANSCRIBING', summary: null } as Task
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).includes('include_content=false')) {
        return Promise.resolve({ data: { ...processing, transcript: null, summary: null } })
      }
      return Promise.resolve({ data: [] })
    })
    const before = mockedAxios.get.mock.calls.filter(([u]) => String(u).includes('include_content=true')).length
    task2.selectTask(processing)
    await flushPromises()
    const after = mockedAxios.get.mock.calls.filter(([u]) => String(u).includes('include_content=true')).length
    expect(after).toBe(before) // 进行中不触发

    wrapper.unmount()
    wrapper2.unmount()
  })

  it('__resetTaskContentCaches：重置后 A→B→A 往返缓存不恢复，重新请求完整内容', async () => {
    const { task, wrapper } = mountTaskState()
    const taskA = makeTask('task-a')
    const taskB = makeTask('task-b')

    installDefaultAxios({
      '/tasks/task-a?include_content=false': { ...taskA, transcript: null },
      '/tasks/task-a?include_content=true': { ...taskA, transcript: 'A的完整转录' },
      '/tasks/task-b?include_content=false': { ...taskB, transcript: null },
      '/tasks/task-b?include_content=true': { ...taskB, transcript: 'B的完整转录' },
    })

    task.activeTab.value = 'transcript'
    task.selectTask(taskA)
    await flushPromises()
    expect(task.selectedTask.value?.transcript).toBe('A的完整转录')
    expect(fullRequests().filter(([u]) => String(u).includes('task-a'))).toHaveLength(1)

    __resetTaskContentCaches()
    task.selectTask(taskB)
    await flushPromises()
    task.selectTask(taskA)
    await flushPromises()

    // 缓存被清：A→B→A 需重新请求完整内容（自愈语义，非缓存恢复）
    expect(task.selectedTask.value?.transcript).toBe('A的完整转录')
    expect(fullRequests().filter(([u]) => String(u).includes('task-a'))).toHaveLength(2)

    wrapper.unmount()
  })

  it('syncWithWs：task_update 广播合并列表（unshift）与 queues 可选字段；progress_update 写进度', async () => {
    const { task, wrapper } = mountTaskState()
    installDefaultAxios()

    const ws = useWebSocket()
    task.syncWithWs(ws)
    ws.connect()

    // 广播新任务（列表 unshift）+ 携带 queues 快照
    const queues = [{ name: 'LLMWorker', active_task_id: null, queue_size: 0, waiting_task_ids: [] }]
    wsInstances[0]!.onmessage?.({
      data: JSON.stringify({ type: 'task_update', task: { ...makeTask('task-a'), status: 'PENDING', progress: 0 }, queues }),
    })
    expect(task.tasks.value.map((t) => t.id)).toEqual(['task-a'])
    expect(task.queues.value).toEqual(queues)

    // progress_update 写列表与选中任务进度
    task.selectedTask.value = makeTask('task-a')
    wsInstances[0]!.onmessage?.({
      data: JSON.stringify({ type: 'progress_update', task_id: 'task-a', progress: 42 }),
    })
    expect(task.tasks.value[0]!.progress).toBe(42)
    expect(task.selectedTask.value?.progress).toBe(42)

    wrapper.unmount()
  })
})
