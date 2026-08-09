/**
 * 对抗性测试：TaskInfoModal 快速重跑按钮 + 视频 URL 原网址展示与复制
 *
 * 需求：
 * 1. FAILED 状态显示「快速重跑」按钮（状态行），点击 emit reTranscribe；非 FAILED 不显示；
 * 2. 「视频 URL」行显示完整原网址文本（可悬停看全），保留 <a> 打开行为，新增复制按钮
 *    （navigator.clipboard.writeText(完整 URL)，成功后短暂显示「已复制」反馈）；
 * 3. 本地文件任务（file://）不渲染无效的 <a>，只显示路径文本 + 复制。
 * 失败 = 实现缺陷。
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

describe('对抗性：TaskInfoModal 快速重跑 + URL 复制', () => {
  let writeTextMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    writeTextMock = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', {
      clipboard: { writeText: writeTextMock },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const mountModal = (task: Task | null): VueWrapper => {
    return mount(TaskInfoModal, {
      props: {
        show: true,
        selectedTask: task,
      },
    })
  }

  const findRetryButton = (wrapper: VueWrapper) =>
    wrapper.findAll('button').find((b) => b.attributes('title') === '快速重跑')

  const findCopyButton = (wrapper: VueWrapper) =>
    wrapper.findAll('button').find((b) => b.attributes('title') === '复制原网址')

  it('FAILED 状态显示「快速重跑」按钮，点击 emit reTranscribe', async () => {
    const wrapper = mountModal(makeTask({ status: 'FAILED' }))

    const retry = findRetryButton(wrapper)
    expect(retry).toBeDefined()

    await retry!.trigger('click')
    expect(wrapper.emitted('reTranscribe')).toHaveLength(1)
  })

  it('COMPLETED / PENDING 等非 FAILED 状态不显示「快速重跑」按钮', () => {
    const completed = mountModal(makeTask({ status: 'COMPLETED' }))
    const pending = mountModal(makeTask({ status: 'PENDING' }))

    expect(findRetryButton(completed)).toBeUndefined()
    expect(findRetryButton(pending)).toBeUndefined()
  })

  it('「视频 URL」行显示完整原网址文本并保留 <a> 打开行为', () => {
    const url = 'https://example.com/very/long/video/path?id=123'
    const wrapper = mountModal(makeTask({ video_url: url }))

    expect(wrapper.text()).toContain(url)
    const anchor = wrapper.find(`a[href="${url}"]`)
    expect(anchor.exists()).toBe(true)
    expect(anchor.attributes('target')).toBe('_blank')
  })

  it('点击复制按钮调用 clipboard.writeText(完整 URL) 并短暂显示「已复制」反馈', async () => {
    const url = 'https://example.com/video?id=abc'
    const wrapper = mountModal(makeTask({ video_url: url }))

    const copy = findCopyButton(wrapper)
    expect(copy).toBeDefined()

    await copy!.trigger('click')
    await flushPromises()

    expect(writeTextMock).toHaveBeenCalledTimes(1)
    expect(writeTextMock).toHaveBeenCalledWith(url)
    // 内联反馈：按钮 title 变为「已复制」（1.5s 后还原）
    const copiedButton = wrapper
      .findAll('button')
      .find((b) => b.attributes('title') === '已复制')
    expect(copiedButton).toBeDefined()
  })

  it('本地文件任务（file://）：不渲染 <a>，显示原路径文本，复制按钮复制该路径', async () => {
    const localPath = 'file:///home/user/videos/demo.mp4'
    const wrapper = mountModal(makeTask({ video_url: localPath }))

    expect(wrapper.findAll('a')).toHaveLength(0)
    expect(wrapper.text()).toContain(localPath)

    const copy = findCopyButton(wrapper)
    await copy!.trigger('click')
    await flushPromises()

    expect(writeTextMock).toHaveBeenCalledWith(localPath)
  })
})
