/**
 * 分P详情缓存（fetchTaskPart / taskPartDetails）回归测试（P0 修复）
 *
 * 根因（已确认）：fetchTaskPart 缓存命中条件 `cached.transcript !== undefined ||
 * cached.summary !== undefined` 对"处理中分P详情"（后端返回 task_parts 全行，
 * transcript/summary 为 null）为 true —— null !== undefined → 命中缓存 →
 * 分P完成后同任务内永久返回 stale null，展开区永久"暂无可展示内容"。
 *
 * 覆盖：
 * 1. [P0] 处理中展开过的分P完成后再展开：null 内容缓存不命中，重新请求并返回新内容；
 * 2. [P0] '' 空串内容同样不命中缓存（re-transcribe 等重置场景）；
 * 3. [回归] 有内容的详情正常命中缓存：不重复请求（返回同一缓存对象）；
 * 4. [回归] 跨任务缓存失效：task_id 不匹配（含 selectTask 清空 + 防御路径）重新请求；
 * 5. [回归] retryFailedParts 清空 taskPartDetails 后重新请求；
 * 6. [防御] 分P列表状态变化（SUMMARIZING → COMPLETED）时缓存失效：
 *    处理中展开仅有转录的分P，完成后再次展开重新请求拿到新总结。
 *
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { __resetTaskContentCaches, useTaskState } from '../state'
import type { TaskState } from '../state'
import type { Task, TaskPart } from '../../../types'

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

// 分P详情（后端 get_task_part 返回 task_parts 全行；处理中 transcript/summary 为
// null。TaskPart 类型声明为 string | undefined，未建模 null——此处模拟后端 JSON，
// 显式允许 null 并在返回处收敛为 TaskPart）
type PartOverrides = Partial<Omit<TaskPart, 'transcript' | 'summary'>> & {
  transcript?: string | null
  summary?: string | null
}
const makePart = (overrides: PartOverrides = {}): TaskPart => ({
  task_id: 'task-1',
  part_index: 0,
  status: 'COMPLETED',
  progress: 1,
  duration: 120,
  title: 'P1 标题',
  ...overrides,
} as TaskPart)

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

const detailCallsFor = (urlPart: string) =>
  mockedAxios.get.mock.calls.filter(([url]) => String(url).includes(urlPart)).length

describe('分P详情缓存（fetchTaskPart）：P0 修复 + 回归 + 防御', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    __resetTaskContentCaches()
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('WebSocket', vi.fn())
  })

  it('[P0] 处理中展开过的分P完成后再展开：null 内容缓存不命中，重新请求并返回新内容', async () => {
    const { task, wrapper } = mountTaskState()
    const task1 = { ...makeTask('task-1'), has_parts: true }

    let detailCalls = 0
    mockedAxios.get.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/tasks/task-1/parts/0')) {
        detailCalls += 1
        return Promise.resolve({
          data: detailCalls === 1
            ? makePart({ status: 'SUMMARIZING', transcript: null, summary: null })
            : makePart({ status: 'COMPLETED', transcript: 'P2 转录', summary: 'P2 总结' }),
        })
      }
      if (u.includes('/tasks/task-1?include_content=false')) {
        return Promise.resolve({ data: task1 })
      }
      if (u.includes('/tasks/task-1/parts')) {
        return Promise.resolve({ data: [makePart({ status: 'SUMMARIZING', transcript: null, summary: null })] })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(task1)
    await flushPromises()

    // 处理中展开 P2：详情无内容（后端返回 task_parts 全行，transcript/summary 为 null）
    const processing = await task.fetchTaskPart('task-1', 0)
    expect(processing?.summary).toBeNull()
    expect(processing?.transcript).toBeNull()

    // 分P完成后再次展开：不得命中 stale null 缓存（原缺陷：永久"暂无可展示内容"）
    const completed = await task.fetchTaskPart('task-1', 0)
    expect(completed?.summary).toBe('P2 总结')
    expect(completed?.transcript).toBe('P2 转录')
    expect(detailCalls).toBe(2)

    wrapper.unmount()
  })

  it("[P0] '' 空串内容同样不命中缓存（re-transcribe 等重置场景）", async () => {
    const { task, wrapper } = mountTaskState()
    const task1 = { ...makeTask('task-1'), has_parts: true }

    let detailCalls = 0
    mockedAxios.get.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/tasks/task-1/parts/0')) {
        detailCalls += 1
        return Promise.resolve({
          data: detailCalls === 1
            ? makePart({ status: 'SUMMARIZING', transcript: '', summary: '' })
            : makePart({ status: 'COMPLETED', transcript: 'P2 转录', summary: 'P2 总结' }),
        })
      }
      if (u.includes('/tasks/task-1?include_content=false')) {
        return Promise.resolve({ data: task1 })
      }
      if (u.includes('/tasks/task-1/parts')) {
        return Promise.resolve({ data: [makePart({ status: 'SUMMARIZING', transcript: '', summary: '' })] })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(task1)
    await flushPromises()

    const processing = await task.fetchTaskPart('task-1', 0)
    expect(processing?.summary).toBe('')

    const completed = await task.fetchTaskPart('task-1', 0)
    expect(completed?.summary).toBe('P2 总结')
    expect(detailCalls).toBe(2)

    wrapper.unmount()
  })

  it('[回归] 有内容的详情缓存命中：不重复请求（返回同一缓存对象）', async () => {
    const { task, wrapper } = mountTaskState()
    const task1 = { ...makeTask('task-1'), has_parts: true }
    const completedPart = makePart({ status: 'COMPLETED', transcript: 'P2 转录', summary: 'P2 总结' })

    mockedAxios.get.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/tasks/task-1/parts/0')) {
        return Promise.resolve({ data: completedPart })
      }
      if (u.includes('/tasks/task-1?include_content=false')) {
        return Promise.resolve({ data: task1 })
      }
      if (u.includes('/tasks/task-1/parts')) {
        return Promise.resolve({ data: [completedPart] })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(task1)
    await flushPromises()

    const first = await task.fetchTaskPart('task-1', 0)
    expect(first?.summary).toBe('P2 总结')
    expect(detailCallsFor('/tasks/task-1/parts/0')).toBe(1)

    // 再次展开：内容完整 + 列表状态一致 → 缓存命中，不重新请求
    // （taskPartDetails 为 Vue ref，命中缓存返回的是响应对象的响应式代理，
    //   与首次返回的原始对象不同引用，故断言内容一致 + 请求次数不变）
    const second = await task.fetchTaskPart('task-1', 0)
    expect(second?.summary).toBe('P2 总结')
    expect(second?.transcript).toBe('P2 转录')
    expect(second?.summary).toBe(first?.summary)
    expect(detailCallsFor('/tasks/task-1/parts/0')).toBe(1)

    wrapper.unmount()
  })

  it('[回归] 跨任务缓存失效：task_id 不匹配（selectTask 清空 + 防御路径）重新请求', async () => {
    const { task, wrapper } = mountTaskState()
    const task1 = { ...makeTask('task-1'), has_parts: true }
    const task2 = { ...makeTask('task-2'), has_parts: true }
    const part1 = makePart({ task_id: 'task-1', summary: 'task-1 的总结' })
    const part2 = makePart({ task_id: 'task-2', summary: 'task-2 的总结' })

    mockedAxios.get.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/tasks/task-1/parts/0')) {
        return Promise.resolve({ data: part1 })
      }
      if (u.includes('/tasks/task-2/parts/0')) {
        return Promise.resolve({ data: part2 })
      }
      if (u.includes('/tasks/task-2?include_content=false')) {
        return Promise.resolve({ data: task2 })
      }
      if (u.includes('/tasks/task-1?include_content=false')) {
        return Promise.resolve({ data: task1 })
      }
      if (u.includes('/tasks/task-1/parts')) {
        return Promise.resolve({ data: [] })
      }
      if (u.includes('/tasks/task-2/parts')) {
        return Promise.resolve({ data: [] })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(task1)
    await flushPromises()
    const first = await task.fetchTaskPart('task-1', 0)
    expect(first?.summary).toBe('task-1 的总结')

    // 切到 task-2：selectTask 清空 taskPartDetails → 重新请求
    task.selectTask(task2)
    await flushPromises()
    const second = await task.fetchTaskPart('task-2', 0)
    expect(second?.summary).toBe('task-2 的总结')
    expect(detailCallsFor('/tasks/task-2/parts/0')).toBe(1)

    // 防御路径：缓存条目 task_id 与请求不一致 → 必须重新请求（不得串任务内容）
    task.taskPartDetails.value = { 0: makePart({ task_id: 'other-task', summary: '别的任务的总结' }) }
    const third = await task.fetchTaskPart('task-1', 0)
    expect(third?.summary).toBe('task-1 的总结')
    expect(third?.summary).not.toBe('别的任务的总结')

    wrapper.unmount()
  })

  it('[回归] retryFailedParts 清空 taskPartDetails 后重新请求', async () => {
    const { task, wrapper } = mountTaskState()
    const task1 = { ...makeTask('task-1'), has_parts: true }
    const completedPart = makePart({ status: 'COMPLETED', transcript: 'P2 转录', summary: 'P2 总结' })

    mockedAxios.get.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/tasks/task-1/parts/0')) {
        return Promise.resolve({ data: completedPart })
      }
      if (u.includes('/tasks/task-1?include_content=false')) {
        return Promise.resolve({ data: task1 })
      }
      if (u.includes('/tasks/task-1/parts')) {
        return Promise.resolve({ data: [completedPart] })
      }
      return Promise.resolve({ data: [] })
    })
    mockedAxios.post.mockResolvedValue({ data: {} })

    task.selectTask(task1)
    await flushPromises()

    await task.fetchTaskPart('task-1', 0)
    expect(detailCallsFor('/tasks/task-1/parts/0')).toBe(1)

    // 重试失败分P：详情缓存被清空
    await task.retryFailedParts('task-1')
    await flushPromises()
    expect(task.taskPartDetails.value).toEqual({})

    // 再次展开：重新请求
    const again = await task.fetchTaskPart('task-1', 0)
    expect(again?.summary).toBe('P2 总结')
    expect(detailCallsFor('/tasks/task-1/parts/0')).toBe(2)

    wrapper.unmount()
  })

  it('[防御] 分P列表状态变化时缓存失效：处理中仅有转录的分P完成后重新请求拿到新总结', async () => {
    const { task, wrapper } = mountTaskState()
    const task1 = { ...makeTask('task-1'), has_parts: true }

    let listStatus = 'SUMMARIZING'
    let detailCalls = 0
    mockedAxios.get.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/tasks/task-1/parts/0')) {
        detailCalls += 1
        return Promise.resolve({
          data: detailCalls === 1
            ? makePart({ status: 'SUMMARIZING', transcript: 'P2 转录', summary: null })
            : makePart({ status: 'COMPLETED', transcript: 'P2 转录', summary: 'P2 总结' }),
        })
      }
      if (u.includes('/tasks/task-1?include_content=false')) {
        return Promise.resolve({ data: task1 })
      }
      if (u.includes('/tasks/task-1/parts')) {
        return Promise.resolve({ data: [makePart({ status: listStatus })] })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(task1)
    await flushPromises()

    // 处理中（SUMMARIZING）展开：详情仅有转录（总结还在生成）
    const first = await task.fetchTaskPart('task-1', 0)
    expect(first?.transcript).toBe('P2 转录')
    expect(first?.summary).toBeNull()
    expect(detailCalls).toBe(1)

    // 列表状态未变：缓存命中（有转录内容），不重复请求
    const second = await task.fetchTaskPart('task-1', 0)
    expect(second?.summary).toBeNull()
    expect(detailCalls).toBe(1)

    // 分P完成：列表刷新（对应 WS 广播后的 scheduleTaskPartsRefresh 路径）
    listStatus = 'COMPLETED'
    await task.fetchTaskParts('task-1')

    // 再展开：状态已变化 → 缓存失效 → 重新请求并返回新总结
    const third = await task.fetchTaskPart('task-1', 0)
    expect(third?.summary).toBe('P2 总结')
    expect(detailCalls).toBe(2)

    wrapper.unmount()
  })
})
