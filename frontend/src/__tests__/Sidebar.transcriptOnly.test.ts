/**
 * 对抗性测试：Sidebar「仅转录原文」开关（需求 B）
 *
 * 需求：提交表单新增醒目开关，开启后 summary_mode 变为 'none'，再次点击恢复之前模式。
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
  llmProviders: [],
  llmSettings: null,
  isUpdatingLlmSettings: false,
  isTestingLlm: false,
  transcriptionSettings: null,
  isUpdatingTranscriptionSettings: false,
  summarizationSettings: null,
  isUpdatingSummarizationSettings: false,
}

describe('对抗性：Sidebar 仅转录开关（需求 B5）', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
  })

  const mountSidebar = (summaryMode = 'standard') => {
    return mount(Sidebar, {
      props: {
        ...baseProps,
        videoUrl: '',
        selectedFile: null,
        localFilePath: '',
        summaryMode,
        isSidebarOpen: true,
      },
      global: {
        stubs: {
          ThemeSelector: true,
        },
      },
    })
  }

  const findToggle = (wrapper: ReturnType<typeof mountSidebar>) =>
    wrapper.findAll('button').find((b) => b.text().includes('仅转录原文'))

  it('点击开关 → summaryMode 变为 "none"，再次点击 → 恢复之前的模式', async () => {
    const wrapper = mountSidebar('standard')
    const toggle = findToggle(wrapper)
    expect(toggle).toBeTruthy()

    await toggle!.trigger('click')
    expect(wrapper.emitted('update:summaryMode')?.at(-1)).toEqual(['none'])

    await toggle!.trigger('click')
    expect(wrapper.emitted('update:summaryMode')?.at(-1)).toEqual(['standard'])
  })

  it('从 agent 模式开启 → 关闭后恢复到 agent 而不是默认值', async () => {
    const wrapper = mountSidebar('agent')
    const toggle = findToggle(wrapper)

    await toggle!.trigger('click')
    expect(wrapper.emitted('update:summaryMode')?.at(-1)).toEqual(['none'])

    await toggle!.trigger('click')
    expect(wrapper.emitted('update:summaryMode')?.at(-1)).toEqual(['agent'])
  })
})
