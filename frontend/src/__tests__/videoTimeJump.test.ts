/**
 * utils/videoTimeJump buildTimestampJumpUrl 测试
 *
 * partIndex 语义：仅 bilibili.com（含子域名）追加 p={partIndex + 1}；
 * b23.tv / YouTube 不追加（向后兼容）。
 */
import { describe, expect, it } from 'vitest'
import { buildTimestampJumpUrl, toTimestampSeconds } from '../utils/videoTimeJump'

describe('buildTimestampJumpUrl', () => {
  it('bilibili.com 域名：带 t= 秒数，partIndex 追加 p={index+1}', () => {
    const url = buildTimestampJumpUrl('https://www.bilibili.com/video/BV1xx', 65, 0)
    expect(url).toContain('t=65')
    expect(url).toContain('p=1')
  })

  it('bilibili.com 域名：第二个分P partIndex=1 → p=2', () => {
    const url = buildTimestampJumpUrl('https://www.bilibili.com/video/BV1xx', 65, 1)
    expect(url).toContain('t=65')
    expect(url).toContain('p=2')
  })

  it('bilibili.com 域名：不传 partIndex 时保持原行为（不加 p=）', () => {
    const url = buildTimestampJumpUrl('https://www.bilibili.com/video/BV1xx', 65)
    expect(url).toContain('t=65')
    expect(url).not.toContain('p=')
  })

  it('b23.tv 短链：带 t= 但不追加 p=（向后兼容）', () => {
    const url = buildTimestampJumpUrl('https://b23.tv/abc123', 30, 2)
    expect(url).toContain('t=30')
    expect(url).not.toContain('p=')
  })

  it('YouTube：带 t={n}s 但不追加 p=（向后兼容）', () => {
    const url = buildTimestampJumpUrl('https://www.youtube.com/watch?v=abc', 90, 0)
    expect(url).toContain('t=90s')
    expect(url).not.toContain('p=')
  })

  it('空 URL / 非法秒数 → null', () => {
    expect(buildTimestampJumpUrl('', 10, 0)).toBeNull()
    expect(buildTimestampJumpUrl('https://example.com/v.mp4', -1)).toBeNull()
    expect(buildTimestampJumpUrl('not a url', 10)).toBeNull()
  })
})

describe('toTimestampSeconds', () => {
  it('HH/MM/SS → 秒', () => {
    expect(toTimestampSeconds('01', '01', '01')).toBe(3661)
  })
})
