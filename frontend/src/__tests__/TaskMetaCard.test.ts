/**
 * 对抗性测试：TaskMetaCard 视频链接复制按钮
 *
 * 需求：视频链接行显示完整 URL（可点击），新增复制按钮 →
 * navigator.clipboard.writeText(URL)，成功后短暂显示「已复制」反馈；
 * file:// 任务不渲染无效 <a>，只显示路径文本 + 复制。
 * 失败 = 实现缺陷。
 */
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TaskMetaCard from '../components/TaskMetaCard.vue'
import type { Task } from '../types'

const makeTask = (overrides: Partial<Task>): Task => ({
  id: 'task-1',
  video_url: 'https://example.com/video',
  status: 'COMPLETED',
  created_at: '2026-08-08T10:00:00Z',
  progress: 100,
  audio_duration: 120,
  transcription_time: 30,
  ...overrides,
})

describe('对抗性：TaskMetaCard 视频链接复制', () => {
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

  const mountCard = (task: Task): VueWrapper => {
    return mount(TaskMetaCard, {
      props: {
        task,
        topic: '测试主题',
      },
    })
  }

  const findCopyButton = (wrapper: VueWrapper) =>
    wrapper.findAll('button').find((b) => b.attributes('title') === '复制原网址')

  it('视频链接行显示完整原网址文本并保留 <a>', () => {
    const url = 'https://example.com/very/long/video/path?id=123'
    const wrapper = mountCard(makeTask({ video_url: url }))

    expect(wrapper.text()).toContain(url)
    const anchor = wrapper.find(`a[href="${url}"]`)
    expect(anchor.exists()).toBe(true)
    expect(anchor.attributes('target')).toBe('_blank')
  })

  it('点击复制按钮调用 clipboard.writeText(URL) 并短暂显示「已复制」反馈', async () => {
    const url = 'https://example.com/video?id=abc'
    const wrapper = mountCard(makeTask({ video_url: url }))

    const copy = findCopyButton(wrapper)
    expect(copy).toBeDefined()

    await copy!.trigger('click')
    await flushPromises()

    expect(writeTextMock).toHaveBeenCalledTimes(1)
    expect(writeTextMock).toHaveBeenCalledWith(url)

    const copiedButton = wrapper
      .findAll('button')
      .find((b) => b.attributes('title') === '已复制')
    expect(copiedButton).toBeDefined()
  })

  it('本地文件任务（file://）：不渲染 <a>，显示路径文本，复制按钮复制该路径', async () => {
    const localPath = 'file:///home/user/videos/demo.mp4'
    const wrapper = mountCard(makeTask({ video_url: localPath }))

    expect(wrapper.findAll('a')).toHaveLength(0)
    expect(wrapper.text()).toContain(localPath)

    const copy = findCopyButton(wrapper)
    await copy!.trigger('click')
    await flushPromises()

    expect(writeTextMock).toHaveBeenCalledWith(localPath)
  })
})
