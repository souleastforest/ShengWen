/**
 * 一P一页分页重构：TaskPartsPanel 分P行点击行为
 *
 * 旧行为（已删除）：点击分P行内联展开预览（expandedPart/expandedSummaryHtml/
 * togglePart/expand emit/loadingPartIndex/refreshKey 收起逻辑）。
 * 新行为：点击分P行 emit jump(part_index)，由 App 跳转到下方 markdown 渲染器
 * 对应页；面板内不再渲染任何分P预览内容。
 *
 * 缺陷回归：点击分P行应跳转而非展开预览。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TaskPartsPanel from '../components/TaskPartsPanel.vue'
import type { TaskPart } from '../types'

const makePart = (partIndex: number, overrides: Partial<TaskPart> = {}): TaskPart => ({
  task_id: 'task-a',
  part_index: partIndex,
  status: 'COMPLETED',
  progress: 1,
  duration: 60,
  title: `P${partIndex + 1} 标题`,
  ...overrides,
})

describe('TaskPartsPanel：点击分P行跳转（移除内联展开预览）', () => {
  it('点击分P行 emit jump(part_index)，而非展开内联预览', async () => {
    const wrapper = mount(TaskPartsPanel, {
      props: { parts: [makePart(0, { summary: 'P1总结' }), makePart(1, { summary: 'P2总结' })] },
    })

    const rows = wrapper.findAll('article button')
    expect(rows.length).toBe(2)
    await rows[1]!.trigger('click')

    const jumpEmits = wrapper.emitted('jump') ?? []
    expect(jumpEmits).toHaveLength(1)
    expect(jumpEmits[0]).toEqual([1])

    // 旧"内联展开预览"分支不得残留：点击后无任何分P预览内容
    expect(wrapper.text()).not.toContain('单P总结')
    expect(wrapper.text()).not.toContain('暂无可展示内容')
    expect(wrapper.text()).not.toContain('正在加载该分P预览')
    expect(wrapper.emitted('expand')).toBeUndefined()

    wrapper.unmount()
  })

  it('点击第一行 emit jump(0)', async () => {
    const wrapper = mount(TaskPartsPanel, { props: { parts: [makePart(0)] } })
    const rows = wrapper.findAll('article button')
    await rows[0]!.trigger('click')
    const jumpEmits = wrapper.emitted('jump') ?? []
    expect(jumpEmits[0]).toEqual([0])
    wrapper.unmount()
  })

  it('行按钮 title 提示"点击跳转到该分P总结"', () => {
    const wrapper = mount(TaskPartsPanel, { props: { parts: [makePart(0)] } })
    const rows = wrapper.findAll('article button')
    expect(rows[0]!.attributes('title')).toBe('点击跳转到该分P总结')
    wrapper.unmount()
  })

  it('带 summary 的分P不做内联渲染：DOM 中无分P预览区', () => {
    const wrapper = mount(TaskPartsPanel, {
      props: { parts: [makePart(0, { summary: '**加粗总结**' })] },
    })
    // 面板内不得渲染任何分P总结 markdown（预览逻辑已删除）
    expect(wrapper.text()).not.toContain('加粗总结')
    expect(wrapper.html()).not.toContain('<strong>')
    wrapper.unmount()
  })

  it('FAILED 分P的 error_message 仍展示（不属于预览，保留）', () => {
    const wrapper = mount(TaskPartsPanel, {
      props: { parts: [makePart(0, { status: 'FAILED', error_message: '下载失败' })] },
    })
    expect(wrapper.text()).toContain('下载失败')
    wrapper.unmount()
  })

  it('parts 为空时不渲染面板', () => {
    const wrapper = mount(TaskPartsPanel, { props: { parts: [] } })
    expect(wrapper.find('section').exists()).toBe(false)
    wrapper.unmount()
  })

  it('旧 props（partDetails/loadingPartIndex/taskId/refreshKey）已删除：不传也能正常挂载', () => {
    const wrapper = mount(TaskPartsPanel, { props: { parts: [makePart(0)] } })
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.vm.$props).not.toHaveProperty('partDetails')
    expect(wrapper.vm.$props).not.toHaveProperty('taskId')
    expect(wrapper.vm.$props).not.toHaveProperty('refreshKey')
    expect(wrapper.vm.$props).not.toHaveProperty('loadingPartIndex')
    wrapper.unmount()
  })
})
