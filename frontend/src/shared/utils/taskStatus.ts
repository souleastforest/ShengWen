/**
 * 任务 / 分P 状态 → 展示文案、样式类、图标的单源映射（P3 收敛）。
 *
 * 所有消费点（Sidebar / TaskInfoModal / TaskPartsPanel）统一引用本模块，
 * 禁止在组件内各自维护状态映射表（避免漂移）。
 *
 * 词汇表（两类，语义不同，勿互相合并）：
 * - 任务级：TaskStatus（types.ts）8 个枚举值，getStatusLabel/getStatusClass/getStatusIcon；
 * - 分P 级：后端 task_parts.PART_STATUSES（PENDING / DOWNLOADING / TRANSCRIBING /
 *   SUMMARIZING / COMPLETED / FAILED）＋ 历史遗留 'PROCESSING' 兜底（后端已不再产出，
 *   保留映射防旧数据回归），getPartStatusLabel/getPartStatusClass。
 *   注意分P 的 COMPLETED 文案为「已完成」（任务级为「完成」），下载/转录/总结聚合为「处理中」——
 *   均为 P3 前旧 TaskPartsPanel 的既有输出，逐值保持。
 */
import { PhCheckCircle, PhXCircle, PhInfo, PhClock, PhSpinner } from '@phosphor-icons/vue'
import { TaskStatus } from '../../types'

/** 任务状态 → 中文文案。未知状态原样返回（与旧实现一致，供运行期兜底）。 */
export const getStatusLabel = (status: TaskStatus): string => {
  switch (status) {
    case TaskStatus.COMPLETED: return '完成'
    case TaskStatus.FAILED: return '失败'
    case TaskStatus.PARTIAL: return '部分完成'
    case TaskStatus.PENDING: return '等待中'
    case TaskStatus.DOWNLOADING: return '下载中'
    case TaskStatus.UPLOADING: return '上传中'
    case TaskStatus.TRANSCRIBING: return '转录中'
    case TaskStatus.SUMMARIZING: return '总结中'
    default: return status
  }
}

/** 任务状态 → Tailwind 徽标样式类。进行中状态（下载/上传/转录/总结）统一蓝色。 */
export const getStatusClass = (status: TaskStatus): string => {
  switch (status) {
    case TaskStatus.COMPLETED: return 'text-emerald-600 bg-emerald-50'
    case TaskStatus.FAILED: return 'text-red-600 bg-red-50'
    case TaskStatus.PARTIAL: return 'text-amber-600 bg-amber-50'
    case TaskStatus.PENDING: return 'text-slate-400 bg-slate-50'
    default: return 'text-blue-600 bg-blue-50'
  }
}

/** 任务状态 → 图标。进行中状态统一转圈图标。 */
export const getStatusIcon = (status: TaskStatus) => {
  switch (status) {
    case TaskStatus.COMPLETED: return PhCheckCircle
    case TaskStatus.FAILED: return PhXCircle
    case TaskStatus.PARTIAL: return PhInfo
    case TaskStatus.PENDING: return PhClock
    default: return PhSpinner
  }
}

/** 分P 状态 → 中文文案。下载/转录/总结聚合为「处理中」；未知状态「等待中」（与旧实现一致）。 */
export const getPartStatusLabel = (status: string): string => {
  switch (status) {
    case TaskStatus.COMPLETED: return '已完成'
    case TaskStatus.FAILED: return '失败'
    case 'PROCESSING': // 历史遗留状态（后端 PART_STATUSES 已不再产出），保留映射防回归
    case TaskStatus.DOWNLOADING:
    case TaskStatus.TRANSCRIBING:
    case TaskStatus.SUMMARIZING: return '处理中'
    default: return '等待中'
  }
}

/** 分P 状态 → Tailwind 徽标样式类。进行中状态统一蓝色。 */
export const getPartStatusClass = (status: string): string => {
  if (status === TaskStatus.COMPLETED) return 'text-emerald-600 bg-emerald-50'
  if (status === TaskStatus.FAILED) return 'text-red-600 bg-red-50'
  if (status === TaskStatus.PENDING) return 'text-slate-400 bg-slate-50'
  return 'text-blue-600 bg-blue-50'
}
