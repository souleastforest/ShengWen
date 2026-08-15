/**
 * P6 seam 契约测试：features/task/components/TaskList.vue
 *
 * 从 Sidebar.vue 拆出的任务列表（纯搬移，行为等价）。契约面：
 * - props: tasks / selectedTask / queues
 * - emits: selectTask / deleteTask / retryTask / showInfo
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TaskList from '../components/TaskList.vue'
import type { QueueSnapshot, Task } from '../../../types'

const makeTask = (overrides: Partial<Task>): Task => ({
  id: 'task-1',
  video_url: 'https://example.com/video.mp4',
  status: 'COMPLETED',
  progress: 1.0,
  created_at: '2026-08-08T10:00:00Z',
  ...overrides,
})

const mountList = (tasks: Task[], selectedTask: Task | null = null, queues: QueueSnapshot[] = []) =>
  mount(TaskList, {
    props: { tasks, selectedTask, queues },
  })

describe('TaskList seam：DOM 结构与渲染', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('渲染任务行（topic 优先，回退 title）', () => {
    const task = makeTask({ id: 't1', topic: '我的主题', title: '我的标题' })
    const wrapper = mountList([task])
    expect(wrapper.text()).toContain('我的主题')
  })

  it('空列表显示空态文案', () => {
    const wrapper = mountList([])
    expect(wrapper.text()).toContain('暂无任务记录')
  })

  it('选中任务行高亮（ring/border 类）', () => {
    const task = makeTask({ id: 't1' })
    const wrapper = mountList([task], task)
    const row = wrapper.findAll('div').find((d) => d.classes().includes('cursor-pointer'))!
    expect(row.classes().join(' ')).toContain('border-blue-200')
  })

  it('FAILED 任务显示快速重跑按钮，其他状态不显示', () => {
    const failed = makeTask({ id: 't1', status: 'FAILED' })
    const ok = makeTask({ id: 't2', status: 'COMPLETED' })
    const wrapper = mountList([failed, ok])
    const retryButtons = wrapper.findAll('button').filter((b) => b.attributes('title') === '快速重跑')
    expect(retryButtons).toHaveLength(1)
  })

  it('排队中任务显示排队徽标（不显示普通状态标签）', () => {
    const task = makeTask({ id: 't1', status: 'PENDING' })
    const queues: QueueSnapshot[] = [
      { name: '默认队列', active_task_id: null, queue_size: 2, waiting_task_ids: ['other', 't1'] },
    ]
    const wrapper = mountList([task], null, queues)
    expect(wrapper.text()).toContain('排队中 (默认队列 #2)')
  })
})

describe('TaskList seam：emit', () => {
  it('点击行 emit selectTask', async () => {
    const task = makeTask({ id: 't1' })
    const wrapper = mountList([task])
    const row = wrapper.findAll('div').find((d) => d.classes().includes('cursor-pointer'))!
    await row.trigger('click')
    expect(wrapper.emitted('selectTask')![0]).toEqual([task])
  })

  it('删除按钮 emit deleteTask(taskId)', async () => {
    const wrapper = mountList([makeTask({ id: 't1' })])
    const trash = wrapper.findAll('button').find((b) => b.attributes('title') === '删除任务')!
    await trash.trigger('click')
    expect(wrapper.emitted('deleteTask')![0]).toEqual(['t1'])
    // 行点击不得触发 selectTask
    expect(wrapper.emitted('selectTask')).toBeUndefined()
  })

  it('信息按钮 emit showInfo(task)', async () => {
    const task = makeTask({ id: 't1' })
    const wrapper = mountList([task])
    const info = wrapper.findAll('button').find((b) => b.attributes('title') === '查看信息')!
    await info.trigger('click')
    expect(wrapper.emitted('showInfo')![0]).toEqual([task])
  })

  it('快速重跑按钮 emit retryTask(task)', async () => {
    const task = makeTask({ id: 't1', status: 'FAILED' })
    const wrapper = mountList([task])
    const retry = wrapper.findAll('button').find((b) => b.attributes('title') === '快速重跑')!
    await retry.trigger('click')
    expect(wrapper.emitted('retryTask')![0]).toEqual([task])
    expect(wrapper.emitted('selectTask')).toBeUndefined()
  })
})

describe('TaskList seam：状态标签与进度', () => {
  it('分P任务状态标签带分片计数（PARTIAL done/total）', () => {
    const task = makeTask({ id: 't1', status: 'PARTIAL', part_count: 3, part_completed: 2 })
    const wrapper = mountList([task])
    expect(wrapper.text()).toContain('部分完成 (2/3)')
  })

  it('ASR 分片计数：TRANSCRIBING 显示"转录中 (x/y)"', () => {
    const task = makeTask({ id: 't1', status: 'TRANSCRIBING', asr_chunk_total: 10, asr_chunk_done: 3 })
    const wrapper = mountList([task])
    expect(wrapper.text()).toContain('转录中 (3/10)')
  })

  it('下载中任务渲染进度条', () => {
    const task = makeTask({ id: 't1', status: 'DOWNLOADING', progress: 40 })
    const wrapper = mountList([task])
    const bar = wrapper.findAll('div').find((d) => d.attributes('style')?.includes('width: 40%'))
    expect(bar).toBeTruthy()
  })
})
