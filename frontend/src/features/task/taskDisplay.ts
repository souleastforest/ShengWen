/**
 * 任务展示辅助（从 Sidebar.vue 拆出的纯函数，行为逐行等价）。
 * TaskList / TaskSearch 共用，避免双入口漂移。
 */
import { TaskStatus, type Task } from '../../types'
import { getStatusLabel } from '../../shared/utils/taskStatus'

export type SearchMatchSource = 'topic' | 'summary'

export type MatchPreview = {
  hasHit: boolean
  before: string
  hit: string
  after: string
  leftEllipsis: boolean
  rightEllipsis: boolean
}

/**
 * 状态标签（含分P/ASR 分片/总结分块计数，与拆片前 Sidebar 单一实现逐行等价）。
 * 注意：TRANSCRIBING 的 ASR 分支（asr_chunk_total > 0 → "转录中 (x/y)"）与
 * SUMMARIZING 的总结分块分支语义独立，TaskList/TaskSearch 必须共用本函数
 * 防止视图漂移（对抗评审 P1-1）。
 */
export const getTaskStatusLabel = (task: Task) => {
  const base = getStatusLabel(task.status)
  const total = Number(task.part_count || 0)
  const done = Number(task.part_completed || 0)
  const failed = Number(task.part_failed || 0)
  if (total > 0 && (task.status === TaskStatus.PARTIAL || task.status === TaskStatus.COMPLETED || task.status === TaskStatus.DOWNLOADING || task.status === TaskStatus.TRANSCRIBING || task.status === TaskStatus.SUMMARIZING)) {
    return failed > 0 ? base + ' (' + done + '/' + total + '，失败 ' + failed + ')' : base + ' (' + done + '/' + total + ')'
  }
  // ASR 分片计数（仅转录阶段；与总结分块 summary_chunk_* 语义独立）：
  // 长音频 10 个 6min 分片 → "转录中 (3/10)"。done||0 兜底、total>0 才显示。
  if (task.status === TaskStatus.TRANSCRIBING) {
    const asrTotal = Number(task.asr_chunk_total || 0)
    if (asrTotal > 0) {
      const asrDone = Number(task.asr_chunk_done || 0)
      return '转录中 (' + Math.min(asrDone, asrTotal) + '/' + asrTotal + ')'
    }
  }
  if (task.status !== TaskStatus.SUMMARIZING) return base
  const summaryTotal = Number(task.summary_chunk_total || 0)
  const summaryDone = Number(task.summary_chunk_done || 0)
  return summaryTotal > 0 ? '总结中 (' + Math.min(summaryDone, summaryTotal) + '/' + summaryTotal + ')' : base
}

/** 任务主题解析：topic → 总结中的 {{topic}} 占位 → title → video_url */
export const resolveTaskTopic = (task: Task) => {
  return task.topic || (task.summary && task.summary.match(/\{\{topic:?\s*(.*?)\}\}/i)?.[1]) || task.title || task.video_url
}

export const formatTaskDate = (dateString: string) => {
  const date = new Date(dateString)
  const now = new Date()
  const isToday = date.toDateString() === now.toDateString()

  if (isToday) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

export const formatTaskDateTime = (dateString: string) => {
  const date = new Date(dateString)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString([], {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

export const emptyPreview = (): MatchPreview => ({
  hasHit: false,
  before: '',
  hit: '',
  after: '',
  leftEllipsis: false,
  rightEllipsis: false,
})

/** 构建关键词命中预览（含前后文切片与省略号标记） */
export const buildMatchPreview = (text: string, keyword: string, context = 18): MatchPreview => {
  const source = (text || '').replace(/\s+/g, ' ').trim()
  const q = keyword.trim()
  if (!source || !q) return emptyPreview()

  const sourceLower = source.toLowerCase()
  const qLower = q.toLowerCase()
  const idx = sourceLower.indexOf(qLower)
  if (idx < 0) return emptyPreview()

  const start = Math.max(0, idx - context)
  const end = Math.min(source.length, idx + q.length + context)
  return {
    hasHit: true,
    before: source.slice(start, idx),
    hit: source.slice(idx, idx + q.length),
    after: source.slice(idx + q.length, end),
    leftEllipsis: start > 0,
    rightEllipsis: end < source.length,
  }
}

/** 构建"修改于"标签（与创建时间几乎一致则不显示） */
export const buildModifiedInfo = (task: Task) => {
  const modifiedRaw = task.latest_modified_at
  if (!modifiedRaw) {
    return { label: '', title: '' }
  }

  const modifiedAt = new Date(modifiedRaw)
  const createdAt = new Date(task.created_at)
  if (Number.isNaN(modifiedAt.getTime()) || Number.isNaN(createdAt.getTime())) {
    return { label: '', title: '' }
  }

  // 与创建时间几乎一致则不显示，避免视觉噪音。
  if (Math.abs(modifiedAt.getTime() - createdAt.getTime()) < 60 * 1000) {
    return { label: '', title: '' }
  }

  const now = new Date()
  if (modifiedAt.toDateString() === now.toDateString()) {
    return {
      label: `改 ${modifiedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`,
      title: formatTaskDateTime(modifiedRaw)
    }
  }

  return {
    label: `改 ${modifiedAt.toLocaleDateString([], { month: '2-digit', day: '2-digit' })}`,
    title: formatTaskDateTime(modifiedRaw)
  }
}
