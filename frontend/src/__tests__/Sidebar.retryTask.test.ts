/**
 * 对抗性测试：Sidebar FAILED 任务行「快速重跑」按钮
 *
 * 需求：FAILED 任务显示红色小圆钮（PhArrowClockwise，title="快速重跑"），
 * 点击 emit retryTask；仅 FAILED 显示（重跑后状态变为 PENDING，按钮随之消失）。
 * 本地文件任务（file://）同样显示（重跑走 re-transcribe 的本地媒体分支）。
 * 失败 = 实现缺陷。
 */
import { mount } from '@vue/test-utils'
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

describe('对抗性：Sidebar FAILED 任务「快速重跑」按钮', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
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
        stubs: {
          ThemeSelector: true,
        },
      },
    })
  }

  const findRetryButtons = (wrapper: ReturnType<typeof mountSidebar>) =>
    wrapper.findAll('button').filter((b) => b.attributes('title') === '快速重跑')

  it('FAILED 任务行渲染「重跑」按钮；COMPLETED/PENDING 等其他状态不渲染', () => {
    const wrapper = mountSidebar([
      makeTask({ id: 't-failed', status: 'FAILED' }),
      makeTask({ id: 't-completed', status: 'COMPLETED' }),
      makeTask({ id: 't-pending', status: 'PENDING' }),
      makeTask({ id: 't-downloading', status: 'DOWNLOADING' }),
      makeTask({ id: 't-partial', status: 'PARTIAL' }),
    ])

    const buttons = findRetryButtons(wrapper)
    expect(buttons).toHaveLength(1)
    // 按钮位于 FAILED 任务行内
    expect(buttons[0]!.findComponent({ name: 'PhArrowClockwise' }).exists()).toBe(true)
  })

  it('点击「重跑」emit retryTask 且携带对应任务对象；不触发 selectTask（stop 冒泡）', async () => {
    const failedTask = makeTask({ id: 't-failed', status: 'FAILED' })
    const wrapper = mountSidebar([failedTask])

    const button = findRetryButtons(wrapper)[0]!
    await button.trigger('click')

    const emits = wrapper.emitted('retryTask')
    expect(emits).toHaveLength(1)
    expect(emits![0]).toEqual([failedTask])
    expect(wrapper.emitted('selectTask')).toBeUndefined()
  })

  it('本地文件任务（file://）同样显示「重跑」按钮', () => {
    const wrapper = mountSidebar([
      makeTask({
        id: 't-local',
        status: 'FAILED',
        video_url: 'file:///home/user/videos/demo.mp4',
      }),
    ])

    expect(findRetryButtons(wrapper)).toHaveLength(1)
  })

  it('任务状态离开 FAILED 后按钮消失（重跑 → PENDING 的列表响应）', () => {
    const wrapper = mountSidebar([
      makeTask({ id: 't-pending', status: 'PENDING' }),
    ])

    expect(findRetryButtons(wrapper)).toHaveLength(0)
  })
})
