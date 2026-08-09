/**
 * 对抗性测试：Sidebar 提交表单模式 Tab 三选一（仅转录 | 标准模式 | Agent 模式）
 *
 * 需求（用户决策）：模式 Tab 改为三选一，默认选中「仅转录」（summary_mode='none'）；
 * 删除下方独立的「仅转录原文」琥珀 toggle。
 * 失败 = 实现缺陷。
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Sidebar from '../components/Sidebar.vue'
import type { SummaryMode, Task } from '../types'

const baseProps = {
  isLocalClient: false,
  tasks: [] as Task[],
  queues: [],
  selectedTask: null as Task | null,
  isSubmitting: false,
  llmProviders: [],
  llmSettings: null,
  isUpdatingLlmSettings: false,
  isTestingLlm: false,
  transcriptionSettings: null,
  isUpdatingTranscriptionSettings: false,
  summarizationSettings: null,
  isUpdatingSummarizationSettings: false,
}

describe('对抗性：Sidebar 模式 Tab 三选一（默认仅转录）', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
  })

  const mountSidebar = (summaryMode?: Exclude<SummaryMode, 'auto'>) => {
    return mount(Sidebar, {
      props: {
        ...baseProps,
        videoUrl: '',
        selectedFile: null,
        localFilePath: '',
        isSidebarOpen: true,
        ...(summaryMode !== undefined ? { summaryMode } : {}),
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

  // 滑动 thumb：定位绝对定位的滑条 div（宽为三态均分）
  const findThumb = (wrapper: ReturnType<typeof mountSidebar>) =>
    wrapper.findAll('div').find((d) =>
      d.classes().some((c) => c.includes('w-[calc(33.333%'))
    )

  const thumbClass = (wrapper: ReturnType<typeof mountSidebar>) =>
    findThumb(wrapper)?.classes().join(' ') ?? ''

  const findGlow = (wrapper: ReturnType<typeof mountSidebar>) =>
    wrapper.findAll('div').find((d) => d.classes().includes('agent-glow'))

  const helperVisible = (wrapper: ReturnType<typeof mountSidebar>) =>
    wrapper.findAll('p').some((p) => p.text().includes('提交后不生成 AI 总结'))

  it('点击「仅转录」→ 发出 none、thumb 落在 none 分支（left-1）、helper 文案出现', async () => {
    const wrapper = mountSidebar('standard')
    expect(helperVisible(wrapper)).toBe(false)

    const tab = findTab(wrapper, '仅转录')!
    await tab.trigger('click')

    const emits = wrapper.emitted('update:summaryMode')!
    expect(emits[emits.length - 1]).toEqual(['none'])
    expect(thumbClass(wrapper)).toContain('left-1')
    expect(thumbClass(wrapper)).toContain('bg-white')
    expect(helperVisible(wrapper)).toBe(true)
  })

  it('点击「标准模式」→ 发出 standard、thumb 落在中间分支 left-[calc(33.333%)]', async () => {
    const wrapper = mountSidebar() // 默认 none
    const tab = findTab(wrapper, '标准模式')!
    await tab.trigger('click')

    const emits = wrapper.emitted('update:summaryMode')!
    expect(emits[emits.length - 1]).toEqual(['standard'])
    expect(thumbClass(wrapper)).toContain('left-[calc(33.333%)]')
    expect(thumbClass(wrapper)).toContain('bg-white')
    expect(helperVisible(wrapper)).toBe(false)
  })

  it('点击「Agent 模式」→ 发出 agent、thumb 落在 right 分支 left-[calc(66.667%)]、glow 点亮', async () => {
    const wrapper = mountSidebar()
    const tab = findTab(wrapper, 'Agent 模式')!
    await tab.trigger('click')

    const emits = wrapper.emitted('update:summaryMode')!
    expect(emits[emits.length - 1]).toEqual(['agent'])
    expect(thumbClass(wrapper)).toContain('left-[calc(66.667%)]')
    expect(thumbClass(wrapper)).toContain('agent-gradient')
    expect(findGlow(wrapper)!.classes()).toContain('opacity-100')
  })

  it('三态 thumb 类名互不相同（钉住误渲染回归：枚举落空错位/宽度错位）', () => {
    const noneThumb = thumbClass(mountSidebar('none'))
    const standardThumb = thumbClass(mountSidebar('standard'))
    const agentThumb = thumbClass(mountSidebar('agent'))

    const thumbs = [noneThumb, standardThumb, agentThumb]
    expect(new Set(thumbs).size).toBe(3)
    // 每个模式都有且仅有自己的定位分支
    expect(noneThumb).toContain('left-1')
    expect(noneThumb).not.toContain('left-[calc(33.333%)]')
    expect(noneThumb).not.toContain('left-[calc(66.667%)]')
    expect(standardThumb).toContain('left-[calc(33.333%)]')
    expect(standardThumb).not.toContain('left-1')
    expect(standardThumb).not.toContain('left-[calc(66.667%)]')
    expect(agentThumb).toContain('left-[calc(66.667%)]')
    expect(agentThumb).not.toContain('left-1')
    expect(agentThumb).not.toContain('left-[calc(33.333%)]')
    // agent 是唯一带渐变背景的 thumb
    expect(agentThumb).toContain('agent-gradient')
    expect(noneThumb).not.toContain('agent-gradient')
    expect(standardThumb).not.toContain('agent-gradient')
  })

  it('独立的「仅转录原文」toggle 按钮已不存在', () => {
    const wrapper = mountSidebar()
    const toggle = wrapper.findAll('button').find((b) => b.text().includes('仅转录原文'))
    expect(toggle).toBeUndefined()
  })

  it('默认 summaryMode 为 none（不传 prop 时）：thumb 落 none 分支、helper 可见、无多余 emit', async () => {
    const wrapper = mountSidebar()
    await wrapper.vm.$nextTick()

    expect(thumbClass(wrapper)).toContain('left-1')
    expect(helperVisible(wrapper)).toBe(true)
    expect(wrapper.emitted('update:summaryMode')).toBeUndefined()
  })
})
