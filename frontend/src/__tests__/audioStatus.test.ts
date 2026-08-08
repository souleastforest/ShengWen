import { describe, expect, it } from 'vitest'
import {
  getAudioStatusInfo,
  canReDownloadAudio,
  isLocalFileUrl,
  TERMINAL_TASK_STATUSES,
} from '../utils/audioStatus'
import type { Task } from '../types'

describe('getAudioStatusInfo 音频状态映射', () => {
  it('audio_downloaded=true -> 已下载（emerald），不允许重新下载', () => {
    const info = getAudioStatusInfo(true, undefined)
    expect(info.label).toBe('已下载')
    expect(info.badgeClass).toBe('text-emerald-600 bg-emerald-50')
    expect(info.showRedownload).toBe(false)
  })

  it('audio_downloaded=false + subtitle_only -> 未下载 · 使用了B站字幕（blue）', () => {
    const info = getAudioStatusInfo(false, 'subtitle_only')
    expect(info.label).toBe('未下载 · 使用了B站字幕')
    expect(info.badgeClass).toBe('text-blue-600 bg-blue-50')
    expect(info.showRedownload).toBe(true)
  })

  it('audio_downloaded=false + reclaimed -> 未下载 · 已被回收清理（amber）', () => {
    const info = getAudioStatusInfo(false, 'reclaimed')
    expect(info.label).toBe('未下载 · 已被回收清理')
    expect(info.badgeClass).toBe('text-amber-600 bg-amber-50')
    expect(info.showRedownload).toBe(true)
  })

  it('audio_downloaded=false + 无原因 -> 未下载（blue）', () => {
    const info = getAudioStatusInfo(false, undefined)
    expect(info.label).toBe('未下载')
    expect(info.badgeClass).toBe('text-blue-600 bg-blue-50')
    expect(info.showRedownload).toBe(true)
  })

  it('字段缺失（undefined）-> 未知（slate），不允许重新下载', () => {
    const info = getAudioStatusInfo(undefined, undefined)
    expect(info.label).toBe('未知')
    expect(info.badgeClass).toBe('text-slate-600 bg-slate-50')
    expect(info.showRedownload).toBe(false)
  })

  it('终态任务清单与后端回收资格一致（COMPLETED/PARTIAL/FAILED）', () => {
    expect(TERMINAL_TASK_STATUSES).toEqual(['COMPLETED', 'PARTIAL', 'FAILED'])
  })
})

describe('canReDownloadAudio 重新下载资格', () => {
  const makeTask = (overrides?: Partial<Task>): Task => ({
    id: 'task-1',
    video_url: 'https://example.com/v.mp4',
    status: 'COMPLETED',
    progress: 1,
    created_at: '2026-08-08T00:00:00Z',
    ...overrides,
  })

  it('audio_downloaded=false 且非 file:// 且终态 -> 允许', () => {
    expect(canReDownloadAudio(makeTask({ audio_downloaded: false }))).toBe(true)
    expect(canReDownloadAudio(makeTask({ audio_downloaded: false, status: 'PARTIAL' }))).toBe(true)
    expect(canReDownloadAudio(makeTask({ audio_downloaded: false, status: 'FAILED' }))).toBe(true)
  })

  it('audio_downloaded=true 或字段缺失 -> 不允许', () => {
    expect(canReDownloadAudio(makeTask({ audio_downloaded: true }))).toBe(false)
    expect(canReDownloadAudio(makeTask({ audio_downloaded: undefined }))).toBe(false)
  })

  it('file:// 本地任务 -> 不允许（无远端媒体可重新下载）', () => {
    const local = makeTask({
      audio_downloaded: false,
      video_url: 'file:///data/videos/foo.mp4',
    })
    expect(canReDownloadAudio(local)).toBe(false)
    expect(isLocalFileUrl('file:///data/videos/foo.mp4')).toBe(true)
    expect(isLocalFileUrl('https://example.com/v.mp4')).toBe(false)
    expect(isLocalFileUrl(undefined)).toBe(false)
  })

  it('非终态（如 DOWNLOADING）-> 不允许（避免与进行中的任务冲突）', () => {
    expect(
      canReDownloadAudio(
        makeTask({ audio_downloaded: false, status: 'DOWNLOADING' }),
      ),
    ).toBe(false)
    expect(
      canReDownloadAudio(
        makeTask({ audio_downloaded: false, status: 'PENDING' }),
      ),
    ).toBe(false)
  })
})
