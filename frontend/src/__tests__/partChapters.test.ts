/**
 * 章节胶囊分P章节项生成（src/utils/partChapters.ts）
 *
 * 多P任务：App.vue 将分P章节项追加到总览收集标题之后传入章节导航；
 * 分P项契约：{ id: 'part-' + part_index, text: 'P' + (part_index+1) + ' ' + title, level: 2 }。
 * 覆盖：id/text/level 生成、未命名兜底、前缀识别。
 */
import { describe, expect, it } from 'vitest'
import {
  buildPartChapterItem,
  isPartHeadingId,
  PART_HEADING_ID_PREFIX,
} from '../utils/partChapters'
import type { TaskPart } from '../types'

const makePart = (idx: number, extra?: Partial<TaskPart>): TaskPart => ({
  task_id: 'task-a',
  part_index: idx,
  status: 'COMPLETED',
  progress: 1,
  ...extra,
})

describe('buildPartChapterItem：分P章节项生成', () => {
  it('id/text/level：part-{part_index} / P{idx+1} {title} / 2', () => {
    const item = buildPartChapterItem(makePart(0, { title: '第一章' }))
    expect(item).toEqual({ id: 'part-0', text: 'P1 第一章', level: 2 })

    // 非 0 起始 part_index（真实任务从 0 连续，但生成器不依赖起始值）
    const item5 = buildPartChapterItem(makePart(5, { title: '第六集' }))
    expect(item5).toEqual({ id: 'part-5', text: 'P6 第六集', level: 2 })
  })

  it('title 缺省兜底：未命名分P', () => {
    const item = buildPartChapterItem(makePart(1))
    expect(item.text).toBe('P2 未命名分P')
    expect(item.id).toBe('part-1')
  })

  it('PART_HEADING_ID_PREFIX 与 isPartHeadingId 前缀识别', () => {
    expect(PART_HEADING_ID_PREFIX).toBe('part-')
    expect(isPartHeadingId('part-0')).toBe(true)
    expect(isPartHeadingId('part-12')).toBe(true)
    expect(isPartHeadingId('总体概览')).toBe(false)
    expect(isPartHeadingId('')).toBe(false)
  })
})
