/**
 * 对抗性测试：Sidebar 状态标签 — ASR 分片计数展示
 *
 * 需求：TRANSCRIBING 且 asr_chunk_total > 0 时，状态标签显示"转录中 (done/total)"
 * （如 10 个 6min 分片 → "转录中 (3/10)"）；无分片计数时保持原"转录中"；
 * SUMMARIZING 的"总结中 (x/y)"标签不受影响（语义独立）。
 * 失败 = 实现缺陷。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
  video_url: 'https://example.com/video',
  status: 'TRANSCRIBING',
  created_at: '2026-08-08T10:00:00Z',
  progress: 30,
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
      stubs: {
        ThemeSelector: true,
      },
    },
  })
}

const findBadgeText = (wrapper: ReturnType<typeof mountSidebar>) => {
  const badges = wrapper.findAll('.rounded-full')
  return badges.map((b) => b.text()).join(' | ')
}

describe('Sidebar 状态标签：ASR 分片计数', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('TRANSCRIBING 且 asr_chunk_total>0：显示"转录中 (done/total)"', () => {
    const wrapper = mountSidebar([
      makeTask({ asr_chunk_total: 10, asr_chunk_done: 3 }),
    ])
    expect(findBadgeText(wrapper)).toContain('转录中 (3/10)')
  })

  it('done 缺省（undefined/null）时按 0 兜底显示', () => {
    const wrapper = mountSidebar([
      makeTask({ asr_chunk_total: 10, asr_chunk_done: undefined }),
    ])
    expect(findBadgeText(wrapper)).toContain('转录中 (0/10)')
  })

  it('TRANSCRIBING 但无分片计数：保持原"转录中"', () => {
    const wrapper = mountSidebar([makeTask({ asr_chunk_total: 0 })])
    expect(findBadgeText(wrapper)).toContain('转录中')
    expect(findBadgeText(wrapper)).not.toContain('(')
  })

  it('SUMMARIZING 的总结分块标签不受影响（语义独立）', () => {
    const wrapper = mountSidebar([
      makeTask({
        status: 'SUMMARIZING',
        summary_chunk_total: 5,
        summary_chunk_done: 2,
      }),
    ])
    const text = findBadgeText(wrapper)
    expect(text).toContain('总结中 (2/5)')
    expect(text).not.toContain('转录中')
  })

  it('TRANSCRIBING 的分片计数与总结分块字段互不混淆', () => {
    const wrapper = mountSidebar([
      makeTask({
        asr_chunk_total: 10,
        asr_chunk_done: 3,
        summary_chunk_total: 5,
        summary_chunk_done: 2,
      }),
    ])
    const text = findBadgeText(wrapper)
    expect(text).toContain('转录中 (3/10)')
    expect(text).not.toContain('2/5')
  })
})
