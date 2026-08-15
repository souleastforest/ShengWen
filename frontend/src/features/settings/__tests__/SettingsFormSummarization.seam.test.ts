/**
 * P6 seam 契约测试：features/settings/components/SettingsFormSummarization.vue
 *
 * Q8 语义以弹窗（SettingsModal）为准：分块时长不做 Sidebar 旧版夹逼
 * （min≥30s / max≥min / target 夹逼），仅 max_agent_value_chars 下限 100。
 * 契约面：
 * - props: summarizationSettings / isUpdatingSummarizationSettings
 * - emits: updateSummarizationSettings
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsFormSummarization from '../components/SettingsFormSummarization.vue'
import type { SummarizationSettings } from '../../../types'

const makeSummarizationSettings = (
  overrides: Partial<SummarizationSettings> = {},
): SummarizationSettings => ({
  mode: 'agent',
  auto_chunk_min_audio_duration_sec: 40 * 60,
  auto_chunk_min_transcript_lines: 1800,
  chunk_target_duration_sec: 20 * 60,
  chunk_min_duration_sec: 10 * 60,
  chunk_max_duration_sec: 30 * 60,
  boundary_jump_sec: 10,
  prev_tail_timestamp_lines_m: 5,
  prev_summary_tail_chars_j: 300,
  llm_call_retry_max: 3,
  max_agent_value_chars: 500,
  fallback_to_standard_on_agent_error: true,
  ...overrides,
})

const baseProps = {
  summarizationSettings: makeSummarizationSettings(),
  isUpdatingSummarizationSettings: false,
}

const mountForm = (overrides: Record<string, unknown> = {}) =>
  mount(SettingsFormSummarization, {
    props: { ...baseProps, summarizationSettings: makeSummarizationSettings(), ...overrides },
  })

const findNumberInputs = (wrapper: ReturnType<typeof mountForm>) => wrapper.findAll('input[type="number"]')

const save = async (wrapper: ReturnType<typeof mountForm>) => {
  const button = wrapper.findAll('button').find((b) => b.text().includes('保存设置'))!
  await button.trigger('click')
}

describe('SettingsFormSummarization seam：props → 表单状态同步', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('秒值以分钟同步到输入框（secondsToMinutes）', () => {
    const wrapper = mountForm()
    const inputs = findNumberInputs(wrapper)
    const values = inputs.map((i) => (i.element as HTMLInputElement).value)
    // 核心参数：目标 20 / 最短 10 / 最长 30；自动触发：音频 40 / 行数 1800；高级：容错 10 / 引用上限 500
    expect(values).toEqual(['20', '10', '30', '40', '1800', '10', '500'])
  })

  it('失败回退开关默认反映 settings 值', () => {
    const wrapper = mountForm()
    const sw = wrapper.findAll('button').find((b) => b.attributes('role') === 'switch')!
    expect(sw.attributes('aria-checked')).toBe('true')
  })
})

describe('SettingsFormSummarization seam：保存 payload（弹窗语义，无夹逼）', () => {
  it('保存按弹窗语义原样换算（分钟→秒），不做 Sidebar 旧版夹逼', async () => {
    const wrapper = mountForm()
    await save(wrapper)

    const emits = wrapper.emitted('updateSummarizationSettings')!
    expect(emits).toHaveLength(1)
    expect(emits![0]![0]).toEqual({
      chunk_target_duration_sec: 20 * 60,
      chunk_min_duration_sec: 10 * 60,
      chunk_max_duration_sec: 30 * 60,
      boundary_jump_sec: 10,
      auto_chunk_min_audio_duration_sec: 40 * 60,
      auto_chunk_min_transcript_lines: 1800,
      max_agent_value_chars: 500,
      fallback_to_standard_on_agent_error: true,
    })
  })

  it('低于 100 的引用上限被夹逼到 100（唯一保留夹逼）', async () => {
    const wrapper = mountForm()
    const inputs = findNumberInputs(wrapper)
    await inputs[6]!.setValue(10)
    await save(wrapper)

    const emits = wrapper.emitted('updateSummarizationSettings')!
    expect(emits![0]![0]).toMatchObject({ max_agent_value_chars: 100 })
  })

  it('保存中禁用保存按钮', () => {
    const wrapper = mountForm({ isUpdatingSummarizationSettings: true })
    const button = wrapper.findAll('button').find((b) => b.text().includes('保存'))!
    expect((button.element as HTMLButtonElement).disabled).toBe(true)
  })
})
