/**
 * 对抗性测试：仅转录模式的"总结标题"开关
 *
 * 需求（用户决策）：模式 Tab 选中「仅转录」（summary_mode='none'）时，提交表单
 * 出现"总结标题"开关，默认开启（转录完成后对全文调用 LLM 生成标题，api同款）；
 * 标准模式/Agent 模式本就会生成总结并带出标题，不显示该开关、不生效。
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

describe('对抗性：仅转录"总结标题"开关', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
  })

  const mountSidebar = (opts?: {
    summaryMode?: Exclude<SummaryMode, 'auto'>
    generateTopic?: boolean
  }) => {
    return mount(Sidebar, {
      props: {
        ...baseProps,
        videoUrl: '',
        selectedFile: null,
        localFilePath: '',
        isSidebarOpen: true,
        ...(opts?.summaryMode !== undefined ? { summaryMode: opts.summaryMode } : {}),
        ...(opts?.generateTopic !== undefined ? { generateTopic: opts.generateTopic } : {}),
      },
      global: {
        stubs: {
          ThemeSelector: true,
        },
      },
    })
  }

  // 开关行：含"总结标题"文案的容器（label + subtext + switch 按钮）
  const findToggleRow = (wrapper: ReturnType<typeof mountSidebar>) =>
    wrapper.findAll('div').find((d) => d.text().includes('总结标题') && d.text().includes('仅转录完成后自动生成标题'))

  const findToggleButton = (wrapper: ReturnType<typeof mountSidebar>) =>
    wrapper.findAll('button').find((b) => b.attributes('role') === 'switch' && b.attributes('aria-checked') !== undefined)

  it('仅 none 时显示开关：none 可见，standard/agent 隐藏', () => {
    expect(findToggleRow(mountSidebar({ summaryMode: 'none' }))).toBeDefined()
    expect(findToggleRow(mountSidebar({ summaryMode: 'standard' }))).toBeUndefined()
    expect(findToggleRow(mountSidebar({ summaryMode: 'agent' }))).toBeUndefined()
  })

  it('默认开启：不传 generateTopic prop 时 switch 呈开启态（bg-amber-500 + translate-x-5）', () => {
    const wrapper = mountSidebar({ summaryMode: 'none' })
    const btn = findToggleButton(wrapper)!
    expect(btn.attributes('aria-checked')).toBe('true')
    expect(btn.classes()).toContain('bg-amber-500')
    const knob = btn.find('span')
    expect(knob.classes()).toContain('translate-x-5')
  })

  it('点击开关 → 发出 update:generateTopic=false，再点恢复 true', async () => {
    const wrapper = mountSidebar({ summaryMode: 'none' })
    const btn = findToggleButton(wrapper)!

    await btn.trigger('click')
    const emits = wrapper.emitted('update:generateTopic')!
    expect(emits[emits.length - 1]).toEqual([false])
    expect(btn.attributes('aria-checked')).toBe('false')
    expect(btn.classes()).toContain('bg-gray-300')

    await btn.trigger('click')
    expect(wrapper.emitted('update:generateTopic')![wrapper.emitted('update:generateTopic')!.length - 1]).toEqual([true])
    expect(btn.attributes('aria-checked')).toBe('true')
  })

  it('显式传入 generateTopic=false 时呈关闭态', () => {
    const wrapper = mountSidebar({ summaryMode: 'none', generateTopic: false })
    const btn = findToggleButton(wrapper)!
    expect(btn.attributes('aria-checked')).toBe('false')
    expect(btn.classes()).toContain('bg-gray-300')
  })

  it('模式切换 none→standard 时开关消失且不再影响其他模式', async () => {
    const wrapper = mountSidebar({ summaryMode: 'none' })
    expect(findToggleRow(wrapper)).toBeDefined()

    const standardTab = wrapper.findAll('button').find((b) => b.text().includes('标准模式'))!
    await standardTab.trigger('click')
    expect(findToggleRow(wrapper)).toBeUndefined()
    // 切回 none：开关恢复可见且保持开启
    const noneTab = wrapper.findAll('button').find((b) => b.text().includes('仅转录'))!
    await noneTab.trigger('click')
    expect(findToggleRow(wrapper)).toBeDefined()
    expect(findToggleButton(wrapper)!.attributes('aria-checked')).toBe('true')
  })
})
