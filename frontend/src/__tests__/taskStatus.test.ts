/**
 * taskStatus 单源映射测试（P3 收敛）
 *
 * 背景：Sidebar / TaskInfoModal 各自维护一份任务状态映射（label/class/icon），
 * TaskPartsPanel 维护一份分P状态映射——P3 收敛为 src/shared/utils/taskStatus.ts 单源。
 *
 * 验收（行为保持）：
 * 1. 遍历 TaskStatus 全部枚举值，三函数（label/class/icon）均有映射——新增枚举值未映射即红；
 * 2. 每个枚举值的输出与 P3 前各消费点的旧实现逐值相等（对比断言：旧实现输出作为基准字面量）；
 * 3. 分P词汇表（后端 PART_STATUSES + 历史遗留 PROCESSING）与旧 TaskPartsPanel 输出逐值相等；
 * 4. 未知状态兜底行为与旧实现一致。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import {
  getStatusLabel,
  getStatusClass,
  getStatusIcon,
  getPartStatusLabel,
  getPartStatusClass,
} from '../shared/utils/taskStatus'
import { TaskStatus, type Task } from '../types'
import {
  PhCheckCircle,
  PhXCircle,
  PhInfo,
  PhClock,
  PhSpinner,
} from '@phosphor-icons/vue'
import TaskInfoModal from '../components/TaskInfoModal.vue'
import TaskPartsPanel from '../components/TaskPartsPanel.vue'

const ALL_STATUSES = Object.values(TaskStatus)

// ---- 对比基准：P3 前旧实现的实际输出（行为等价验证的基准快照） ----
// 旧 Sidebar.vue:435-447 / TaskInfoModal.vue:43-55（两份逐字相同）
const OLD_TASK_LABELS: Record<string, string> = {
  PENDING: '等待中',
  DOWNLOADING: '下载中',
  UPLOADING: '上传中',
  TRANSCRIBING: '转录中',
  SUMMARIZING: '总结中',
  COMPLETED: '完成',
  FAILED: '失败',
  PARTIAL: '部分完成',
}
// 旧 Sidebar.vue:483-491 / TaskInfoModal.vue:57-65（两份逐字相同）
const OLD_TASK_CLASSES: Record<string, string> = {
  COMPLETED: 'text-emerald-600 bg-emerald-50',
  FAILED: 'text-red-600 bg-red-50',
  PARTIAL: 'text-amber-600 bg-amber-50',
  PENDING: 'text-slate-400 bg-slate-50',
}
const OLD_TASK_BLUE_CLASS = 'text-blue-600 bg-blue-50'
// 旧 Sidebar.vue:504-512
const OLD_TASK_ICONS: Record<string, unknown> = {
  COMPLETED: PhCheckCircle,
  FAILED: PhXCircle,
  PARTIAL: PhInfo,
  PENDING: PhClock,
}
const OLD_TASK_SPINNER_ICON = PhSpinner
// 旧 TaskPartsPanel.vue:44-61（分P词汇表）
const OLD_PART_LABELS: Record<string, string> = {
  COMPLETED: '已完成',
  FAILED: '失败',
  PROCESSING: '处理中',
  DOWNLOADING: '处理中',
  TRANSCRIBING: '处理中',
  SUMMARIZING: '处理中',
}
const OLD_PART_LABEL_DEFAULT = '等待中'
const OLD_PART_CLASSES: Record<string, string> = {
  COMPLETED: 'text-emerald-600 bg-emerald-50',
  FAILED: 'text-red-600 bg-red-50',
  PENDING: 'text-slate-400 bg-slate-50',
}
const OLD_PART_CLASS_DEFAULT = 'text-blue-600 bg-blue-50'

describe('taskStatus 单源映射：任务级（TaskStatus 枚举驱动）', () => {
  it('全枚举覆盖：每个枚举值均有 label/class/icon 映射（未映射即红）', () => {
    expect(ALL_STATUSES.length).toBeGreaterThan(0)
    for (const status of ALL_STATUSES) {
      const label = getStatusLabel(status)
      const cls = getStatusClass(status)
      const icon = getStatusIcon(status)
      expect(label, `label 未映射: ${status}`).toBeTruthy()
      expect(cls, `class 未映射: ${status}`).toBeTruthy()
      expect(icon, `icon 未映射: ${status}`).toBeTruthy()
      // 已知枚举值不得原样透出原始状态字符串（新增枚举值未加映射即红）
      expect(label, `label 仍是原始状态: ${status}`).not.toBe(status)
    }
  })

  it('label：与旧 Sidebar/TaskInfoModal 实现逐值相等（行为等价）', () => {
    for (const status of ALL_STATUSES) {
      expect(getStatusLabel(status)).toBe(OLD_TASK_LABELS[status])
    }
  })

  it('class：与旧 Sidebar/TaskInfoModal 实现逐值相等（行为等价）', () => {
    for (const status of ALL_STATUSES) {
      const expected = OLD_TASK_CLASSES[status] ?? OLD_TASK_BLUE_CLASS
      expect(getStatusClass(status)).toBe(expected)
    }
  })

  it('icon：与旧 Sidebar 实现逐值相等（行为等价）', () => {
    for (const status of ALL_STATUSES) {
      const expected = OLD_TASK_ICONS[status] ?? OLD_TASK_SPINNER_ICON
      expect(getStatusIcon(status)).toBe(expected)
    }
  })

  it('未知状态兜底：label 原样返回、class 蓝色、icon 转圈（与旧实现一致）', () => {
    // 类型系统只允许 TaskStatus，此处 as 断言模拟运行期后端异常值（旧实现 default 分支行为）
    const weird = 'WEIRD_STATUS' as TaskStatus
    expect(getStatusLabel(weird)).toBe('WEIRD_STATUS')
    expect(getStatusClass(weird)).toBe(OLD_TASK_BLUE_CLASS)
    expect(getStatusIcon(weird)).toBe(OLD_TASK_SPINNER_ICON)
  })
})

describe('taskStatus 单源映射：分P级（后端 PART_STATUSES 词汇表）', () => {
  it('label：与旧 TaskPartsPanel 实现逐值相等（行为等价）', () => {
    const partStatuses = ['PENDING', 'DOWNLOADING', 'TRANSCRIBING', 'SUMMARIZING', 'COMPLETED', 'FAILED', 'PROCESSING']
    for (const status of partStatuses) {
      expect(getPartStatusLabel(status)).toBe(OLD_PART_LABELS[status] ?? OLD_PART_LABEL_DEFAULT)
    }
  })

  it('class：与旧 TaskPartsPanel 实现逐值相等（行为等价）', () => {
    const partStatuses = ['PENDING', 'DOWNLOADING', 'TRANSCRIBING', 'SUMMARIZING', 'COMPLETED', 'FAILED', 'PROCESSING']
    for (const status of partStatuses) {
      const expected = OLD_PART_CLASSES[status] ?? OLD_PART_CLASS_DEFAULT
      expect(getPartStatusClass(status)).toBe(expected)
    }
  })

  it('未知分P状态兜底：label "等待中"、class 蓝色（与旧实现一致）', () => {
    expect(getPartStatusLabel('UNKNOWN_PART_STATUS')).toBe(OLD_PART_LABEL_DEFAULT)
    expect(getPartStatusClass('UNKNOWN_PART_STATUS')).toBe(OLD_PART_CLASS_DEFAULT)
  })
})

describe('消费点接线：组件引用单源映射（mount 断言渲染结果与旧输出一致）', () => {
  const makeTask = (status: TaskStatus): Task => ({
    id: 'task-1',
    video_url: 'https://example.com/video',
    status,
    created_at: '2026-08-08T10:00:00Z',
    progress: 0,
  })

  it('TaskInfoModal 状态徽标渲染共享映射的文案', () => {
    const wrapper = mount(TaskInfoModal, {
      props: { show: true, selectedTask: makeTask('COMPLETED') },
    })
    expect(wrapper.text()).toContain(OLD_TASK_LABELS.COMPLETED)

    const partial = mount(TaskInfoModal, {
      props: { show: true, selectedTask: makeTask('PARTIAL') },
    })
    expect(partial.text()).toContain(OLD_TASK_LABELS.PARTIAL)
  })

  it('TaskPartsPanel 分P状态徽标渲染共享映射的文案', () => {
    const parts = [
      { task_id: 'task-1', part_index: 1, title: 'P1', status: 'COMPLETED', progress: 1 },
      { task_id: 'task-1', part_index: 2, title: 'P2', status: 'TRANSCRIBING', progress: 0.5 },
    ]
    const wrapper = mount(TaskPartsPanel, { props: { parts } })
    expect(wrapper.text()).toContain(OLD_PART_LABELS.COMPLETED)
    expect(wrapper.text()).toContain(OLD_PART_LABELS.TRANSCRIBING)
  })
})
