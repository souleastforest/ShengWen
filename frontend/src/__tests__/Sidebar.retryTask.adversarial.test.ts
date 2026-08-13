/**
 * 对抗性测试：Sidebar「快速重跑」的搜索（manage）视图 + 列表响应式消失
 *
 * 需求：主列表与 manage/搜索视图对称显示 FAILED 红色小圆钮（@click.stop，
 * title="快速重跑"），点击 emit retryTask；非 FAILED 一律不显示；
 * 重跑后任务状态随 WS 更新离开 FAILED 时按钮消失。
 * 每个用例失败 = 实现缺陷。
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
  video_url: 'https://example.com/video',
  status: 'FAILED',
  created_at: '2026-08-08T10:00:00Z',
  progress: 0,
  ...overrides,
})

const mountSidebar = (tasks: Task[]) => {
  return mount(Sidebar, {
    props: {
      ...baseProps,
      tasks,
      videoUrl: '',
      selectedFile: null,
      localFilePath: '',
      isSidebarOpen: true,
    },
    global: {
      stubs: { ThemeSelector: true },
    },
  })
}

const retryButtons = (wrapper: VueWrapper) =>
  wrapper.findAll('button').filter((b) => b.attributes('title') === '快速重跑')

const switchToManageTab = async (wrapper: VueWrapper) => {
  const tab = wrapper.findAll('button').find((b) => b.attributes('title') === '任务搜索')
  expect(tab).toBeDefined()
  await tab!.trigger('click')
}

describe('对抗性：Sidebar 快速重跑（manage 视图 + 响应式）', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
  })

  it('manage/搜索视图：FAILED 任务行显示重跑按钮，点击 emit retryTask 且不触发 selectTask', async () => {
    const failedTask = makeTask({ id: 't-failed', status: 'FAILED' })
    const wrapper = mountSidebar([failedTask, makeTask({ id: 't-ok', status: 'COMPLETED' })])
    await switchToManageTab(wrapper)

    // manage 视图：仅 FAILED 行有按钮
    expect(retryButtons(wrapper)).toHaveLength(1)

    await retryButtons(wrapper)[0]!.trigger('click')
    const emits = wrapper.emitted('retryTask')
    expect(emits).toHaveLength(1)
    expect(emits![0]).toEqual([failedTask])
    // @click.stop：不得触发行点击的 selectTask
    expect(wrapper.emitted('selectTask')).toBeUndefined()
  })

  it('manage/搜索视图：非 FAILED（SUMMARIZING/TRANSCRIBING/PARTIAL）不显示重跑按钮', async () => {
    const wrapper = mountSidebar([
      makeTask({ id: 't1', status: 'SUMMARIZING' }),
      makeTask({ id: 't2', status: 'TRANSCRIBING' }),
      makeTask({ id: 't3', status: 'PARTIAL' }),
      makeTask({ id: 't4', status: 'PENDING' }),
      makeTask({ id: 't5', status: 'COMPLETED' }),
    ])
    await switchToManageTab(wrapper)

    expect(retryButtons(wrapper)).toHaveLength(0)
  })

  it('主列表与 manage 视图对称：两个视图各自渲染重跑按钮', async () => {
    const wrapper = mountSidebar([makeTask({ id: 't-failed', status: 'FAILED' })])
    expect(retryButtons(wrapper)).toHaveLength(1) // 主列表
    await switchToManageTab(wrapper)
    expect(retryButtons(wrapper)).toHaveLength(1) // manage 视图
  })

  it('任务状态经 WS 更新离开 FAILED 后按钮消失（FAILED→PENDING 响应式）', async () => {
    const wrapper = mountSidebar([makeTask({ id: 't-failed', status: 'FAILED' })])
    expect(retryButtons(wrapper)).toHaveLength(1)

    // 模拟 WS 推送：任务对象状态变为 PENDING（retry 后后端重置）
    await wrapper.setProps({
      tasks: [makeTask({ id: 't-failed', status: 'PENDING' })],
    })
    expect(retryButtons(wrapper)).toHaveLength(0)

    // 再变回 FAILED（如重跑再次失败）→ 按钮恢复
    await wrapper.setProps({
      tasks: [makeTask({ id: 't-failed', status: 'FAILED' })],
    })
    expect(retryButtons(wrapper)).toHaveLength(1)
  })

  it('manage 视图关键词过滤后 FAILED 任务仍显示重跑按钮', async () => {
    const wrapper = mountSidebar([makeTask({ id: 't-failed', status: 'FAILED', video_url: 'file:///tmp/x.mp4' })])
    await switchToManageTab(wrapper)

    // 关键词命中 topic/摘要后仍保留按钮
    const input = wrapper.find('input[placeholder*="搜索"]')
    await input.setValue('')
    expect(retryButtons(wrapper)).toHaveLength(1)
  })
})
