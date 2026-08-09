/**
 * 对抗性测试：TaskInfoModal 快速重跑与 URL 复制的边界场景
 *
 * 需求：
 * 1. FAILED 显示「快速重跑」（emit reTranscribe）；非 FAILED 不显示；
 *    FAILED 且音频缺失时与「重新下载」按钮共存不冲突（file:// 本地任务只显示重跑）；
 * 2. URL 复制：完整原网址（含特殊字符/B站 query 参数）写入剪贴板；
 *    成功后 1.5s 内显示「已复制」，随后还原；复制失败不显示成功反馈。
 * 每个用例失败 = 实现缺陷。
 */
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TaskInfoModal from '../components/TaskInfoModal.vue'
import type { Task } from '../types'

const makeTask = (overrides: Partial<Task>): Task => ({
  id: 'task-1',
  video_url: 'https://example.com/video',
  status: 'FAILED',
  created_at: '2026-08-08T10:00:00Z',
  progress: 0,
  ...overrides,
})

const mountModal = (task: Task | null): VueWrapper =>
  mount(TaskInfoModal, { props: { show: true, selectedTask: task } })

const byTitle = (wrapper: VueWrapper, title: string) =>
  wrapper.findAll('button').find((b) => b.attributes('title') === title)

// 「重新下载」按钮是文本按钮（无 title），按文案定位
const byText = (wrapper: VueWrapper, text: string) =>
  wrapper.findAll('button').find((b) => b.text().includes(text))

describe('对抗性：TaskInfoModal 快速重跑 + URL 复制边界', () => {
  let writeTextMock: ReturnType<typeof vi.fn>
  let execCommandSpy: ReturnType<typeof vi.fn>
  const originalNavigator = globalThis.navigator

  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    writeTextMock = vi.fn().mockResolvedValue(undefined)
    execCommandSpy = vi.fn().mockReturnValue(false)
    Object.defineProperty(document, 'execCommand', {
      value: execCommandSpy,
      configurable: true,
      writable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    vi.useRealTimers()
    Object.defineProperty(globalThis, 'navigator', {
      value: originalNavigator,
      configurable: true,
    })
  })

  const withClipboard = (mock: ReturnType<typeof vi.fn>) =>
    vi.stubGlobal('navigator', { clipboard: { writeText: mock } })

  it('FAILED 且音频缺失（在线 URL）：快速重跑与重新下载按钮共存', () => {
    const wrapper = mountModal(
      makeTask({
        status: 'FAILED',
        audio_downloaded: false,
        audio_missing_reason: 'reclaimed',
      }),
    )
    expect(byTitle(wrapper, '快速重跑')).toBeDefined()
    expect(byText(wrapper, '重新下载')).toBeDefined()
  })

  it('FAILED 但 file:// 本地任务：只显示快速重跑，不显示重新下载', () => {
    const wrapper = mountModal(
      makeTask({
        status: 'FAILED',
        video_url: 'file:///tmp/local.mp4',
        audio_downloaded: false,
      }),
    )
    expect(byTitle(wrapper, '快速重跑')).toBeDefined()
    expect(byText(wrapper, '重新下载')).toBeUndefined()
  })

  it('非终态 SUMMARIZING/TRANSCRIBING 不显示快速重跑（与重新下载同时缺席）', () => {
    for (const status of ['SUMMARIZING', 'TRANSCRIBING'] as const) {
      const wrapper = mountModal(makeTask({ status }))
      expect(byTitle(wrapper, '快速重跑')).toBeUndefined()
    }
  })

  it('B 站带追踪参数 URL：复制内容为完整原样 URL（不做任何清洗）', async () => {
    withClipboard(writeTextMock)
    const url =
      'https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333.999.0.0&vd_source=abc123'
    const wrapper = mountModal(makeTask({ video_url: url }))

    await byTitle(wrapper, '复制原网址')!.trigger('click')
    await flushPromises()

    expect(writeTextMock).toHaveBeenCalledTimes(1)
    expect(writeTextMock).toHaveBeenCalledWith(url)
  })

  it('超长 URL：展示 truncate 但复制内容完整', async () => {
    withClipboard(writeTextMock)
    const url =
      'https://example.com/' + 'a'.repeat(300) + '?q=' + 'b'.repeat(80)
    const wrapper = mountModal(makeTask({ video_url: url }))

    await byTitle(wrapper, '复制原网址')!.trigger('click')
    await flushPromises()

    expect(writeTextMock).toHaveBeenCalledWith(url)
    expect(writeTextMock.mock.calls[0]![0]).toHaveLength(url.length)
  })

  it('复制成功后 1.5s 内显示「已复制」，超时后还原为「复制原网址」', async () => {
    vi.useFakeTimers()
    withClipboard(writeTextMock)
    const wrapper = mountModal(makeTask({ video_url: 'https://example.com/v' }))
    const { nextTick } = await import('vue')

    await byTitle(wrapper, '复制原网址')!.trigger('click')
    await flushPromises()
    expect(byTitle(wrapper, '已复制')).toBeDefined()

    vi.advanceTimersByTime(1499)
    await nextTick()
    expect(byTitle(wrapper, '已复制')).toBeDefined()
    vi.advanceTimersByTime(1)
    await nextTick()
    expect(byTitle(wrapper, '已复制')).toBeUndefined()
    expect(byTitle(wrapper, '复制原网址')).toBeDefined()
  })

  it('复制失败（clipboard 抛错且降级 execCommand 失败）→ 不显示「已复制」', async () => {
    withClipboard(writeTextMock.mockRejectedValue(new Error('denied')))
    const wrapper = mountModal(makeTask({ video_url: 'https://example.com/v' }))

    await byTitle(wrapper, '复制原网址')!.trigger('click')
    await flushPromises()

    expect(execCommandSpy).toHaveBeenCalledWith('copy')
    expect(byTitle(wrapper, '已复制')).toBeUndefined()
    expect(byTitle(wrapper, '复制原网址')).toBeDefined()
  })

  it('多次连续点击复制：反馈计时不叠加残留（最终仍会还原）', async () => {
    vi.useFakeTimers()
    withClipboard(writeTextMock)
    const wrapper = mountModal(makeTask({ video_url: 'https://example.com/v' }))
    const { nextTick } = await import('vue')
    const copyBtn = byTitle(wrapper, '复制原网址')!

    await copyBtn.trigger('click')
    await flushPromises()
    await byTitle(wrapper, '已复制')!.trigger('click')
    await flushPromises()

    vi.advanceTimersByTime(1500)
    await nextTick()
    expect(byTitle(wrapper, '已复制')).toBeUndefined()
  })
})
