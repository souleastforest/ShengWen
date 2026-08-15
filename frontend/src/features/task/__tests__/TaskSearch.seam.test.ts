/**
 * P6 seam 契约测试：features/task/components/TaskSearch.vue
 *
 * 从 Sidebar.vue 拆出的任务搜索/管理视图（纯搬移，行为等价）。契约面：
 * - props: tasks / selectedTask
 * - emits: selectTask / deleteTask / retryTask / showInfo / focusSearchMatch
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TaskSearch from '../components/TaskSearch.vue'
import type { Task } from '../../../types'
import { TaskStatus } from '../../../types'

const makeTask = (overrides: Partial<Task>): Task => ({
  id: 'task-1',
  video_url: 'https://example.com/video.mp4',
  status: 'COMPLETED',
  progress: 1.0,
  created_at: '2026-08-08T10:00:00Z',
  ...overrides,
})

const mountSearch = (tasks: Task[], selectedTask: Task | null = null) =>
  mount(TaskSearch, { props: { tasks, selectedTask } })

const findKeywordInput = (wrapper: VueWrapper) =>
  wrapper.find('input[placeholder*="搜索"]')

const rows = (wrapper: VueWrapper) =>
  wrapper.findAll('div').filter((d) => d.classes().includes('cursor-pointer'))

describe('TaskSearch seam：过滤与排序', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('关键词命中 topic 或 summary 才显示', async () => {
    const matchTopic = makeTask({ id: 't1', topic: '机器学习入门' })
    const matchSummary = makeTask({ id: 't2', summary: '这里提到神经网络' })
    const miss = makeTask({ id: 't3', topic: '无关内容' })
    const wrapper = mountSearch([matchTopic, matchSummary, miss])

    await findKeywordInput(wrapper).setValue('神经')
    expect(rows(wrapper)).toHaveLength(1)
    expect(wrapper.text()).toContain('神经网络')
    expect(wrapper.text()).toContain('共 1 条')
  })

  it('状态筛选只显示对应状态任务', async () => {
    const failed = makeTask({ id: 't1', status: 'FAILED' })
    const ok = makeTask({ id: 't2', status: 'COMPLETED' })
    const wrapper = mountSearch([failed, ok])

    const selects = wrapper.findAll('select')
    const statusSelect = selects[0]!
    await statusSelect.setValue(TaskStatus.FAILED)
    expect(rows(wrapper)).toHaveLength(1)
    expect(wrapper.text()).toContain('失败')
  })

  it('排序：最新优先（默认）/ 最早优先 / 最新修改优先', async () => {
    const older = makeTask({ id: 't1', topic: '旧任务', created_at: '2026-01-01T00:00:00Z' })
    const newer = makeTask({ id: 't2', topic: '新任务', created_at: '2026-06-01T00:00:00Z' })
    const wrapper = mountSearch([older, newer])

    // 默认 newest：t2 在前
    let firstRow = rows(wrapper)[0]!
    expect(firstRow.text()).toContain('新任务')

    const selects = wrapper.findAll('select')
    await selects[1]!.setValue('oldest')
    firstRow = rows(wrapper)[0]!
    expect(firstRow.text()).toContain('旧任务')
  })

  it('无匹配结果显示空态', async () => {
    const wrapper = mountSearch([makeTask({ id: 't1' })])
    await findKeywordInput(wrapper).setValue('不存在的关键词xyz')
    expect(wrapper.text()).toContain('没有匹配的任务')
  })
})

describe('TaskSearch seam：emit', () => {
  it('点击结果行 emit selectTask', async () => {
    const task = makeTask({ id: 't1', topic: '主题' })
    const wrapper = mountSearch([task])
    await rows(wrapper)[0]!.trigger('click')
    expect(wrapper.emitted('selectTask')![0]).toEqual([task])
  })

  it('关键词命中 summary 时点击 emit focusSearchMatch（requestId 递增）', async () => {
    const task = makeTask({ id: 't1', summary: '正文包含目标关键词的内容' })
    const wrapper = mountSearch([task])
    await findKeywordInput(wrapper).setValue('目标关键词')
    await rows(wrapper)[0]!.trigger('click')

    const emits = wrapper.emitted('focusSearchMatch')!
    expect(emits).toHaveLength(1)
    expect(emits[0]).toEqual([
      { taskId: 't1', keyword: '目标关键词', source: 'summary', requestId: 1 },
    ])
  })

  it('第二次命中 requestId 递增为 2', async () => {
    const task = makeTask({ id: 't1', topic: '目标关键词主题' })
    const wrapper = mountSearch([task])
    await findKeywordInput(wrapper).setValue('目标关键词')
    await rows(wrapper)[0]!.trigger('click')
    await findKeywordInput(wrapper).setValue('目标关键词2')
    await findKeywordInput(wrapper).setValue('目标关键词')
    await rows(wrapper)[0]!.trigger('click')

    const emits = wrapper.emitted('focusSearchMatch')!
    expect(emits[1]![0]).toMatchObject({ requestId: 2, source: 'topic' })
  })

  it('重跑/删除/信息按钮 emit 对应事件且不触发 selectTask', async () => {
    const failed = makeTask({ id: 't1', status: 'FAILED', topic: '失败任务' })
    const wrapper = mountSearch([failed])

    const retry = wrapper.findAll('button').find((b) => b.attributes('title') === '快速重跑')!
    await retry.trigger('click')
    expect(wrapper.emitted('retryTask')![0]).toEqual([failed])

    const trash = wrapper.findAll('button').find((b) => b.attributes('title') === '删除任务')!
    await trash.trigger('click')
    expect(wrapper.emitted('deleteTask')![0]).toEqual(['t1'])

    const info = wrapper.findAll('button').find((b) => b.attributes('title') === '查看信息')!
    await info.trigger('click')
    expect(wrapper.emitted('showInfo')![0]).toEqual([failed])

    expect(wrapper.emitted('selectTask')).toBeUndefined()
  })
})
