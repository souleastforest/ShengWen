/**
 * FloatingToolbar "AI 总结"菜单的补总结入口测试。
 *
 * 回归背景（bugfix: resummarize-entry-for-transcript-only）：
 * 旧代码用 `selectedTask?.transcript` 做"AI 重新总结"按钮守卫，而轻量详情
 * （include_content=false）恒剥离 transcript、懒加载只在"原文"tab 触发——
 * 选中任务停在"总结"tab 时 transcript 恒为 null → 仅转录任务（无 summary）
 * 无任何"从原文跑总结"入口，hover 只剩复制/下载 Markdown。
 *
 * 修复后：
 * - 无总结的已完成/失败任务 → 显示"生成 AI 总结（标准）"与"生成 AI 总结（Agent）"
 *   （显式模式，满足"仅转录后想按 Agent 模式补总结"）
 * - 有总结的任务 → 仍显示"AI 重新总结"（沿用全局模式，emit 不带参数）
 * - 处理中任务 → 不显示任何总结入口
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import FloatingToolbar from '../components/FloatingToolbar.vue'
import { TaskStatus, type Task } from '../types'

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-a',
    video_url: 'https://www.bilibili.com/video/BV1xx411c7mD',
    status: TaskStatus.COMPLETED,
    progress: 100,
    created_at: '2026-08-10 00:00:00',
    summary_mode: 'none',
    ...overrides,
  }
}

function mountToolbar(task: Task | null) {
  return mount(FloatingToolbar, {
    props: {
      selectedTask: task,
      isSidebarOpen: false,
      headings: [],
      activeHeadingId: undefined,
      activeTab: 'summary',
    },
    attrs: {
      'onUpdate:activeTab': () => {},
    },
  })
}

describe('FloatingToolbar 补总结入口（transcript 懒加载盲区回归）', () => {
  it('仅转录任务（COMPLETED + 无 summary + transcript=null）显示"生成 AI 总结"双模式入口', () => {
    const wrapper = mountToolbar(
      makeTask({ summary: undefined, transcript: undefined, summary_mode: 'none' }),
    )
    const buttons = wrapper.findAll('button').map((b) => b.text())
    expect(buttons).toContain('生成 AI 总结（标准）')
    expect(buttons).toContain('生成 AI 总结（Agent）')
    expect(buttons).not.toContain('AI 重新总结')
  })

  it('无总结失败任务同样显示"生成 AI 总结"入口', () => {
    const wrapper = mountToolbar(
      makeTask({ status: TaskStatus.FAILED, summary: undefined, transcript: undefined }),
    )
    const texts = wrapper.findAll('button').map((b) => b.text())
    expect(texts).toContain('生成 AI 总结（标准）')
    expect(texts).toContain('生成 AI 总结（Agent）')
  })

  it('有总结的已完成任务仍显示"AI 重新总结"（不回归）', () => {
    const wrapper = mountToolbar(makeTask({ summary: '已有总结内容', transcript: '转录内容' }))
    const texts = wrapper.findAll('button').map((b) => b.text())
    expect(texts).toContain('AI 重新总结')
    expect(texts).not.toContain('生成 AI 总结（标准）')
    expect(texts).not.toContain('生成 AI 总结（Agent）')
  })

  it('处理中任务不显示任何总结入口', () => {
    const wrapper = mountToolbar(
      makeTask({ status: TaskStatus.TRANSCRIBING, summary: undefined }),
    )
    const texts = wrapper.findAll('button').map((b) => b.text())
    expect(texts).not.toContain('AI 重新总结')
    expect(texts).not.toContain('生成 AI 总结（标准）')
    expect(texts).not.toContain('生成 AI 总结（Agent）')
  })

  it('点击"生成 AI 总结（Agent）"emit reSummarize("agent")', async () => {
    const wrapper = mountToolbar(makeTask({ summary: undefined }))
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '生成 AI 总结（Agent）')!
      .trigger('click')
    expect(wrapper.emitted('reSummarize')).toEqual([['agent']])
  })

  it('点击"生成 AI 总结（标准）"emit reSummarize("standard")', async () => {
    const wrapper = mountToolbar(makeTask({ summary: undefined }))
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '生成 AI 总结（标准）')!
      .trigger('click')
    expect(wrapper.emitted('reSummarize')).toEqual([['standard']])
  })

  it('点击"AI 重新总结"emit reSummarize 不带模式（沿用全局模式）', async () => {
    const wrapper = mountToolbar(makeTask({ summary: '已有总结' }))
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'AI 重新总结')!
      .trigger('click')
    // Vue 未传参数的 emit 记录为空参数列表 → 上层 $event 为 undefined → 沿用全局模式
    expect(wrapper.emitted('reSummarize')).toEqual([[]])
  })

  it('未选中任务时不显示任何总结入口', () => {
    const wrapper = mountToolbar(null)
    const texts = wrapper.findAll('button').map((b) => b.text())
    expect(texts).not.toContain('AI 重新总结')
    expect(texts).not.toContain('生成 AI 总结（标准）')
    expect(texts).not.toContain('生成 AI 总结（Agent）')
  })
})
