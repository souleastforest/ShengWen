/**
 * 一P一页分页重构：TaskContentArea 分页器 UI
 *
 * - 分页器仅 task.has_parts 且 multipartPageCount > 0（= parts 数）时显示；
 * - ◀ 第 x / N 页 ▶ + 输入框跳转（1-based 分P号，Enter 生效，clamp 到 0..N-1）；
 * - 边界（首页/末页）按钮 disabled；
 * - 当前分P页内容 v-html 渲染 pageCompiledMarkdown；
 * - 分P 处理中 → "正在处理中"占位；无内容已完成 → "暂无可展示内容"占位；
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
  overviewCompiledMarkdown?: string
  pageCompiledMarkdown?: string
  multipartPage?: number
  multipartPageCount?: number
  multipartPagePart?: TaskPart | null
} = {}) => {
  return mount(TaskContentArea, {
    props: {
      task: overrides.task ?? makeTask({ summary: '总览' }),
      activeTab: 'summary' as const,
      overviewCompiledMarkdown: overrides.overviewCompiledMarkdown ?? '<p>总览内容</p>',
      pageCompiledMarkdown: overrides.pageCompiledMarkdown ?? '',
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

  it('当前页内容：pageCompiledMarkdown 经 v-html 渲染（markdown 产物透出）', () => {
    const wrapper = mountArea({
      task: makeTask({ has_parts: true, summary: '总览' }),
      multipartPageCount: 1,
      pageCompiledMarkdown: '<p><strong>P1总结</strong></p>',
    })
    const content = wrapper.find('[data-testid="multipart-page-content"]')
    expect(content.exists()).toBe(true)
    expect(content.html()).toContain('<strong>P1总结</strong>')
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
      pageCompiledMarkdown: '<p>有内容</p>',
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
        overviewCompiledMarkdown: '<p>总览</p>',
        pageCompiledMarkdown: '',
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
