/**
 * P7 迁移：useTaskViewModel.summaryModeTab.adversarial.test.ts 的组件层部分
 * （Sidebar 模式 Tab 三选一渲染 + TaskMetaCard summary_mode 渲染）。
 * 组件 props/emit seam 不变（P7 零改动清单），用例原样保留；
 * composable 层 payload/确认窗口断言已随域迁至 features/task|upload/__tests__。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import Sidebar from '../components/Sidebar.vue'
import TaskMetaCard from '../components/TaskMetaCard.vue'
import type { SummaryMode, Task } from '../types'

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

const sidebarBaseProps = {
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

const mountSidebar = (summaryMode?: SummaryMode) => {
  return mount(Sidebar, {
    props: {
      ...sidebarBaseProps,
      videoUrl: '',
      selectedFile: null,
      localFilePath: '',
      isSidebarOpen: true,
      // 'auto' 为历史兼容值（[S7] 用例验证其 thumb 不 fall-through）：运行时透传，
      // 仅类型收窄到组件声明的 UI 三态
      ...(summaryMode !== undefined
        ? { summaryMode: summaryMode as Exclude<SummaryMode, 'auto'> }
        : {}),
    },
    global: {
      stubs: {
        ThemeSelector: true,
      },
    },
  })
}

const findTab = (wrapper: ReturnType<typeof mountSidebar>, label: string) =>
  wrapper.findAll('button').find((b) => b.text().includes(label))

const findThumb = (wrapper: ReturnType<typeof mountSidebar>) =>
  wrapper.findAll('div').find((d) =>
    d.classes().some((c) => c.includes('w-[calc(33.333%'))
  )

const thumbClass = (wrapper: ReturnType<typeof mountSidebar>) =>
  findThumb(wrapper)?.classes().join(' ') ?? ''

const helperVisible = (wrapper: ReturnType<typeof mountSidebar>) =>
  wrapper.findAll('p').some((p) => p.text().includes('提交后不生成 AI 总结'))

describe('对抗性：模式 Tab 三选一（需求 1）— Sidebar 层', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
  })

  it('[S3] 连续点击循环 none→standard→agent→none：emit 序列正确、最终状态一致、thumb 随动', async () => {
    const wrapper = mountSidebar()

    const clicks: Array<[string, string]> = [
      ['标准模式', 'standard'],
      ['Agent 模式', 'agent'],
      ['仅转录', 'none'],
    ]
    const expectedThumbs = [
      'left-[calc(33.333%)]',
      'left-[calc(66.667%)]',
      'left-1',
    ]

    for (let i = 0; i < clicks.length; i++) {
      const [label, mode] = clicks[i]!
      const tab = findTab(wrapper, label)!
      await tab.trigger('click')
      const emits = wrapper.emitted('update:summaryMode')!
      expect(emits[emits.length - 1]).toEqual([mode])
      expect(thumbClass(wrapper)).toContain(expectedThumbs[i])
    }

    // 回到 none：helper 文案恢复可见
    expect(helperVisible(wrapper)).toBe(true)
    wrapper.unmount()
  })

  it('[S4] thumb 宽度类 w-[calc(33.333%-6px)] 三态下均存在（钉住宽度回归）', () => {
    for (const mode of ['none', 'standard', 'agent'] as const) {
      const cls = thumbClass(mountSidebar(mode))
      expect(cls).toContain('w-[calc(33.333%-6px)]')
    }
  })

  it('[S7] 历史值 auto 传入时 thumb 不 fall-through 到 agent 样式（Record 显式兜底）', () => {
    const wrapper = mountSidebar('auto')
    const cls = thumbClass(wrapper)
    // auto → none 位置（Record 显式映射），绝不落到 agent 样式
    expect(cls).toContain('left-1')
    expect(cls).not.toContain('left-[calc(66.667%)]')
    expect(cls).not.toContain('agent-gradient')
    wrapper.unmount()
  })

  it('[S5] 旧 UI 残留检查：渲染结果不含"仅转录原文"按钮/文案；none 时新 helper 文案存在', () => {
    const wrapper = mountSidebar('none')
    expect(wrapper.html()).not.toContain('仅转录原文')
    expect(helperVisible(wrapper)).toBe(true)
    // 非 none 模式不得出现该 helper（需求：仅 none 时提示）
    expect(helperVisible(mountSidebar('standard'))).toBe(false)
    expect(helperVisible(mountSidebar('agent'))).toBe(false)
    wrapper.unmount()
  })
})

describe('对抗性：TaskMetaCard summary_mode 渲染（需求 2e）', () => {
  it('[B7] none → "仅转录"；auto → "自动模式"；standard/agent 保持既有文案；undefined 不显示该行', () => {
    const mountCard = (summary_mode?: SummaryMode) =>
      mount(TaskMetaCard, {
        props: {
          task: { ...baseTask, summary_mode },
          topic: '主题',
        },
      })

    const noneHtml = mountCard('none').text()
    expect(noneHtml).toContain('仅转录')
    expect(noneHtml).not.toContain('自动模式')
    expect(noneHtml).not.toContain('标准模式')

    const autoHtml = mountCard('auto').text()
    expect(autoHtml).toContain('自动模式')
    expect(autoHtml).not.toContain('仅转录')

    expect(mountCard('standard').text()).toContain('标准模式')
    expect(mountCard('agent').text()).toContain('Agent 增强模式')

    const undefinedText = mountCard(undefined).text()
    expect(undefinedText).not.toContain('总结模式')
  })
})
