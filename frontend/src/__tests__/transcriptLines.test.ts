/**
 * utils/transcriptLines 解析测试
 *
 * 语义对齐后端 summarization/chunker.py 的 HHMMSS 行首解析：
 * - 行首 6 位数字（HHMMSS）且 mm<60、ss<60 → subtitle（含 seconds）；
 * - 其余行（无时间戳 / 非法时间戳 / 空内容）→ plain 纯文本行；
 * - formatHms 秒 → "HH:MM:SS"（补零，向下取整）。
 */
import { describe, expect, it } from 'vitest'
import { formatHms, parseTranscriptLines } from '../utils/transcriptLines'

describe('parseTranscriptLines', () => {
  it('HHMMSS 行首解析为 subtitle 行（seconds = 秒数，text 去除时间戳前缀）', () => {
    const lines = parseTranscriptLines('001234 你好世界\n010203 第二行')
    expect(lines).toEqual([
      { kind: 'subtitle', seconds: 754, text: '你好世界' }, // 12:34
      { kind: 'subtitle', seconds: 3723, text: '第二行' }, // 01:02:03
    ])
  })

  it('无时间戳行解析为 plain 行（原文保留）', () => {
    const lines = parseTranscriptLines('第一行没有时间戳\n000012 有时间的')
    expect(lines).toEqual([
      { kind: 'plain', text: '第一行没有时间戳' },
      { kind: 'subtitle', seconds: 12, text: '有时间的' },
    ])
  })

  it('mm >= 60 或 ss >= 60 的 6 位数字行视为非法时间戳 → plain', () => {
    expect(parseTranscriptLines('006000 分钟越界')).toEqual([
      { kind: 'plain', text: '006000 分钟越界' },
    ])
    expect(parseTranscriptLines('000060 秒越界')).toEqual([
      { kind: 'plain', text: '000060 秒越界' },
    ])
  })

  it('时间戳后无内容（空行）→ plain 原文行', () => {
    expect(parseTranscriptLines('000012')).toEqual([{ kind: 'plain', text: '000012' }])
  })

  it('null / 空串 → 空数组', () => {
    expect(parseTranscriptLines(null)).toEqual([])
    expect(parseTranscriptLines('')).toEqual([])
    expect(parseTranscriptLines(undefined)).toEqual([])
  })

  it('非法字符开头的行（非 6 位数字）→ plain', () => {
    expect(parseTranscriptLines('见 00:12 中文括号时间戳')).toEqual([
      { kind: 'plain', text: '见 00:12 中文括号时间戳' },
    ])
  })
})

describe('formatHms', () => {
  it('秒 → "HH:MM:SS"（补零）', () => {
    expect(formatHms(0)).toBe('00:00:00')
    expect(formatHms(12)).toBe('00:00:12')
    expect(formatHms(3661)).toBe('01:01:01')
    expect(formatHms(45296)).toBe('12:34:56')
  })

  it('小数秒向下取整；负数归零', () => {
    expect(formatHms(12.9)).toBe('00:00:12')
    expect(formatHms(-5)).toBe('00:00:00')
  })
})
