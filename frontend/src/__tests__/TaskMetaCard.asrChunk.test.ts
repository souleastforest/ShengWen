/**
 * 对抗性测试：TaskMetaCard 任务统计 — 转录分片进度展示
 *
 * 需求：转录阶段（status===TRANSCRIBING 且 asr_chunk_total>0）显示"转录分片 done/total"，
 * 与"分块进度"（总结分块 summary_chunk_*）并列但语义独立——总结分块标签保持原名。
 * done||0 兜底、total>0 才显示。
 * 失败 = 实现缺陷。
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TaskMetaCard from '../components/TaskMetaCard.vue'
import type { Task } from '../types'

const makeTask = (overrides: Partial<Task>): Task => ({
  id: 'task-1',
  video_url: 'https://example.com/video',
  status: 'TRANSCRIBING',
  created_at: '2026-08-08T10:00:00Z',
  progress: 30,
  audio_duration: 600,
  transcription_time: 60,
  ...overrides,
})

const mountCard = (task: Task) => {
  return mount(TaskMetaCard, {
    props: {
      task,
      topic: '测试主题',
    },
  })
}

describe('TaskMetaCard 任务统计：转录分片', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('TRANSCRIBING 且 asr_chunk_total>0：显示"转录分片 done/total"', () => {
    const wrapper = mountCard(makeTask({ asr_chunk_total: 10, asr_chunk_done: 4 }))
    expect(wrapper.text()).toContain('转录分片')
    expect(wrapper.text()).toContain('4/10')
  })

  it('done 缺省（undefined/null）时按 0 兜底显示', () => {
    const wrapper = mountCard(makeTask({ asr_chunk_total: 3, asr_chunk_done: undefined }))
    expect(wrapper.text()).toContain('转录分片')
    expect(wrapper.text()).toContain('0/3')
  })

  it('done 超过 total 时按 total 截断（与 Sidebar 对齐）', () => {
    const wrapper = mountCard(makeTask({ asr_chunk_total: 10, asr_chunk_done: 12 }))
    expect(wrapper.text()).toContain('转录分片')
    expect(wrapper.text()).toContain('10/10')
  })

  it('multipart 父任务（has_parts）不显示转录分片（分P进度由 Sidebar part 分支展示）', () => {
    const wrapper = mountCard(
      makeTask({ asr_chunk_total: 10, asr_chunk_done: 6, has_parts: true }),
    )
    expect(wrapper.text()).not.toContain('转录分片')
  })

  it('非转录阶段（如 COMPLETED）即使有分片计数也不显示', () => {
    const wrapper = mountCard(
      makeTask({ status: 'COMPLETED', progress: 100, asr_chunk_total: 10, asr_chunk_done: 10 }),
    )
    expect(wrapper.text()).not.toContain('转录分片')
  })

  it('TRANSCRIBING 但 total 为 0/缺省：不显示转录分片', () => {
    const wrapper = mountCard(makeTask({}))
    expect(wrapper.text()).not.toContain('转录分片')
  })

  it('与"分块进度"（总结分块）语义独立并存', () => {
    const wrapper = mountCard(
      makeTask({
        asr_chunk_total: 10,
        asr_chunk_done: 6,
        summary_chunk_total: 5,
        summary_chunk_done: 2,
      }),
    )
    const text = wrapper.text()
    expect(text).toContain('转录分片')
    expect(text).toContain('6/10')
    expect(text).toContain('分块进度')
    expect(text).toContain('2/5')
  })
})
