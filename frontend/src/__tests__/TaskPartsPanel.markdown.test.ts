/**
 * P0 修复 B（含 P1-1 评审修订）：分P展开区渲染（TaskPartsPanel）
 *
 * - summary 经 compileMarkdownText 净化管线编译后 v-html 渲染
 *   （XSS 净化必须走该出口，禁止直接 v-html 插入未净化内容）；
 * - transcript 保持 whitespace-pre-wrap 纯文本（P1-1：marked 无 breaks
 *   配置会折叠单换行，且行首 #/-/** 会被误解释为 markdown——与主内容区
 *   TaskContentArea 对 transcript 的纯文本处理保持一致）。
 *
 * 每个用例失败 = 实现缺陷。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import TaskPartsPanel from '../components/TaskPartsPanel.vue'
import type { TaskPart } from '../types'

// 与 App.p1Defenses.test.ts / useMarkdownCompile.seam.test.ts 同款 DOMPurify
// 行为双（happy-dom 与全量算法不兼容）：以正则剥离事件/样式属性验证净化链路
// 被消费——若组件绕过 compileMarkdownText 直接 v-html 未净化内容，本断言失败。
vi.mock('dompurify', () => {
  const stripEventHandlers = (html: string) =>
    String(html)
      .replace(/\son\w+=["'][^"']*["']/g, '')
      .replace(/\sstyle=(["'])[^"']*\1/g, '')
  return {
    default: { sanitize: vi.fn(stripEventHandlers) },
  }
})

const makePart = (overrides: Partial<TaskPart> = {}): TaskPart => ({
  task_id: 'task-a',
  part_index: 0,
  status: 'COMPLETED',
  progress: 1,
  duration: 60,
  title: 'P1 标题',
  ...overrides,
})

const expandPart = async (wrapper: ReturnType<typeof mount>) => {
  const expandButton = wrapper.findAll('article button')[0]
  expect(expandButton).toBeTruthy()
  await expandButton!.trigger('click')
}

describe('P0 修复 B：分P展开区 Markdown 渲染', () => {
  it('summary 按 markdown 渲染：**加粗总结** → <strong>加粗总结</strong>，非纯文本', async () => {
    const wrapper = mount(TaskPartsPanel, {
      props: { parts: [makePart({ summary: '**加粗总结**' })], taskId: 'task-a' },
    })
    await expandPart(wrapper)

    expect(wrapper.html()).toContain('<strong>加粗总结</strong>')
    // 不再以纯文本展示原始标记
    expect(wrapper.text()).not.toContain('**加粗总结**')

    wrapper.unmount()
  })

  it('transcript 保持纯文本（P1-1）：不做 markdown 解释（** 不渲染为 <strong>）', async () => {
    const wrapper = mount(TaskPartsPanel, {
      props: { parts: [makePart({ transcript: '**加粗转录**' })], taskId: 'task-a' },
    })
    await expandPart(wrapper)

    // 原始标记原样透出，不得被编译为 <strong>
    expect(wrapper.text()).toContain('**加粗转录**')
    expect(wrapper.html()).not.toContain('<strong>加粗转录</strong>')

    wrapper.unmount()
  })

  it('多行转录保持逐行展示，行首 # 不误解释为标题（P1-1）', async () => {
    const transcript = '00:00:00[音乐] 开场\n# 这不是标题\n普通行内容'
    const wrapper = mount(TaskPartsPanel, {
      props: { parts: [makePart({ transcript })], taskId: 'task-a' },
    })
    await expandPart(wrapper)

    expect(wrapper.text()).toContain('00:00:00[音乐] 开场')
    expect(wrapper.text()).toContain('# 这不是标题')
    expect(wrapper.text()).toContain('普通行内容')
    expect(wrapper.html()).not.toContain('<h1')

    wrapper.unmount()
  })

  it('partDetails 覆盖列表分P：详情有总结时渲染详情内容（含 markdown）', async () => {
    const partDetails = { 0: makePart({ summary: '**详情加粗**' }) }
    const wrapper = mount(TaskPartsPanel, {
      props: {
        parts: [makePart({ summary: '列表截断版' })],
        partDetails,
        taskId: 'task-a',
      },
    })
    await expandPart(wrapper)

    expect(wrapper.html()).toContain('<strong>详情加粗</strong>')
    expect(wrapper.text()).toContain('详情加粗')

    wrapper.unmount()
  })

  it('XSS 防御：summary 携带原始 HTML（onerror 等）时经 DOMPurify 净化，不得透出', async () => {
    const wrapper = mount(TaskPartsPanel, {
      props: {
        parts: [makePart({ summary: '<img src="x" onerror="alert(1)">**正文**' })],
        taskId: 'task-a',
      },
    })
    await expandPart(wrapper)

    expect(wrapper.html()).toContain('<strong>正文</strong>')
    expect(wrapper.html()).not.toContain('onerror')

    wrapper.unmount()
  })

  it('无内容分P展开仍显示空态"暂无可展示内容"', async () => {
    const wrapper = mount(TaskPartsPanel, {
      props: { parts: [makePart({ transcript: undefined, summary: undefined })], taskId: 'task-a' },
    })
    await expandPart(wrapper)

    expect(wrapper.text()).toContain('暂无可展示内容')

    wrapper.unmount()
  })
})
