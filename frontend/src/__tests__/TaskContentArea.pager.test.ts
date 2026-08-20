/**
 * 一P一页分页重构：TaskContentArea 分页器 UI
 *
 * - 分页器仅 task.has_parts 且 multipartPageCount > 0（= parts 数）时显示；
 * - ◀ 第 x / N 页 ▶ + 输入框跳转（1-based 分P号，Enter 生效，clamp 到 0..N-1）；
 * - 边界（首页/末页）按钮 disabled；
 * - 总览段 + 当前分P页 summary 拼接的 compiledMarkdown 由唯一 MarkdownContent
 *   统一渲染（独立分P v-html 容器与 "P{x} 分P总结" 小标题已移除；时间芯片
 *   命中 article.markdown-theme-container 作用域，样式不再丢失）；
 * - 分页器位于 MarkdownContent 内容下方；
 * - 分P 处理中 → "正在处理中"占位；无内容已完成 → "暂无可展示内容"占位（分页器附近）；
 * - 总览区常驻（无"展开完整分P总结"按钮）。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import TaskContentArea from '../components/TaskContentArea.vue'
import type { Task, TaskPart } from '../types'

// TaskContentArea 内部 MarkdownContent 走 compileMarkdownText 管线依赖 DOMPurify；
// 与 App.p1Defenses.test.ts 同款行为双（happy-dom 与全量算法不兼容）
vi.mock('dompurify', () => {
  const stripEventHandlers = (html: string) =>
    String(html)
      .replace(/\son\w+=["'][^"']*["']/g, '')
      .replace(/\sstyle=(["'])[^"']*\1/g, '')
  return {
    default: { sanitize: vi.fn(stripEventHandlers) },
  }
})

const makeTask = (overrides: Partial<Task>): Task => ({
  id: 'task-1',
  video_url: 'https://example.com/video.mp4',
  status: 'COMPLETED',
  progress: 1.0,
  created_at: '2026-08-08T10:00:00Z',
  ...overrides,
})

const makePart = (partIndex: number, overrides: Partial<TaskPart> = {}): TaskPart => ({
  task_id: 'task-1',
  part_index: partIndex,
  status: 'COMPLETED',
  progress: 1,
  duration: 60,
  title: `P${partIndex + 1}`,
  ...overrides,
})

const mountArea = (overrides: {
  task?: Task
  compiledMarkdown?: string
  multipartPage?: number
  multipartPageCount?: number
  multipartPagePart?: TaskPart | null
} = {}) => {
  return mount(TaskContentArea, {
    props: {
      task: overrides.task ?? makeTask({ summary: '总览' }),
      activeTab: 'summary' as const,
      compiledMarkdown: overrides.compiledMarkdown ?? '<p>总览内容</p>',
      multipartPage: overrides.multipartPage ?? 0,
      multipartPageCount: overrides.multipartPageCount ?? 0,
      multipartPagePart: overrides.multipartPagePart ?? null,
      topic: '',
      isEditingTopic: false,
      editingTopicValue: '',
    },
  })
}

describe('TaskContentArea：多P 一P一页分页器', () => {
  it('非多P任务：不显示分页器，总览区常驻，无"展开完整分P总结"按钮', () => {
    const wrapper = mountArea({ task: makeTask({ summary: '普通总结' }) })
    expect(wrapper.find('[data-testid="multipart-pager"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('展开完整分P总结')
    wrapper.unmount()
  })

  it('多P任务但 multipartPageCount=0（parts 未加载）：不显示分页器', () => {
    const wrapper = mountArea({ task: makeTask({ has_parts: true, summary: '总览' }) })
    expect(wrapper.find('[data-testid="multipart-pager"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('多P任务：显示分页器"第 x / N 页"（一P一页），无"展开完整分P总结"按钮', () => {
    const wrapper = mountArea({
      task: makeTask({ has_parts: true, summary: '总览' }),
      multipartPageCount: 3,
      multipartPage: 1,
    })
    const pager = wrapper.find('[data-testid="multipart-pager"]')
    expect(pager.exists()).toBe(true)
    expect(pager.text()).toContain('第 2 / 3 页')
    expect(wrapper.text()).not.toContain('展开完整分P总结')
    expect(wrapper.text()).not.toContain('每页 10 个 P')
    wrapper.unmount()
  })

  it('边界：首页上一页 disabled，末页下一页 disabled', async () => {
    const wrapper = mountArea({
      task: makeTask({ has_parts: true, summary: '总览' }),
      multipartPageCount: 2,
      multipartPage: 0,
    })
    expect(wrapper.find('[data-testid="multipart-pager-prev"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="multipart-pager-next"]').attributes('disabled')).toBeUndefined()

    await wrapper.find('[data-testid="multipart-pager-next"]').trigger('click')
    expect(wrapper.emitted('change-multipart-page')![0]).toEqual([1])

    await wrapper.setProps({ multipartPage: 1 })
    expect(wrapper.find('[data-testid="multipart-pager-next"]').attributes('disabled')).toBeDefined()
    await wrapper.find('[data-testid="multipart-pager-prev"]').trigger('click')
    expect(wrapper.emitted('change-multipart-page')![1]).toEqual([0])

    wrapper.unmount()
  })

  it('输入框跳转：Enter 跳转且 clamp 到 0..N-1（0/-1/超界）', async () => {
    const wrapper = mountArea({
      task: makeTask({ has_parts: true, summary: '总览' }),
      multipartPageCount: 3,
    })
    const input = wrapper.find('[data-testid="multipart-pager-input"]')
    expect(input.exists()).toBe(true)

    const lastEmitted = () => {
      const calls = wrapper.emitted('change-multipart-page')!
      return calls[calls.length - 1]
    }

    // 正常分P号（1-based）：输入 3 → 第 3 页（index 2）
    await input.setValue('3')
    await input.trigger('keydown', { key: 'Enter' })
    expect(lastEmitted()).toEqual([2])

    // 0 / -1 → clamp 到首页（index 0）
    await input.setValue('0')
    await input.trigger('keydown', { key: 'Enter' })
    expect(lastEmitted()).toEqual([0])
    await input.setValue('-1')
    await input.trigger('keydown', { key: 'Enter' })
    expect(lastEmitted()).toEqual([0])

    // 超界 → clamp 到末页（index 2）
    await input.setValue('99')
    await input.trigger('keydown', { key: 'Enter' })
    expect(lastEmitted()).toEqual([2])

    wrapper.unmount()
  })

  it('输入非数字/空：忽略，不发跳转', async () => {
    const wrapper = mountArea({
      task: makeTask({ has_parts: true, summary: '总览' }),
      multipartPageCount: 3,
    })
    const input = wrapper.find('[data-testid="multipart-pager-input"]')

    await input.setValue('abc')
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('change-multipart-page')).toBeUndefined()

    await input.setValue('')
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('change-multipart-page')).toBeUndefined()

    wrapper.unmount()
  })

  it('combined 编译产物由 MarkdownContent 统一渲染：独立分P容器与"P{x} 分P总结"小标题已移除', () => {
    const wrapper = mountArea({
      task: makeTask({ has_parts: true, summary: '总览' }),
      multipartPageCount: 1,
      compiledMarkdown:
        '<p>总览内容</p><hr><p>P1的总结</p><span class="ss-time-jump-chip"><svg class="ss-time-chip-icon"></svg>见 00:12</span>',
    })
    // 独立 v-html 容器（data-testid="multipart-page-content"）与 "P{x} 分P总结" 小标题不得存在
    expect(wrapper.find('[data-testid="multipart-page-content"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('P1 分P总结')
    // combined 内容（含分P页与分隔符 <hr>）在 MarkdownContent 的 article.markdown-theme-container 内渲染
    const article = wrapper.find('article.markdown-theme-container')
    expect(article.exists()).toBe(true)
    const rendered = article.find('[data-summary-content]')
    expect(rendered.html()).toContain('P1的总结')
    expect(rendered.html()).toContain('<hr')
    // 时间芯片（.ss-time-jump-chip / .ss-time-chip-icon）位于 markdown-theme-container
    // 作用域内 —— base.css 样式（0.7em 图标尺寸等）可命中，不再出现"过大、对不齐"
    expect(article.find('.ss-time-jump-chip').exists()).toBe(true)
    expect(article.find('.ss-time-chip-icon').exists()).toBe(true)
    wrapper.unmount()
  })

  it('分页器位于 MarkdownContent 内容下方（内容之下，非独立容器）', () => {
    const wrapper = mountArea({
      task: makeTask({ has_parts: true, summary: '总览' }),
      multipartPageCount: 2,
      compiledMarkdown: '<p>总览内容</p>',
    })
    const nodes = wrapper.findAll('article.markdown-theme-container, [data-testid="multipart-pager"]')
    const articleIdx = nodes.findIndex((n) => n.classes().includes('markdown-theme-container'))
    const pagerIdx = nodes.findIndex((n) => n.attributes('data-testid') === 'multipart-pager')
    expect(articleIdx).toBeGreaterThanOrEqual(0)
    expect(pagerIdx).toBeGreaterThanOrEqual(0)
    expect(articleIdx).toBeLessThan(pagerIdx)
    wrapper.unmount()
  })

  it('占位：分P 处理中 → "正在处理中"；已完成但无内容 → "暂无可展示内容"', () => {
    const processing = mountArea({
      task: makeTask({ has_parts: true, summary: '总览' }),
      multipartPageCount: 1,
      multipartPagePart: makePart(0, { status: 'TRANSCRIBING', summary: undefined }),
    })
    const processingPlaceholder = processing.find('[data-testid="multipart-page-placeholder"]')
    expect(processingPlaceholder.exists()).toBe(true)
    expect(processingPlaceholder.text()).toContain('正在处理中')
    processing.unmount()

    const empty = mountArea({
      task: makeTask({ has_parts: true, summary: '总览' }),
      multipartPageCount: 1,
      multipartPagePart: makePart(0, { status: 'COMPLETED', summary: undefined }),
    })
    const emptyPlaceholder = empty.find('[data-testid="multipart-page-placeholder"]')
    expect(emptyPlaceholder.exists()).toBe(true)
    expect(emptyPlaceholder.text()).toContain('暂无可展示内容')
    empty.unmount()
  })

  it('当前页有内容时不再显示占位', () => {
    const wrapper = mountArea({
      task: makeTask({ has_parts: true, summary: '总览' }),
      multipartPageCount: 1,
      compiledMarkdown: '<p>总览内容</p><hr><p>有内容</p>',
      multipartPagePart: makePart(0, { summary: '有内容' }),
    })
    expect(wrapper.find('[data-testid="multipart-page-placeholder"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('转录 tab：分页器在 summary 专属 v-show 容器内（activeTab 切换即隐藏）', () => {
    const wrapper = mount(TaskContentArea, {
      props: {
        task: makeTask({ has_parts: true, summary: '总览' }),
        activeTab: 'transcript' as const,
        compiledMarkdown: '<p>总览</p>',
        multipartPage: 0,
        multipartPageCount: 2,
        multipartPagePart: null,
        topic: '',
        isEditingTopic: false,
        editingTopicValue: '',
      },
    })
    const pager = wrapper.find('[data-testid="multipart-pager"]')
    expect(pager.exists()).toBe(true)
    // 分页器位于 v-show="activeTab === 'summary'" 容器内：转录 tab 下容器为
    // display:none（happy-dom 无法用 isVisible 判断 v-show，改查内联样式）
    expect(pager.element.closest('[style*="display: none"]')).not.toBeNull()
    wrapper.unmount()
  })
})
