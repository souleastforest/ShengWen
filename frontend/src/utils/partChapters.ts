/**
 * 章节胶囊分P章节项（多P任务章节导航）
 *
 * App.vue 将分P章节项追加到总览收集标题之后，合并后传入
 * FloatingToolbarChapterNav；组件按 id 前缀识别分P区并渲染分区标签；
 * App.vue 按同前缀分流跳转（切页 + 滚动内容区）。
 * 前缀 'part-' 是跳转分流与分P区识别的唯一契约，三方共用本模块。
 */
import type { MarkdownHeadingItem, TaskPart } from '../types'

/** 分P章节项 id 前缀（跳转分流 / 分P区识别共用） */
export const PART_HEADING_ID_PREFIX = 'part-'

/**
 * 生成分P章节项：
 * { id: 'part-' + part_index, text: 'P' + (part_index+1) + ' ' + (title || '未命名分P'), level: 2 }
 * level 2 与章节胶囊现有 h2 缩进/字号一致（可读性优先）。
 */
export const buildPartChapterItem = (part: TaskPart): MarkdownHeadingItem => ({
  id: `${PART_HEADING_ID_PREFIX}${part.part_index}`,
  text: `P${part.part_index + 1} ${part.title || '未命名分P'}`,
  level: 2,
})

/** 判断 id 是否为分P章节项（'part-' 前缀） */
export const isPartHeadingId = (id: string): boolean => id.startsWith(PART_HEADING_ID_PREFIX)
