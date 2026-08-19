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
 * 6. [P2-1] 处理中状态缓存的详情（即使有内容）不命中：列表未刷新窗口内
 *    再次展开重新请求拿到新内容；列表刷新为 COMPLETED 后缓存命中；
 * 7. [防御] 非处理中状态变化（COMPLETED → FAILED）时缓存失效重新请求；
 * 8. [P2-5] 网络失败后下次展开重新请求无死锁（loadingPartIndex 复位）。
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

  it('[P2-1] 处理中状态缓存的详情（即使有内容）不命中：列表未刷新窗口内重新请求；COMPLETED 后缓存命中', async () => {
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

    // 分P刚完成、前端 parts 列表尚未刷新（仍 SUMMARIZING）的窗口内再次展开：
    // 处理中状态缓存视为必然过期 → 不命中，重新请求拿到新总结
    const second = await task.fetchTaskPart('task-1', 0)
    expect(second?.summary).toBe('P2 总结')
    expect(detailCalls).toBe(2)

    // 列表刷新到 COMPLETED（对应 WS 广播后的 scheduleTaskPartsRefresh 路径）：
    // 内容完整 + 状态一致 → 缓存命中，不重复请求
    listStatus = 'COMPLETED'
    await task.fetchTaskParts('task-1')
    const third = await task.fetchTaskPart('task-1', 0)
    expect(third?.summary).toBe('P2 总结')
    expect(detailCalls).toBe(2)

    wrapper.unmount()
  })

  it('[防御] 非处理中状态变化（COMPLETED → FAILED）时缓存失效重新请求', async () => {
    const { task, wrapper } = mountTaskState()
    const task1 = { ...makeTask('task-1'), has_parts: true }

    let listStatus = 'COMPLETED'
    let detailCalls = 0
    mockedAxios.get.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/tasks/task-1/parts/0')) {
        detailCalls += 1
        return Promise.resolve({
          data: detailCalls === 1
            ? makePart({ status: 'COMPLETED', transcript: 'P2 转录', summary: 'P2 总结' })
            : makePart({ status: 'FAILED', transcript: 'P2 转录', summary: 'P2 总结', error_message: '后处理校验失败' }),
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

    // COMPLETED 展开：完整内容入缓存
    const first = await task.fetchTaskPart('task-1', 0)
    expect(first?.summary).toBe('P2 总结')
    expect(detailCalls).toBe(1)

    // 列表状态未变：缓存命中，不重复请求
    const second = await task.fetchTaskPart('task-1', 0)
    expect(second?.summary).toBe('P2 总结')
    expect(detailCalls).toBe(1)

    // 列表显示状态变化（COMPLETED → FAILED，非处理中）：缓存失效重新请求
    listStatus = 'FAILED'
    await task.fetchTaskParts('task-1')
    const third = await task.fetchTaskPart('task-1', 0)
    expect(third?.status).toBe('FAILED')
    expect(third?.error_message).toBe('后处理校验失败')
    expect(detailCalls).toBe(2)

    wrapper.unmount()
  })

  it('[P2-5] 网络失败后下次展开重新请求无死锁（loadingPartIndex 复位）', async () => {
    const { task, wrapper } = mountTaskState()
    const task1 = { ...makeTask('task-1'), has_parts: true }

    let detailCalls = 0
    mockedAxios.get.mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes('/tasks/task-1/parts/0')) {
        detailCalls += 1
        if (detailCalls === 1) {
          return Promise.reject(new Error('network error'))
        }
        return Promise.resolve({
          data: makePart({ status: 'COMPLETED', transcript: 'P2 转录', summary: 'P2 总结' }),
        })
      }
      if (u.includes('/tasks/task-1?include_content=false')) {
        return Promise.resolve({ data: task1 })
      }
      if (u.includes('/tasks/task-1/parts')) {
        return Promise.resolve({ data: [makePart({ status: 'COMPLETED' })] })
      }
      return Promise.resolve({ data: [] })
    })

    task.selectTask(task1)
    await flushPromises()

    // 首次展开网络失败：拒绝上抛、缓存不写入、loadingPartIndex 复位（finally）
    await expect(task.fetchTaskPart('task-1', 0)).rejects.toThrow('network error')
    expect(task.loadingPartIndex.value).toBeNull()

    // 下次展开：重新请求成功（无死锁）
    const result = await task.fetchTaskPart('task-1', 0)
    expect(result?.summary).toBe('P2 总结')
    expect(detailCalls).toBe(2)

    wrapper.unmount()
  })
})
