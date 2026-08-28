/**
 * 转录行解析（字幕化渲染的前端解析层）
 *
 * 语义对齐后端 summarization/chunker.py 的 HHMMSS 行首解析：
 * - 行首 6 位数字（HHMMSS）且 mm<60、ss<60 → subtitle 行（seconds = 秒数）；
 * - 其余（无时间戳 / 非法时间戳 / 时间戳后无内容）→ plain 纯文本行（原文保留）。
 *
 * 注意：transcript 文本格式（HHMMSS 行）是 chunker/LLM 链路的稳定契约，解析
 * 只用于渲染，绝不可改写/回写 transcript 字符串。
 */
import { toTimestampSeconds } from './videoTimeJump'

export interface TranscriptLine {
  kind: 'subtitle' | 'plain'
  /** subtitle 行的时间（秒）；plain 行为空 */
  seconds?: number
  /** subtitle 行去除时间戳前缀的文本；plain 行为原始行 */
  text: string
}

// 行首 6 位数字 + 剩余内容（与后端 _HHMMSS_RE 一致；mm/ss 边界由显式校验兜底）
const HHMMSS_RE = /^(\d{2})(\d{2})(\d{2})(.*)$/

export const parseTranscriptLines = (text: string | null | undefined): TranscriptLine[] => {
  return (text ?? '')
    .split('\n')
    .map((line): TranscriptLine => {
      const match = HHMMSS_RE.exec(line)
      if (match) {
        const hh = match[1]
        const mm = match[2]
        const ss = match[3]
        const content = (match[4] ?? '').trim()
        // mm/ss < 60 才合法（对齐 chunker.parse_timestamp_sec）；空内容行不视为字幕行
        if (hh && mm && ss && Number(mm) < 60 && Number(ss) < 60 && content) {
          return {
            kind: 'subtitle',
            seconds: toTimestampSeconds(hh, mm, ss),
            text: content,
          }
        }
      }
      return { kind: 'plain', text: line }
    })
    .filter((line) => line.kind === 'subtitle' || line.text !== '')
}

/** 秒 → "HH:MM:SS"（补零；小数向下取整；负数归零） */
export const formatHms = (seconds: number): string => {
  const total = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}
