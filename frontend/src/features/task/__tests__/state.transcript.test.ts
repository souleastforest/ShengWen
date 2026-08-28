/**
 * P7 迁移：useTaskViewModel.transcript.test.ts → features/task/state.ts（原文懒加载）
 * 用例逻辑原样保留，仅改引用（useTaskViewModel → useTaskState）与 setup。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
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

const mountTaskState = () => {
  let task!: TaskState
  let ws!: ReturnType<typeof useWebSocket>
  const TestComponent = defineComponent({
    setup() {
      task = useTaskState()
      ws = useWebSocket()
      task.syncWithWs(ws)
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  task.fetchTasks()
  ws.connect()
  return { task, ws, wrapper }
}

type WsInstance = {
  close: () => void
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: ((err: unknown) => void) | null
}

describe('task 域 transcript lazy loading（useTaskViewModel 迁移）', () => {
  beforeEach(() => {
    // module 级 per-task 缓存跨测试实例共享，必须重置，否则内容会被上个测试污染
    __resetTaskContentCaches()
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})

    // 默认所有 GET 返回空数组（初始列表拉取）
    mockedAxios.get.mockResolvedValue({ data: [] })
    mockedAxios.isCancel.mockReturnValue(false)
    mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
      return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
    })

    wsInstances = []
    const WebSocketMock = vi.fn(function MockWebSocket(this: WsInstance) {
      this.close = vi.fn()
      this.onopen = null
      this.onmessage = null
      this.onclose = null
      this.onerror = null
      wsInstances.push(this)
    })
    vi.stubGlobal('WebSocket', WebSocketMock)
  })

  let wsInstances: WsInstance[] = []

  it('selectTask 详情请求使用 include_content=false，transcript 被后端剥离为 null', async () => {
    const { task, wrapper } = mountTaskState()

    // 轻量详情响应：transcript 为 null（后端 pop 后经 response_model 序列化为 null）
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(completedTask)
    await flushPromises()

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-1?include_content=false')
    expect(task.selectedTask.value?.transcript).toBeNull()

    wrapper.unmount()
  })

  it('切换到"原文"tab 时按需加载 include_content=true 完整内容并填充 transcript', async () => {
    const { task, wrapper } = mountTaskState()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null, summary: 'summary preview' } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...completedTask, transcript: '完整转录原文', summary: '完整总结' } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(completedTask)
    await flushPromises()
    expect(task.selectedTask.value?.transcript).toBeNull()

    task.activeTab.value = 'transcript'
    await flushPromises()

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-1?include_content=true')
    expect(task.selectedTask.value?.transcript).toBe('完整转录原文')

    wrapper.unmount()
  })

  it('已加载的 transcript 不会在选择同一任务时被轻量详情响应覆盖', async () => {
    const { task, wrapper } = mountTaskState()

    let includeFull = false
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=true') {
        includeFull = true
        return Promise.resolve({ data: { ...completedTask, transcript: '完整转录原文' } })
      }
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(completedTask)
    await flushPromises()
    task.activeTab.value = 'transcript'
    await flushPromises()
    expect(includeFull).toBe(true)
    expect(task.selectedTask.value?.transcript).toBe('完整转录原文')

    // 重新点击同一任务：轻量详情返回后不应丢失已加载的 transcript
    task.selectTask(completedTask)
    await flushPromises()
    expect(task.selectedTask.value?.transcript).toBe('完整转录原文')

    wrapper.unmount()
  })

  it('copyContent(\'transcript\') 在转录未加载时先拉取完整内容再复制', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', {
      clipboard: { writeText: writeTextMock },
    })

    const { task, wrapper } = mountTaskState()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null, summary: 'summary preview' } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...completedTask, transcript: '可复制的完整转录' } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(completedTask)
    await flushPromises()

    // 停留在"总结"tab 直接复制转录（悬浮工具栏场景）
    const result = await task.copyContent('transcript')

    expect(result).toBe(true)
    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-1?include_content=true')
    expect(writeTextMock).toHaveBeenCalledWith('可复制的完整转录')

    wrapper.unmount()
  })

  it('任务确实没有转录时 copyContent 返回 false（不伪造内容）', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', {
      clipboard: { writeText: writeTextMock },
    })

    const { task, wrapper } = mountTaskState()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url.includes('/tasks/task-1')) {
        return Promise.resolve({ data: { ...completedTask, transcript: null, summary: 'summary preview' } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(completedTask)
    await flushPromises()

    const result = await task.copyContent('transcript')

    expect(result).toBe(false)
    expect(writeTextMock).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('切换任务时（activeTab 已是 transcript）自动加载新任务的完整转录，不残留旧任务内容', async () => {
    const { task, wrapper } = mountTaskState()
    const taskA = { ...completedTask, id: 'task-a' }
    const taskB = { ...completedTask, id: 'task-b' }

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-a?include_content=false') {
        return Promise.resolve({ data: { ...taskA, transcript: null } })
      }
      if (url === '/tasks/task-a?include_content=true') {
        return Promise.resolve({ data: { ...taskA, transcript: 'A的完整转录' } })
      }
      if (url === '/tasks/task-b?include_content=false') {
        return Promise.resolve({ data: { ...taskB, transcript: null } })
      }
      if (url === '/tasks/task-b?include_content=true') {
        return Promise.resolve({ data: { ...taskB, transcript: 'B的完整转录' } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(taskA)
    await flushPromises()
    task.activeTab.value = 'transcript'
    await flushPromises()
    expect(task.selectedTask.value?.transcript).toBe('A的完整转录')

    // 核心回归：activeTab 已是 'transcript'，切换任务时 watch 必须重新触发加载
    task.selectTask(taskB)
    await flushPromises()

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-b?include_content=true')
    expect(task.selectedTask.value?.id).toBe('task-b')
    expect(task.selectedTask.value?.transcript).toBe('B的完整转录')

    wrapper.unmount()
  })

  it('同任务重选/WS 重连时完整 summary 不被轻量响应的截断版覆盖', async () => {
    const { task, wrapper } = mountTaskState()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null, summary: '截断版总结' } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...completedTask, transcript: '完整转录', summary: '完整总结' } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(completedTask)
    await flushPromises()
    expect(task.selectedTask.value?.summary).toBe('截断版总结')

    task.activeTab.value = 'transcript'
    await flushPromises()
    expect(task.selectedTask.value?.summary).toBe('完整总结')
    expect(task.selectedTask.value?.transcript).toBe('完整转录')

    // 模拟 WS 重连 / 重新点击同一任务：轻量详情返回后不得覆盖已加载的完整内容
    task.selectTask(completedTask)
    await flushPromises()
    expect(task.selectedTask.value?.summary).toBe('完整总结')
    expect(task.selectedTask.value?.transcript).toBe('完整转录')

    wrapper.unmount()
  })

  it('downloadContent 在转录未加载时先请求完整内容再下载', async () => {
    const createObjectURLMock = vi.fn().mockReturnValue('blob:mock')
    const revokeObjectURLMock = vi.fn()
    // 可构造的 URL stub：happy-dom 的 anchor click 会执行 new URL(...)，纯对象会抛错
    class MockURL {}
    Object.assign(MockURL, {
      createObjectURL: createObjectURLMock,
      revokeObjectURL: revokeObjectURLMock,
    })
    vi.stubGlobal('URL', MockURL as unknown as typeof URL)

    const { task, wrapper } = mountTaskState()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null, summary: '截断版总结' } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...completedTask, transcript: '可下载的完整转录' } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(completedTask)
    await flushPromises()

    await task.downloadContent('transcript')

    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-1?include_content=true')
    expect(createObjectURLMock).toHaveBeenCalled()
    expect(revokeObjectURLMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('A→B→A 往返切换时从 per-task 缓存恢复 A 的完整转录，不重复请求', async () => {
    const { task, wrapper } = mountTaskState()
    const taskA = { ...completedTask, id: 'task-a' }
    const taskB = { ...completedTask, id: 'task-b' }

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-a?include_content=false') {
        return Promise.resolve({ data: { ...taskA, transcript: null } })
      }
      if (url === '/tasks/task-a?include_content=true') {
        return Promise.resolve({ data: { ...taskA, transcript: 'A的完整转录', summary: 'A的完整总结' } })
      }
      if (url === '/tasks/task-b?include_content=false') {
        return Promise.resolve({ data: { ...taskB, transcript: null } })
      }
      if (url === '/tasks/task-b?include_content=true') {
        return Promise.resolve({ data: { ...taskB, transcript: 'B的完整转录', summary: 'B的完整总结' } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(taskA)
    await flushPromises()
    task.activeTab.value = 'transcript'
    await flushPromises()

    task.selectTask(taskB)
    await flushPromises()

    // 回到 A：应立即恢复 A 的完整内容（缓存补齐），无需再次请求完整内容
    task.selectTask(taskA)
    await flushPromises()

    expect(task.selectedTask.value?.id).toBe('task-a')
    expect(task.selectedTask.value?.transcript).toBe('A的完整转录')
    expect(task.selectedTask.value?.summary).toBe('A的完整总结')

    const fullRequests = mockedAxios.get.mock.calls.filter(([url]) => String(url).includes('include_content=true'))
    expect(fullRequests).toHaveLength(2) // 仅 A 首次与 B 首次各一次

    wrapper.unmount()
  })

  it('转录加载请求并发去重：tab watch 与 copyContent 共享同一次 GET', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', {
      clipboard: { writeText: writeTextMock },
    })

    const { task, wrapper } = mountTaskState()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...completedTask, transcript: '完整转录' } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(completedTask)
    await flushPromises()

    // 切到"原文"tab（watch 已排队）后立刻复制：两者触发同一按需加载
    task.activeTab.value = 'transcript'
    const copyPromise = task.copyContent('transcript')
    await flushPromises()
    const result = await copyPromise

    const fullRequests = mockedAxios.get.mock.calls.filter(([url]) => String(url).includes('include_content=true'))
    expect(fullRequests).toHaveLength(1)
    expect(result).toBe(true)
    expect(writeTextMock).toHaveBeenCalledWith('完整转录')

    wrapper.unmount()
  })

  it('多P finalize 无 WS 广播：轮询合并后 transcript watch 补拉完整内容（含 segments）', async () => {
    const { task, wrapper } = mountTaskState()
    const multiTask: Task = {
      ...completedTask,
      id: 'task-multi',
      has_parts: true,
      latest_modified_at: 'T0',
    }

    // 阶段一：多P 未 finalize，主行 transcript 为 ''（轻量详情与完整内容均返回空串；
    // 列表剥离 → null 是阶段二轮询合并的输入）
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-multi?include_content=false') {
        return Promise.resolve({ data: { ...multiTask, transcript: '', latest_modified_at: 'T0' } })
      }
      if (url === '/tasks/task-multi?include_content=true') {
        return Promise.resolve({ data: { ...multiTask, transcript: '', latest_modified_at: 'T0' } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(multiTask)
    await flushPromises()
    task.activeTab.value = 'transcript'
    await flushPromises()
    expect(task.selectedTask.value?.transcript).toBe('')
    const fullCalls = () =>
      mockedAxios.get.mock.calls.filter(([url]) => String(url).includes('include_content=true')).length
    expect(fullCalls()).toBe(1)

    // 阶段二：finalize 写主行（db.update_task 无 WS 广播）；轮询合并：列表剥离
    // transcript → null、latest_modified_at 变化 → 内容陈旧 → 主行 transcript 置 null。
    // watch 必须因 transcript 值变化（'' → null）重新补拉完整内容。
    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/') {
        return Promise.resolve({ data: [{ ...multiTask, transcript: null, latest_modified_at: 'T1' }] })
      }
      if (url === '/tasks/task-multi?include_content=true') {
        return Promise.resolve({
          data: {
            ...multiTask,
            transcript: '多P 聚合转录全文',
            transcript_segments: [{ start: 0, end: 3, text: '第一段' }],
            latest_modified_at: 'T1',
          },
        })
      }
      return Promise.resolve({ data: [] })
    })

    await task.fetchTasks() // 模拟 60s 轮询
    await flushPromises()

    expect(task.selectedTask.value?.transcript).toBe('多P 聚合转录全文')
    expect(task.selectedTask.value?.transcript_segments).toEqual([{ start: 0, end: 3, text: '第一段' }])
    expect(fullCalls()).toBe(2) // 初始 + watch 补拉

    wrapper.unmount()
  })

  it('downloadSubtitle: 转录缺失时先拉完整内容，再 GET /subtitles?format=srt 并下载 Blob（文件名 字幕-<topic>.srt）', async () => {
    const createObjectURLMock = vi.fn().mockReturnValue('blob:mock')
    const revokeObjectURLMock = vi.fn()
    class MockURL {}
    Object.assign(MockURL, {
      createObjectURL: createObjectURLMock,
      revokeObjectURL: revokeObjectURLMock,
    })
    vi.stubGlobal('URL', MockURL as unknown as typeof URL)

    // 捕获 anchor 的 download 文件名
    let lastDownload = ''
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      lastDownload = this.download
    })

    const { task, wrapper } = mountTaskState()
    const taskWithTopic = { ...completedTask, id: 'task-1', topic: '测试主题' }

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...taskWithTopic, transcript: null } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...taskWithTopic, transcript: '完整转录' } })
      }
      if (url === '/tasks/task-1/subtitles') {
        return Promise.resolve({ data: '1\n00:00:00,000 --> 00:00:01,000\n完整转录\n' })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(taskWithTopic)
    await flushPromises()

    await task.downloadSubtitle('srt')

    // 转录缺失 → 先按需加载完整内容
    expect(mockedAxios.get).toHaveBeenCalledWith('/tasks/task-1?include_content=true')
    // 字幕端点：responseType text + format 参数
    expect(mockedAxios.get).toHaveBeenCalledWith(
      '/tasks/task-1/subtitles',
      expect.objectContaining({ params: { format: 'srt' }, responseType: 'text' }),
    )
    // Blob 内容 = 后端返回的字幕文本
    const blob = createObjectURLMock.mock.calls[0]![0] as Blob
    expect(await blob.text()).toContain('00:00:00,000 --> 00:00:01,000')
    expect(lastDownload).toBe('字幕-测试主题.srt')
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeObjectURLMock).toHaveBeenCalled()

    clickSpy.mockRestore()
    wrapper.unmount()
  })

  it('downloadSubtitle: 转录已加载时不重复拉完整内容，vtt 文件名 字幕-<topic>.vtt', async () => {
    const createObjectURLMock = vi.fn().mockReturnValue('blob:mock')
    const revokeObjectURLMock = vi.fn()
    class MockURL {}
    Object.assign(MockURL, {
      createObjectURL: createObjectURLMock,
      revokeObjectURL: revokeObjectURLMock,
    })
    vi.stubGlobal('URL', MockURL as unknown as typeof URL)

    let lastDownload = ''
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      lastDownload = this.download
    })

    const { task, wrapper } = mountTaskState()
    const taskWithTopic = { ...completedTask, id: 'task-1', topic: '测试主题' }

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...taskWithTopic, transcript: null } })
      }
      if (url === '/tasks/task-1?include_content=true') {
        return Promise.resolve({ data: { ...taskWithTopic, transcript: '已加载的完整转录' } })
      }
      if (url === '/tasks/task-1/subtitles') {
        return Promise.resolve({ data: 'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n已加载的完整转录\n' })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(taskWithTopic)
    await flushPromises()
    task.activeTab.value = 'transcript'
    await flushPromises()

    const fullCallsBefore = mockedAxios.get.mock.calls.filter(([url]) =>
      String(url).includes('include_content=true')).length

    await task.downloadSubtitle('vtt')

    const fullCallsAfter = mockedAxios.get.mock.calls.filter(([url]) =>
      String(url).includes('include_content=true')).length
    expect(fullCallsAfter).toBe(fullCallsBefore) // 已加载 → 不重复拉取

    expect(mockedAxios.get).toHaveBeenCalledWith(
      '/tasks/task-1/subtitles',
      expect.objectContaining({ params: { format: 'vtt' }, responseType: 'text' }),
    )
    const blob = createObjectURLMock.mock.calls[0]![0] as Blob
    expect(await blob.text()).toContain('WEBVTT')
    expect(lastDownload).toBe('字幕-测试主题.vtt')

    clickSpy.mockRestore()
    wrapper.unmount()
  })

  // ---- transcript_segments 线格式防御（BLOCKER-3：WS 广播可能透传 DB 原始 JSON 字符串）----

  it('WS task_update 广播携带字符串 transcript_segments → 合并后归一化为数组（现状字符串透传 → TranscriptViewer 崩溃）', async () => {
    const { task, wrapper } = mountTaskState()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(completedTask)
    await flushPromises()

    // 后端漏修场景：WS 广播携带 DB 原始 JSON 字符串（多P finalize 后广播全量行）
    const rawSegments = JSON.stringify([{ start: 0, end: 3, text: '第一段' }])
    wsInstances[0]!.onmessage?.({
      data: JSON.stringify({
        type: 'task_update',
        task: { ...completedTask, transcript: '转录全文', transcript_segments: rawSegments },
      }),
    })
    await flushPromises()

    expect(task.selectedTask.value?.transcript_segments).toEqual([{ start: 0, end: 3, text: '第一段' }])
    expect(typeof task.selectedTask.value?.transcript_segments).not.toBe('string')

    wrapper.unmount()
  })

  it('WS task_update 广播携带坏 JSON segments → 合并后为 null（与后端 _segments_list 语义一致，不残留字符串）', async () => {
    const { task, wrapper } = mountTaskState()

    mockedAxios.get.mockImplementation((url: string) => {
      if (url === '/tasks/task-1?include_content=false') {
        return Promise.resolve({ data: { ...completedTask, transcript: null } })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(completedTask)
    await flushPromises()

    wsInstances[0]!.onmessage?.({
      data: JSON.stringify({
        type: 'task_update',
        task: { ...completedTask, transcript_segments: '{bad json' },
      }),
    })
    await flushPromises()

    expect(task.selectedTask.value?.transcript_segments).toBeNull()

    wrapper.unmount()
  })

  it('hasSegments 语义：字符串 segments 不视为"已加载"内容（merge 时回退 incoming 归一化值，不保留污染字符串）', async () => {
    const { task, wrapper } = mountTaskState()

    // 场景：selectedTask 已被字符串 segments 污染（模拟旧广播透传），随后轮询
    // 合并轻量列表项（transcript_segments 为 null）——字符串不得判定为"已加载"，
    // 否则 null 不会覆盖污染值，TranscriptViewer 仍可能收到字符串。
    task.selectedTask.value = {
      ...completedTask,
      transcript_segments: JSON.stringify([{ start: 0, end: 3, text: '污染' }]),
    } as unknown as Task
    mockedAxios.get.mockResolvedValue({ data: [{ ...completedTask, transcript_segments: null }] })

    await task.fetchTasks()

    expect(task.selectedTask.value?.transcript_segments).toBeNull()

    wrapper.unmount()
  })
})
