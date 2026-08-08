import { describe, expect, it } from 'vitest'
import { getQueueInfo, getQueueNameLabel } from '../utils/queueStatus'
import type { QueueSnapshot } from '../types'

const makeQueue = (overrides?: Partial<QueueSnapshot>): QueueSnapshot => ({
  name: 'VideoDownloaderWorker',
  active_task_id: null,
  queue_size: 0,
  waiting_task_ids: [],
  ...overrides,
})

const queues: QueueSnapshot[] = [
  makeQueue({
    name: 'VideoDownloaderWorker',
    active_task_id: 'task-active-dl',
    queue_size: 2,
    waiting_task_ids: ['task-w1', 'task-w2'],
  }),
  makeQueue({
    name: 'FileUploadWorker',
    active_task_id: null,
    queue_size: 1,
    waiting_task_ids: ['task-u1'],
  }),
  makeQueue({
    name: 'TranscriberWorker',
    active_task_id: 'task-active-tr',
    queue_size: 0,
    waiting_task_ids: [],
  }),
  makeQueue({
    name: 'LLMWorker',
    active_task_id: null,
    queue_size: 0,
    waiting_task_ids: [],
  }),
]

describe('getQueueInfo 排队推导', () => {
  it('命中 waiting_task_ids 时返回 queued=true、position=index+1、队列名映射', () => {
    expect(getQueueInfo('task-w1', queues)).toEqual({
      queued: true,
      queueName: '下载队列',
      position: 1,
    })
    expect(getQueueInfo('task-w2', queues)).toEqual({
      queued: true,
      queueName: '下载队列',
      position: 2,
    })
    expect(getQueueInfo('task-u1', queues)).toEqual({
      queued: true,
      queueName: '上传队列',
      position: 1,
    })
  })

  it('覆盖四个固定队列名的文案映射', () => {
    expect(getQueueNameLabel('VideoDownloaderWorker')).toBe('下载队列')
    expect(getQueueNameLabel('FileUploadWorker')).toBe('上传队列')
    expect(getQueueNameLabel('TranscriberWorker')).toBe('转录队列')
    expect(getQueueNameLabel('LLMWorker')).toBe('总结队列')
    // 未知队列名回退为原始名称
    expect(getQueueNameLabel('UnknownWorker')).toBe('UnknownWorker')
  })

  it('任务等于某个 queue 的 active_task_id 时不算排队，返回 null', () => {
    expect(getQueueInfo('task-active-dl', queues)).toBeNull()
    expect(getQueueInfo('task-active-tr', queues)).toBeNull()
  })

  it('不在任何队列快照中时返回 null', () => {
    expect(getQueueInfo('task-nonexistent', queues)).toBeNull()
  })

  it('快照为空时返回 null（未实例化 worker 的全空快照）', () => {
    expect(getQueueInfo('task-w1', [])).toBeNull()
  })

  it('优先返回第一个命中队列的排队信息', () => {
    const duplicated = [
      makeQueue({ name: 'VideoDownloaderWorker', waiting_task_ids: ['task-x'] }),
      makeQueue({ name: 'FileUploadWorker', waiting_task_ids: ['task-x'] }),
    ]
    expect(getQueueInfo('task-x', duplicated)).toEqual({
      queued: true,
      queueName: '下载队列',
      position: 1,
    })
  })
})
