/**
 * P1 防线加固（任务切换视图状态全重置）——TaskPartsPanel
 *
 * [P1-3] expandedPart 按任务 id 变化重置：切换任务后，旧任务展开的分P详情
 * 不得继续显示在新任务的分P列表下。
 *
 * 场景：A→B 任务切换时 parts 数组可能不经过空数组（直接替换），面板保持挂载，
 * expandedPart 残留旧任务的展开索引会导致新任务对应索引的分P被意外展开。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TaskPartsPanel from '../components/TaskPartsPanel.vue'
import type { TaskPart } from '../types'

const makeParts = (taskId: string): TaskPart[] => [
  { task_id: taskId, part_index: 0, status: 'COMPLETED', progress: 1, duration: 60, title: `${taskId} P1`, summary: `${taskId} 的总结1` },
  { task_id: taskId, part_index: 1, status: 'COMPLETED', progress: 1, duration: 120, title: `${taskId} P2`, summary: `${taskId} 的总结2` },
]

describe('P1 防线加固：TaskPartsPanel expandedPart 重置', () => {
  it('任务切换（taskId 变化）后，已展开的分P详情被收起', async () => {
    const partsA = makeParts('task-a')
    const partsB = makeParts('task-b')

    const wrapper = mount(TaskPartsPanel, {
      props: { parts: partsA, taskId: 'task-a' },
    })

    // 展开任务 A 的第一个分P
    const firstPartButton = wrapper.findAll('article button')[0]
    expect(firstPartButton).toBeTruthy()
    await firstPartButton!.trigger('click')
    expect(wrapper.text()).toContain('task-a 的总结1')

    // 切换到任务 B（parts 直接替换、面板保持挂载）
    await wrapper.setProps({ parts: partsB, taskId: 'task-b' })

    // 旧任务的展开状态必须重置：不得显示任何展开的分P详情
    expect(wrapper.text()).not.toContain('task-a 的总结1')
    expect(wrapper.text()).not.toContain('task-b 的总结1')

    wrapper.unmount()
  })

  it('同一任务下（taskId 不变）保持展开状态，不因 parts 刷新收起', async () => {
    const partsA1 = makeParts('task-a')
    const partsA2 = makeParts('task-a')

    const wrapper = mount(TaskPartsPanel, {
      props: { parts: partsA1, taskId: 'task-a', refreshKey: 0 },
    })

    const firstPartButton = wrapper.findAll('article button')[0]
    expect(firstPartButton).toBeTruthy()
    await firstPartButton!.trigger('click')
    expect(wrapper.text()).toContain('task-a 的总结1')

    // 同一任务的 parts 刷新（进度更新等）：展开状态应保留
    await wrapper.setProps({ parts: partsA2 })
    expect(wrapper.text()).toContain('task-a 的总结1')

    wrapper.unmount()
  })

  it('[S-4] refreshKey 变化（重试失败分P等内容刷新信号）后收起已展开的分P', async () => {
    const partsA = makeParts('task-a')

    const wrapper = mount(TaskPartsPanel, {
      props: { parts: partsA, taskId: 'task-a', refreshKey: 0 },
    })

    // 展开第一个分P
    const firstPartButton = wrapper.findAll('article button')[0]
    expect(firstPartButton).toBeTruthy()
    await firstPartButton!.trigger('click')
    expect(wrapper.text()).toContain('task-a 的总结1')

    // 重试失败分P：refreshKey 递增 → 展开区收起（后端已置分P content 为 NULL）
    await wrapper.setProps({ refreshKey: 1 })

    expect(wrapper.text()).not.toContain('task-a 的总结1')
    expect(wrapper.text()).not.toContain('单P总结')

    wrapper.unmount()
  })
})
