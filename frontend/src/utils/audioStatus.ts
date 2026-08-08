import { TaskStatus, type Task, type AudioMissingReason } from '../types'

export interface AudioStatusInfo {
  label: string
  badgeClass: string
  hint: string | null
  showRedownload: boolean
}

/** 终态任务（与后端回收器资格保持一致） */
export const TERMINAL_TASK_STATUSES: readonly TaskStatus[] = [
  TaskStatus.COMPLETED,
  TaskStatus.PARTIAL,
  TaskStatus.FAILED,
]

/** 本地文件任务（video_url 为 file:// 协议，无远端媒体可重新下载） */
export const isLocalFileUrl = (url: string | undefined): boolean =>
  typeof url === 'string' && url.startsWith('file://')

/**
 * 音频状态 -> 展示文案/徽章样式映射（TaskInfoModal 与 TaskMetaCard 共用，
 * 避免两处文案漂移）。徽章语义：
 * - true              -> 已下载
 * - false+subtitle_only -> 未下载 · 使用了B站字幕
 * - false+reclaimed   -> 未下载 · 已被回收清理
 * - false+无原因      -> 未下载
 * - null/undefined    -> 未知（存量任务或字段缺失）
 */
export const getAudioStatusInfo = (
  audioDownloaded: boolean | undefined,
  audioMissingReason: AudioMissingReason | undefined,
): AudioStatusInfo => {
  if (audioDownloaded === true) {
    return {
      label: '已下载',
      badgeClass: 'text-emerald-600 bg-emerald-50',
      hint: '音频文件已下载到服务器',
      showRedownload: false,
    }
  }

  if (audioDownloaded === false) {
    if (audioMissingReason === 'subtitle_only') {
      return {
        label: '未下载 · 使用了B站字幕',
        badgeClass: 'text-blue-600 bg-blue-50',
        hint: '该任务未下载音频，直接使用了 B 站字幕进行转录',
        showRedownload: true,
      }
    }
    if (audioMissingReason === 'reclaimed') {
      return {
        label: '未下载 · 已被回收清理',
        badgeClass: 'text-amber-600 bg-amber-50',
        hint: '音频文件已被存储回收器清理，可重新下载',
        showRedownload: true,
      }
    }
    return {
      label: '未下载',
      badgeClass: 'text-blue-600 bg-blue-50',
      hint: null,
      showRedownload: true,
    }
  }

  return {
    label: '未知',
    badgeClass: 'text-slate-600 bg-slate-50',
    hint: null,
    showRedownload: false,
  }
}

/**
 * 是否允许重新下载音频（TaskInfoModal 按钮与 FloatingToolbar 菜单项共用）：
 * - audio_downloaded === false（有可重新下载的目标）
 * - 非 file:// 本地任务（本地直读任务无远端媒体）
 * - 任务处于终态（COMPLETED / PARTIAL / FAILED）
 */
export const canReDownloadAudio = (
  task: Pick<Task, 'audio_downloaded' | 'video_url' | 'status'>,
): boolean => {
  if (task.audio_downloaded !== false) return false
  if (isLocalFileUrl(task.video_url)) return false
  return TERMINAL_TASK_STATUSES.includes(task.status)
}
