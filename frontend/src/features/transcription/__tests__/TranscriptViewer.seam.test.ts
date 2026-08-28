/**
 * TranscriptViewer 渲染 seam 测试
 *
 * - segments 优先：有 segments 时直接渲染（[HH:MM:SS] chip + 文本），忽略 transcript 行解析；
 * - 无 segments → parseTranscriptLines 回退：subtitle 行 = 时间 chip + 文本，plain 行 = 纯文本；
 * - 时间 chip：有可跳转 URL（buildTimestampJumpUrl 非 null）→ a[target=_blank]（href 含 t=）；
 *   本地文件/无 URL → span.ss-time-jump-chip--disabled；
 * - partIndex 透传：bilibili.com 域名 href 含 p={partIndex+1}。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TranscriptViewer from '../components/TranscriptViewer.vue'
import type { TranscriptSegment } from '../../../types'

const segments: TranscriptSegment[] = [
  { start: 1.5, end: 4.0, text: '第一段' },
  { start: 5.0, end: 9.0, text: '第二段', speaker_id: '1' },
]

describe('TranscriptViewer', () => {
  it('segments 优先渲染：[HH:MM:SS] chip + 文本，忽略 transcript 行解析', () => {
    const wrapper = mount(TranscriptViewer, {
      props: {
        transcript: '000001 旧式时间戳行',
        segments,
        videoUrl: 'https://www.bilibili.com/video/BV1xx',
      },
    })
    expect(wrapper.find('.ss-transcript-viewer').exists()).toBe(true)
    const lines = wrapper.findAll('.ss-transcript-line')
    expect(lines).toHaveLength(2)
    expect(lines[0]!.text()).toContain('[00:00:01]')
    expect(lines[0]!.text()).toContain('第一段')
    expect(lines[1]!.text()).toContain('[00:00:05]')
    expect(lines[1]!.text()).toContain('第二段')
    // segments 模式不出现旧式时间戳行解析结果（00:00:01 来自 segments 而非 transcript 行）
    wrapper.unmount()
  })

  it('无 segments：HHMMSS 行回退解析为 chip + 文本，plain 行为纯文本', () => {
    const wrapper = mount(TranscriptViewer, {
      props: {
        transcript: '000012 你好世界\n这是没有时间戳的一行',
        videoUrl: '',
      },
    })
    const lines = wrapper.findAll('.ss-transcript-line')
    expect(lines).toHaveLength(2)
    expect(lines[0]!.text()).toContain('[00:00:12]')
    expect(lines[0]!.text()).toContain('你好世界')
    expect(lines[1]!.text()).toBe('这是没有时间戳的一行')
    expect(lines[1]!.find('.ss-time-jump-chip').exists()).toBe(false)
    wrapper.unmount()
  })

  it('B站 URL：chip 为 a[target=_blank] 且 href 含 t= 秒数', () => {
    const wrapper = mount(TranscriptViewer, {
      props: {
        transcript: '000105 跳转测试', // 01:05 = 65 秒
        videoUrl: 'https://www.bilibili.com/video/BV1xx',
      },
    })
    const chip = wrapper.find('.ss-time-jump-chip')
    expect(chip.element.tagName).toBe('A')
    expect(chip.attributes('target')).toBe('_blank')
    expect(chip.attributes('href')).toContain('t=65')
    wrapper.unmount()
  })

  it('本地文件/无 URL：chip 为 span.ss-time-jump-chip--disabled（不可跳转）', () => {
    const wrapper = mount(TranscriptViewer, {
      props: {
        transcript: '000012 本地文件行',
        videoUrl: 'file:///tmp/demo.mp4',
      },
    })
    const chip = wrapper.find('.ss-time-jump-chip')
    expect(chip.element.tagName).toBe('SPAN')
    expect(chip.classes()).toContain('ss-time-jump-chip--disabled')
    expect(wrapper.find('a.ss-time-jump-chip').exists()).toBe(false)
    wrapper.unmount()
  })

  it('partIndex 透传：bilibili.com 域名 href 含 p={partIndex+1}', () => {
    const wrapper = mount(TranscriptViewer, {
      props: {
        transcript: '000012 分P行',
        videoUrl: 'https://www.bilibili.com/video/BV1xx',
        partIndex: 1,
      },
    })
    expect(wrapper.find('.ss-time-jump-chip').attributes('href')).toContain('p=2')
    wrapper.unmount()
  })

  it('空内容：显示"暂无转录内容"占位', () => {
    const wrapper = mount(TranscriptViewer, {
      props: { transcript: null, segments: null, videoUrl: '' },
    })
    expect(wrapper.text()).toContain('暂无转录内容')
    wrapper.unmount()
  })
})
