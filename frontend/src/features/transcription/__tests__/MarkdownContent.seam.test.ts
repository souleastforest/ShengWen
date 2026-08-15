/**
 * P6 seam 契约测试：features/transcription/components/MarkdownContent.vue
 *
 * TaskContentArea markdown 渲染容器拆片（纯搬移，行为等价）：
 * - props: task / compiledMarkdown / activeTab / summaryHighlightRequest /
 *   headingJumpRequest / scrollContainer
 * - emits: open-mermaid-viewer / update-markdown-headings /
 *   update-active-heading-id
 * 覆盖：v-html 渲染、标题收集（slug 去重）、搜索高亮、标题跳转、XSS DEV 断言。
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import type { Task } from '../../../types'

const makeTask = (overrides: Partial<Task>): Task => ({
  id: 'task-1',
  video_url: 'https://example.com/video.mp4',
  status: 'COMPLETED',
  progress: 1.0,
  created_at: '2026-08-08T10:00:00Z',
  ...overrides,
})

const baseProps = {
  task: makeTask({ summary: '总结内容' }),
  compiledMarkdown: '<h1>标题一</h1><h2>小标题</h2>',
  activeTab: 'summary' as 'summary' | 'transcript',
  summaryHighlightRequest: null as { taskId: string; keyword: string; source: 'topic' | 'summary'; requestId: number } | null,
  headingJumpRequest: null as { id: string; requestId: number } | null,
  scrollContainer: () => null,
}

const mountContent = (overrides: Record<string, unknown> = {}) =>
  mount(MarkdownContent, {
    props: {
      ...baseProps,
      task: makeTask({ summary: '总结内容' }),
      ...overrides,
    },
  })

describe('MarkdownContent seam：渲染与标题收集', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('IntersectionObserver', class {
      observe = vi.fn()
      disconnect = vi.fn()
      unobserve = vi.fn()
      takeRecords = vi.fn()
    })
  })

  it('渲染 v-html 内容（data-summary-content）', () => {
    const wrapper = mountContent()
    expect(wrapper.find('[data-summary-content]').exists()).toBe(true)
    expect(wrapper.find('[data-summary-content]').html()).toContain('<h1>标题一</h1>')
  })

  it('无 compiledMarkdown 时显示加载/空态文案', () => {
    const wrapper = mountContent({ compiledMarkdown: '', task: makeTask({ summary: '有总结' }) })
    expect(wrapper.text()).toContain('正在加载总结预览...')

    const empty = mountContent({ compiledMarkdown: '', task: makeTask({ summary: '' }) })
    expect(empty.text()).toContain('暂无总结内容')
  })

  it('标题收集：emit update-markdown-headings（slug 生成 + 去重）', async () => {
    const wrapper = mountContent({
      compiledMarkdown: '<h1>机器学习</h1><h2>机器学习</h2><h2>第二章</h2>',
    })
    await nextTick()
    await nextTick()

    const emits = wrapper.emitted('update-markdown-headings')!
    const latest = emits[emits.length - 1]![0] as Array<{ id: string; text: string; level: number }>
    expect(latest.map((h) => h.id)).toEqual(['机器学习', '机器学习-2', '第二章'])
    expect(latest.map((h) => h.level)).toEqual([1, 2, 2])
  })

  it('transcript tab 清空标题（emit 空数组）', async () => {
    const wrapper = mountContent()
    await nextTick()
    await wrapper.setProps({ activeTab: 'transcript' })
    await nextTick()

    const emits = wrapper.emitted('update-markdown-headings')!
    expect(emits[emits.length - 1]![0]).toEqual([])
  })
})

describe('MarkdownContent seam：搜索高亮与标题跳转', () => {
  it('summaryHighlightRequest 命中后插入 mark.summary-search-highlight', async () => {
    const wrapper = mountContent({
      task: makeTask({ id: 't1', summary: '正文包含目标关键词' }),
      compiledMarkdown: '<p>正文包含目标关键词的内容</p>',
      summaryHighlightRequest: { taskId: 't1', keyword: '目标关键词', source: 'summary', requestId: 1 },
    })
    await nextTick()
    await nextTick()

    expect(wrapper.find('mark.summary-search-highlight').exists()).toBe(true)
    expect(wrapper.find('mark.summary-search-highlight').text()).toBe('目标关键词')
  })

  it('请求任务与当前任务不一致时不高亮', async () => {
    const wrapper = mountContent({
      compiledMarkdown: '<p>正文包含目标关键词</p>',
      summaryHighlightRequest: { taskId: 'other-task', keyword: '目标关键词', source: 'summary', requestId: 1 },
    })
    await nextTick()
    await nextTick()
    expect(wrapper.find('mark.summary-search-highlight').exists()).toBe(false)
  })

  it('headingJumpRequest 触发滚动跳转（scrollTo）', async () => {
    const scrollTo = vi.fn()
    const container = document.createElement('div')
    container.scrollTo = scrollTo
    const wrapper = mountContent({
      compiledMarkdown: '<h1 id="目标标题">目标标题</h1>',
      scrollContainer: () => container,
    })
    await nextTick()
    await nextTick()
    // 标题收集完成后下发跳转请求（watch 非 immediate，需在挂载后变更）
    await wrapper.setProps({ headingJumpRequest: { id: '目标标题', requestId: 1 } })
    await nextTick()

    expect(scrollTo).toHaveBeenCalled()
  })
})

describe('MarkdownContent seam：XSS 渲染层 DEV 断言', () => {
  it('compiledMarkdown 含未净化痕迹时 console.error 告警', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mountContent({
      compiledMarkdown: '<img src="x" onerror="alert(1)">',
    })
    await nextTick()
    await nextTick()

    expect(errorSpy).toHaveBeenCalled()
  })
})
