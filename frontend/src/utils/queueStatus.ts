import type { QueueSnapshot } from '../types'

export interface QueueInfo {
  queued: boolean
  queueName: string
  position: number
}

// 后端 worker 队列名 -> 前端展示文案
export const QUEUE_NAME_LABELS: Record<string, string> = {
  VideoDownloaderWorker: '下载队列',
  FileUploadWorker: '上传队列',
  TranscriberWorker: '转录队列',
  LLMWorker: '总结队列',
}

export const getQueueNameLabel = (queueName: string): string =>
  QUEUE_NAME_LABELS[queueName] ?? queueName

/**
 * 在队列快照中查找任务：
 * - 命中某个 queue 的 waiting_task_ids（FIFO 顺序）→ { queued: true, queueName, position: index+1 }
 * - 任务的 id 等于某个 queue 的 active_task_id（正在执行，不算排队）→ null
 * - 不在任何快照中 → null
 */
export const getQueueInfo = (
  taskId: string,
  queues: QueueSnapshot[],
): QueueInfo | null => {
  for (const queue of queues) {
    const index = queue.waiting_task_ids.indexOf(taskId)
    if (index !== -1) {
      return {
        queued: true,
        queueName: getQueueNameLabel(queue.name),
        position: index + 1,
      }
    }
  }
  return null
}
