/**
 * P6 seam 契约测试：features/settings/components/SettingsFormLlm.vue
 *
 * Q8 语义以弹窗（SettingsModal）为准：SettingsForm×3 双入口复用（Sidebar 面板 +
 * SettingsModal），字段/夹逼/默认值统一到弹窗语义。
 * 契约面：
 * - props: llmProviders / llmSettings / isUpdatingLlmSettings / isTestingLlm
 * - emits: updateLlmSettings / updateLlmSettingsAndTest
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsFormLlm from '../components/SettingsFormLlm.vue'
import type { LLMProvider, LLMSettings } from '../../../types'

const providers: LLMProvider[] = [
  {
    id: 'openai',
    label: 'OpenAI',
    default_base_url: 'https://api.openai.com/v1',
    default_model_id: 'gpt-4o',
    description: 'OpenAI',
  },
  {
    id: 'anthropic',
    label: 'Anthropic',
    default_base_url: 'https://api.anthropic.com',
    default_model_id: 'claude-3-5-sonnet',
    description: 'Anthropic',
  },
]

const makeLlmSettings = (overrides: Partial<LLMSettings> = {}): LLMSettings => ({
  provider: 'openai',
  base_url: 'https://api.openai.com/v1',
  model_id: 'gpt-4o',
  temperature: 0.7,
  context_window_size: 128000,
  has_api_key: true,
  api_key_hint: 'sk-***abcd',
  extra_headers: {},
  ...overrides,
})

const baseProps = {
  llmProviders: providers,
  llmSettings: null as LLMSettings | null,
  isUpdatingLlmSettings: false,
  isTestingLlm: false,
}

const mountForm = (overrides: Record<string, unknown> = {}) =>
  mount(SettingsFormLlm, { props: { ...baseProps, ...overrides } })

describe('SettingsFormLlm seam：props → 表单状态同步', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('llmSettings 同步到表单（含 temperature 默认值 0.7）', () => {
    const wrapper = mountForm({ llmSettings: makeLlmSettings() })
    const inputs = wrapper.findAll('input')
    expect((wrapper.find('select').element as HTMLSelectElement).value).toBe('openai')
    expect(inputs.some((i) => (i.element as HTMLInputElement).value === 'https://api.openai.com/v1')).toBe(true)
    expect(inputs.some((i) => (i.element as HTMLInputElement).value === 'gpt-4o')).toBe(true)
    // API Key 输入框留空
    const apiKey = inputs.find((i) => (i.element as HTMLInputElement).type === 'password')!
    expect((apiKey.element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('已配置 API Key')
  })

  it('供应商选择触发预设填充（base_url/model_id）', async () => {
    const wrapper = mountForm()
    await wrapper.find('select').setValue('anthropic')
    const inputs = wrapper.findAll('input')
    expect(inputs.some((i) => (i.element as HTMLInputElement).value === 'https://api.anthropic.com')).toBe(true)
    expect(inputs.some((i) => (i.element as HTMLInputElement).value === 'claude-3-5-sonnet')).toBe(true)
  })
})

describe('SettingsFormLlm seam：保存 emit', () => {
  it('保存配置 emit updateLlmSettings（trim 后 payload，空 API Key 不携带）', async () => {
    const wrapper = mountForm({ llmSettings: makeLlmSettings() })
    await wrapper.find('select').setValue('openai')
    const save = wrapper.findAll('button').find((b) => b.text().includes('保存配置'))!
    await save.trigger('click')

    const emits = wrapper.emitted('updateLlmSettings')!
    expect(emits).toHaveLength(1)
    expect(emits![0]![0]).toEqual({
      provider: 'openai',
      base_url: 'https://api.openai.com/v1',
      model_id: 'gpt-4o',
      temperature: 0.7,
    })
  })

  it('填写 API Key 后保存携带 api_key 且清空输入框', async () => {
    const wrapper = mountForm({ llmSettings: makeLlmSettings() })
    const apiKey = wrapper.findAll('input').find((i) => (i.element as HTMLInputElement).type === 'password')!
    await apiKey.setValue('  sk-new-key  ')
    const save = wrapper.findAll('button').find((b) => b.text().includes('保存配置'))!
    await save.trigger('click')

    const emits = wrapper.emitted('updateLlmSettings')!
    expect(emits![0]![0]).toMatchObject({ api_key: 'sk-new-key' })
    expect((apiKey.element as HTMLInputElement).value).toBe('')
  })

  it('anthropic 供应商显示 Extra Headers 区，合法 JSON 解析进 payload', async () => {
    const wrapper = mountForm()
    await wrapper.find('select').setValue('anthropic')
    const textarea = wrapper.find('textarea')
    expect(textarea.exists()).toBe(true)
    await textarea.setValue('{"anthropic-version": "2023-06-01"}')

    const save = wrapper.findAll('button').find((b) => b.text().includes('保存配置'))!
    await save.trigger('click')
    const emits = wrapper.emitted('updateLlmSettings')!
    expect(emits![0]![0]).toMatchObject({
      extra_headers: { 'anthropic-version': '2023-06-01' },
    })
  })

  it('无效 JSON 的 Extra Headers 被忽略', async () => {
    const wrapper = mountForm()
    await wrapper.find('select').setValue('anthropic')
    await wrapper.find('textarea').setValue('{not-json')

    const save = wrapper.findAll('button').find((b) => b.text().includes('保存配置'))!
    await save.trigger('click')
    const emits = wrapper.emitted('updateLlmSettings')!
    expect(emits![0]![0]).not.toHaveProperty('extra_headers')
  })

  it('测试连接按钮 emit updateLlmSettingsAndTest', async () => {
    const wrapper = mountForm({ llmSettings: makeLlmSettings() })
    await wrapper.find('select').setValue('openai')
    const test = wrapper.findAll('button').find((b) => b.text().includes('测试连接'))!
    await test.trigger('click')

    const emits = wrapper.emitted('updateLlmSettingsAndTest')!
    expect(emits).toHaveLength(1)
    expect(emits![0]![0]).toMatchObject({ provider: 'openai' })
  })

  it('保存中/测试中禁用表单按钮', () => {
    const wrapper = mountForm({ isUpdatingLlmSettings: true, isTestingLlm: true })
    const buttons = wrapper.findAll('button')
    expect(buttons.every((b) => (b.element as HTMLButtonElement).disabled)).toBe(true)
  })
})
