/**
 * P1 防线加固（安全 + 一致性守卫）——composable 层（useTaskViewModel）
 *
 * 覆盖：
 * 1. [P1-2] 内容合并守卫统一：WS 重连全量对账（onopen → fetchTasks 列表合并 +
 *    selectTask(currentTask)）后，已加载的完整 summary/transcript 不得被轻量列表
 *    项清空或回退为截断版 _summary_overview；
 * 2. [P1-2] WS task_update 合并保持"广播内容权威"：re-transcribe 的 '' 重置必须
 *    传播到 selectedTask（守卫抽取不得弱化该语义）；
 * 3. [P1-4] summary tab 按需加载：摘要未加载（null / ''）时自动请求
 *    include_content=true，修复总结 tab 长期显示截断版的问题；
 * 4. [P1-4] 不回归：summary 已有内容（截断预览）或任务进行中时不得重复请求；
 * 5. [P1-5] quality 死绑定清理：提交 payload 使用显式常量 'audio_only'，
 *    composable 不再导出 quality 状态。
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

const wsInstances: WsInstance[] = []

const emitWsTaskUpdate = (task: Task) => {
  const ws = wsInstances[wsInstances.length - 1]
  if (!ws) return
  ws.onmessage?.({ data: JSON.stringify({ type: 'task_update', task }) })
}

const triggerWsOnopen = () => {
  const ws = wsInstances[wsInstances.length - 1]
  expect(ws).toBeTruthy()
  ws!.onopen?.()
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

describe('P1 防线加固：useTaskViewModel', () => {
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

  describe('[P1-2] 内容合并守卫统一（WS 重连对账）', () => {
    it('WS 重连全量对账后，已加载的完整 summary/transcript 被保留（不被轻量列表项清空/截断）', async () => {
      const { viewModel, wrapper } = mountViewModel()
      const taskA = makeTask('task-a')

      // 首次选择任务并加载完整内容（原文 tab 触发 include_content=true）
      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '截断版总结' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: '完整转录', summary: '完整总结' },
      })
      viewModel.selectTask(taskA)
      await flushPromises()
      viewModel.activeTab.value = 'transcript'
      await flushPromises()
      expect(viewModel.selectedTask.value?.summary).toBe('完整总结')
      expect(viewModel.selectedTask.value?.transcript).toBe('完整转录')

      // 断线重连：onopen 触发全量对账——fetchTasks 列表 + selectTask(currentTask)。
      // 轻量 payload（列表/详情）携带截断版 _summary_overview 时，合并守卫必须
      // 保留已加载的完整内容，不得回退为截断版。
      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '截断版总结' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: '完整转录', summary: '完整总结' },
        // 列表接口轻量项（防御目标：即使列表携带截断版 summary 字段也不得覆盖完整版）
        '/tasks/': [{ ...taskA, summary: '截断版总结' }],
      })
      triggerWsOnopen()
      await flushPromises()

      // 回归断言：重连对账不得把完整内容回退为截断版 _summary_overview 或清空
      expect(viewModel.selectedTask.value?.summary).toBe('完整总结')
      expect(viewModel.selectedTask.value?.transcript).toBe('完整转录')

      wrapper.unmount()
    })

    it('重连对账（per-task 缓存失效时）：轻量列表合并不得覆盖已加载的完整内容（守卫兜底）', async () => {
      const { viewModel, wrapper } = mountViewModel()
      const taskA = makeTask('task-a')

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '截断版总结' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: '完整转录', summary: '完整总结' },
      })
      viewModel.selectTask(taskA)
      await flushPromises()
      viewModel.activeTab.value = 'transcript'
      await flushPromises()
      expect(viewModel.selectedTask.value?.summary).toBe('完整总结')

      // per-task 缓存失效（如处理中广播清缓存后断线重连，缓存未被重新填充）
      __resetTaskContentCaches()

      // 重连对账：fetchTasks 列表项携带截断版 summary（防御目标负载形态）
      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '截断版总结' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: '完整转录', summary: '完整总结' },
        '/tasks/': [{ ...taskA, summary: '截断版总结' }],
      })
      triggerWsOnopen()
      await flushPromises()

      // 合并守卫兜底：即使缓存失效，轻量 payload 也不得覆盖已加载的完整内容
      expect(viewModel.selectedTask.value?.summary).toBe('完整总结')
      expect(viewModel.selectedTask.value?.transcript).toBe('完整转录')

      wrapper.unmount()
    })

    it('[I-1] 双开标签页：断线期间任务内容被另一标签页重置（latest_modified_at 变化），重连对账不得保留陈旧内容', async () => {
      const { viewModel, wrapper } = mountViewModel()
      const taskA = makeTask('task-a')

      // 首次加载完整内容（per-task 缓存记录 fetch 时的 latest_modified_at = T0）
      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '截断版总结', latest_modified_at: 'T0' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: '旧转录', summary: '旧总结', latest_modified_at: 'T0' },
      })
      viewModel.selectTask(taskA)
      await flushPromises()
      viewModel.activeTab.value = 'transcript'
      await flushPromises()
      expect(viewModel.selectedTask.value?.summary).toBe('旧总结')
      expect(viewModel.selectedTask.value?.latest_modified_at).toBe('T0')

      // 切回 summary tab，模拟重连时停留在总结页
      viewModel.activeTab.value = 'summary'
      await flushPromises()

      // 断线期间另一标签页对同一任务 re-transcribe：后端重置内容并写入新内容，
      // latest_modified_at T0 → T1。重连对账：列表项 summary 被 pop（无该字段），
      // 但携带最新 latest_modified_at。
      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: null, latest_modified_at: 'T1' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: '新转录', summary: '新总结', latest_modified_at: 'T1' },
        '/tasks/': [{ ...taskA, latest_modified_at: 'T1' }],
      })
      triggerWsOnopen()
      await flushPromises()

      // 陈旧内容不得保留：以 incoming（无内容字段）为准 → 懒加载 watch 补拉新内容
      expect(viewModel.selectedTask.value?.summary).toBe('新总结')
      expect(viewModel.selectedTask.value?.transcript).toBe('新转录')
      const taskAFullRequests = fullRequests().filter(([url]) => String(url).includes('task-a'))
      expect(taskAFullRequests).toHaveLength(2) // 首次加载 1 次 + 重连补拉 1 次（缓存未兜底）

      wrapper.unmount()
    })

    it('WS task_update 广播保持内容权威：re-transcribe 的 "" 重置必须传播到 selectedTask', async () => {
      const { viewModel, wrapper } = mountViewModel()
      const taskA = makeTask('task-a')

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '截断版总结' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: '完整转录', summary: '完整总结' },
      })
      viewModel.selectTask(taskA)
      await flushPromises()
      viewModel.activeTab.value = 'transcript'
      await flushPromises()
      expect(viewModel.selectedTask.value?.transcript).toBe('完整转录')

      // re-transcribe 广播：后端将 transcript 重置为空串（内容将变化）
      emitWsTaskUpdate({ ...taskA, status: 'TRANSCRIBING', transcript: '', summary: '' })
      await flushPromises()

      // 守卫统一不得吞掉权威的 '' 重置：UI 不得滞留旧内容
      expect(viewModel.selectedTask.value?.transcript).toBe('')
      expect(viewModel.selectedTask.value?.summary).toBe('')

      wrapper.unmount()
    })

    it('WS task_update 广播携带新的完整内容时以广播为准（权威来源覆盖旧内容）', async () => {
      const { viewModel, wrapper } = mountViewModel()
      const taskA = makeTask('task-a')

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '旧总结' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: '旧转录', summary: '旧总结' },
      })
      viewModel.selectTask(taskA)
      await flushPromises()

      emitWsTaskUpdate({ ...taskA, transcript: '新完整转录', summary: '新完整总结' })
      await flushPromises()

      expect(viewModel.selectedTask.value?.transcript).toBe('新完整转录')
      expect(viewModel.selectedTask.value?.summary).toBe('新完整总结')

      wrapper.unmount()
    })
  })

  describe('[P1-4] summary tab 按需加载补齐', () => {
    it('摘要未加载（null）时，默认 summary tab 自动请求 include_content=true 并显示完整版', async () => {
      const { viewModel, wrapper } = mountViewModel()
      const taskA = makeTask('task-a')

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: null },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: '完整转录', summary: '完整总结' },
      })

      // 默认 activeTab = 'summary'：首次选择任务即应懒加载完整总结
      viewModel.selectTask(taskA)
      await flushPromises()

      expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-a?include_content=true')
      expect(viewModel.selectedTask.value?.summary).toBe('完整总结')

      wrapper.unmount()
    })

    it('[I-2] 详情接口失败后 summary tab 自动补拉完整内容（不卡死在 undefined）', async () => {
      const { viewModel, wrapper } = mountViewModel()
      const taskA = makeTask('task-a')

      // 轻量详情请求失败（网络错误）：selectedTask 停留在列表项（summary=undefined）
      mockedAxios.get.mockImplementation((url: string) => {
        if (String(url).includes('include_content=false')) {
          return Promise.reject(new Error('网络错误'))
        }
        if (String(url).includes('include_content=true')) {
          return Promise.resolve({ data: { ...taskA, transcript: '完整转录', summary: '完整总结' } })
        }
        return Promise.resolve({ data: [] })
      })
      mockedAxios.post.mockResolvedValue({ data: {} })
      mockedAxios.isCancel.mockReturnValue(false)
      mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
        return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
      })

      // 默认 summary tab：详情失败后 summary 必须被标记为"未加载"（null），
      // 触发懒加载补拉完整内容
      viewModel.selectTask(taskA)
      await flushPromises()

      expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-a?include_content=true')
      expect(viewModel.selectedTask.value?.summary).toBe('完整总结')

      wrapper.unmount()
    })

    it('摘要被后端重置为空串（""）后，已完成任务的 summary tab 自动重载完整版（断线自愈）', async () => {
      const { viewModel, wrapper } = mountViewModel()
      const taskA = makeTask('task-a')

      // 轻量详情返回 summary=''（re-summarize 重置后断线，COMPLETED 广播丢失，
      // 对账拿到的是空串/无内容状态）——summary tab 必须自动补拉完整内容
      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '' },
        '/tasks/task-a?include_content=true': { ...taskA, transcript: '完整转录', summary: '重新总结后的新内容' },
      })

      viewModel.selectTask(taskA)
      await flushPromises()

      expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-a?include_content=true')
      expect(viewModel.selectedTask.value?.summary).toBe('重新总结后的新内容')

      wrapper.unmount()
    })

    it('不回归：summary 已有内容（截断版预览）时，summary tab 不重复请求完整内容', async () => {
      const { viewModel, wrapper } = mountViewModel()
      const taskA = makeTask('task-a')

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: '截断版总结' },
      })

      viewModel.selectTask(taskA)
      await flushPromises()

      expect(fullRequests()).toHaveLength(0)
      expect(viewModel.selectedTask.value?.summary).toBe('截断版总结')

      wrapper.unmount()
    })

    it('不回归：进行中任务（PROCESSING）选中时 summary tab 不触发完整内容加载', async () => {
      const { viewModel, wrapper } = mountViewModel()
      const taskA = { ...makeTask('task-a'), status: 'TRANSCRIBING' } as Task

      installDefaultAxios({
        '/tasks/task-a?include_content=false': { ...taskA, transcript: null, summary: null },
      })

      viewModel.selectTask(taskA)
      await flushPromises()

      expect(viewModel.activeTab.value).toBe('summary')
      expect(fullRequests()).toHaveLength(0)

      wrapper.unmount()
    })
  })

  describe('[P1-5] quality 死绑定清理', () => {
    it('提交 payload 使用显式常量 quality=audio_only（行为等价）；composable 不再导出 quality 状态', async () => {
      const { viewModel, wrapper } = mountViewModel()

      installDefaultAxios({ '/tasks/': [] })

      viewModel.videoUrl.value = 'https://example.com/video.mp4'
      await viewModel.submitTask()

      const postCall = mockedAxios.post.mock.calls.find(([url]) => String(url).includes('/tasks/'))
      expect(postCall).toBeTruthy()
      const payload = postCall![1] as Record<string, unknown>
      expect(payload.quality).toBe('audio_only')

      // 死绑定清理：quality 状态已从 composable 导出面移除
      expect('quality' in viewModel).toBe(false)

      wrapper.unmount()
    })
  })
})
