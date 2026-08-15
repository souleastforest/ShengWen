/**
 * P6 Sidebar 巨型组件首批组件级单测：任务筛选交互（manage 视图）
 *
 * 覆盖代表性交互之一"任务筛选"：切到搜索视图 → 关键词过滤 → 点击结果行
 * emit selectTask + focusSearchMatch + 关闭侧栏（行为与拆片前等价）。
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Sidebar from '../components/Sidebar.vue'
import type { Task } from '../types'

const baseProps = {
  isLocalClient: false,
  tasks: [] as Task[],
  queues: [],
  selectedTask: null as Task | null,
  isSubmitting: false,
  uploadProgress: 0,
  maxUploadBytes: 2 * 1024 * 1024 * 1024,
  llmProviders: [],
  llmSettings: null,
  isUpdatingLlmSettings: false,
  isTestingLlm: false,
  transcriptionSettings: null,
  isUpdatingTranscriptionSettings: false,
  summarizationSettings: null,
  isUpdatingSummarizationSettings: false,
}

const makeTask = (overrides: Partial<Task>): Task => ({
  id: 'task-1',
  video_url: 'https://example.com/video.mp4',
  status: 'COMPLETED',
  progress: 1.0,
  created_at: '2026-08-08T10:00:00Z',
  ...overrides,
})

const mountSidebar = (tasks: Task[]) =>
  mount(Sidebar, {
    props: {
      ...baseProps,
      tasks,
      videoUrl: '',
      selectedFile: null,
      localFilePath: '',
      isSidebarOpen: true,
    },
    global: { stubs: { ThemeSelector: true } },
  })

const switchToManageTab = async (wrapper: VueWrapper) => {
  const tab = wrapper.findAll('button').find((b) => b.attributes('title') === '任务搜索')!
  await tab.trigger('click')
}

const resultRows = (wrapper: VueWrapper) =>
  wrapper.findAll('div').filter((d) => d.classes().includes('cursor-pointer'))

describe('Sidebar 任务筛选交互（manage 视图）', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
  })

  it('关键词过滤：仅保留命中 topic/summary 的任务行', async () => {
    const wrapper = mountSidebar([
      makeTask({ id: 't1', topic: '机器学习入门' }),
      makeTask({ id: 't2', summary: '包含目标关键词的总结' }),
      makeTask({ id: 't3', topic: '无关' }),
    ])
    await switchToManageTab(wrapper)

    const input = wrapper.find('input[placeholder*="搜索"]')
    await input.setValue('目标关键词')
    expect(resultRows(wrapper)).toHaveLength(1)
    expect(wrapper.text()).toContain('共 1 条')
    expect(wrapper.text()).not.toContain('机器学习入门')
  })

  it('点击搜索结果行：emit selectTask + focusSearchMatch，并关闭侧栏', async () => {
    const task = makeTask({ id: 't1', topic: '目标关键词主题' })
    const wrapper = mountSidebar([task])
    await switchToManageTab(wrapper)

    const input = wrapper.find('input[placeholder*="搜索"]')
    await input.setValue('目标关键词')
    await resultRows(wrapper)[0]!.trigger('click')

    expect(wrapper.emitted('selectTask')![0]).toEqual([task])
    expect(wrapper.emitted('focusSearchMatch')![0]).toEqual([
      { taskId: 't1', keyword: '目标关键词', source: 'topic', requestId: 1 },
    ])
    // 点击后侧栏关闭（行为等价）
    expect(wrapper.emitted('update:isSidebarOpen')![0]).toEqual([false])
  })

  it('无关键词点击结果行：仅 emit selectTask，不触发 focusSearchMatch', async () => {
    const task = makeTask({ id: 't1', topic: '随便一个主题' })
    const wrapper = mountSidebar([task])
    await switchToManageTab(wrapper)

    await resultRows(wrapper)[0]!.trigger('click')
    expect(wrapper.emitted('selectTask')).toHaveLength(1)
    expect(wrapper.emitted('focusSearchMatch')).toBeUndefined()
  })
})
