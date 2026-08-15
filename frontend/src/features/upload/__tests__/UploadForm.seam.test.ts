/**
 * P6 seam 契约测试：features/upload/components/UploadForm.vue
 *
 * 从 Sidebar.vue 拆出的提交表单（纯搬移，行为等价）。契约面：
 * - models: videoUrl / selectedFile / localFilePath / summaryMode / generateTopic
 * - props: isLocalClient / isSubmitting / uploadProgress / maxUploadBytes
 * - emits: submit / cancelSubmit
 * 本测试锁定 props 透传、emit 触发、DOM 结构与大小预检逻辑。
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import UploadForm from '../components/UploadForm.vue'

const baseProps = {
  videoUrl: '',
  selectedFile: null as File | null,
  localFilePath: '',
  summaryMode: 'none' as 'none' | 'standard' | 'agent',
  generateTopic: true,
  isLocalClient: false,
  isSubmitting: false,
  uploadProgress: 0,
  maxUploadBytes: 2 * 1024 * 1024 * 1024,
}

const mountForm = (overrides: Record<string, unknown> = {}) =>
  mount(UploadForm, { props: { ...baseProps, ...overrides } })

const makeFile = (size: number, name = 'video.mp4') => {
  const file = new File(['x'], name, { type: 'video/mp4' })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

describe('UploadForm seam：DOM 结构', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('渲染三通道：URL 输入、文件上传按钮、本地路径输入（isLocalClient）', () => {
    const wrapper = mountForm()
    expect(wrapper.find('input[placeholder*="粘贴视频 URL"]').exists()).toBe(true)
    expect(wrapper.find('button[title="上传文件"]').exists()).toBe(true)
    expect(wrapper.find('input[type="file"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('根据本地文件路径创建')

    const local = mountForm({ isLocalClient: true })
    expect(local.text()).toContain('根据本地文件路径创建')
    expect(local.find('input[placeholder*="粘贴本机文件路径"]').exists()).toBe(true)
  })

  it('渲染三态模式 Tab（仅转录/标准模式/Agent 模式）与提交按钮', () => {
    const wrapper = mountForm()
    expect(wrapper.text()).toContain('仅转录')
    expect(wrapper.text()).toContain('标准模式')
    expect(wrapper.text()).toContain('Agent 模式')
    expect(wrapper.text()).toContain('开始处理')
    // thumb 三态均分定位类保留
    expect(
      wrapper.findAll('div').some((d) => d.classes().some((c) => c.includes('w-[calc(33.333%')))
    ).toBe(true)
  })

  it('仅转录模式显示"总结标题"开关，其他模式不显示', () => {
    const wrapper = mountForm()
    expect(wrapper.text()).toContain('总结标题')
    const standard = mountForm({ summaryMode: 'standard' })
    expect(standard.text()).not.toContain('总结标题')
  })
})

describe('UploadForm seam：文件大小预检', () => {
  it('超过 maxUploadBytes 的文件被拒绝并显示动态错误提示', async () => {
    const wrapper = mountForm({ maxUploadBytes: 1 * 1024 * 1024 * 1024 })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [makeFile(2 * 1024 * 1024 * 1024)] })
    await input.trigger('change')

    expect(wrapper.text()).toContain('文件过大')
    expect(wrapper.text()).toContain('1.00 GB')
    expect(wrapper.find('button[title="清除文件"]').exists()).toBe(false)
  })

  it('上限内文件正常选中并清空互斥 URL/路径输入', async () => {
    const wrapper = mountForm({ videoUrl: 'https://example.com/x.mp4' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [makeFile(1024 * 1024, 'ok.mp4')] })
    await input.trigger('change')

    expect(wrapper.find('button[title="清除文件"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('ok.mp4')
    // 互斥：选中文件后 URL 输入被清空
    const videoUrlEmits = wrapper.emitted('update:videoUrl')
    expect(videoUrlEmits).toBeTruthy()
    expect(videoUrlEmits![videoUrlEmits!.length - 1]).toEqual([''])
  })
})

describe('UploadForm seam：提交/取消 emit', () => {
  it('点击提交按钮 emit submit（非提交中）', async () => {
    const wrapper = mountForm({ videoUrl: 'https://example.com/x.mp4' })
    const button = wrapper.findAll('button').find((b) => b.text().includes('开始处理'))!
    await button.trigger('click')
    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  it('提交中点击 emit cancelSubmit', async () => {
    const wrapper = mountForm({ isSubmitting: true, uploadProgress: 0 })
    const button = wrapper.findAll('button').find((b) => b.text().includes('取消提交'))!
    await button.trigger('click')
    expect(wrapper.emitted('cancelSubmit')).toHaveLength(1)
  })

  it('提交中显示上传进度条与百分比', async () => {
    const wrapper = mountForm({ isSubmitting: true, uploadProgress: 42 })
    expect(wrapper.text()).toContain('42%')
  })

  it('URL 输入框回车触发 submit（非空），空输入不触发', async () => {
    const wrapper = mountForm()
    const input = wrapper.find('input[placeholder*="粘贴视频 URL"]')
    await input.trigger('keydown.enter')
    expect(wrapper.emitted('submit')).toBeUndefined()

    await input.setValue('https://example.com/x.mp4')
    await input.trigger('keydown.enter')
    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  it('本地路径输入回车触发 submit（非空）', async () => {
    const wrapper = mountForm({ isLocalClient: true, localFilePath: 'D:/videos/a.mp4' })
    const input = wrapper.find('input[placeholder*="粘贴本机文件路径"]')
    await input.trigger('keydown.enter')
    expect(wrapper.emitted('submit')).toHaveLength(1)
  })
})

describe('UploadForm seam：模式 Tab 与开关 emit', () => {
  it('点击模式 Tab 发出 update:summaryMode', async () => {
    const wrapper = mountForm()
    const tab = wrapper.findAll('button').find((b) => b.text().includes('Agent 模式'))!
    await tab.trigger('click')
    const emits = wrapper.emitted('update:summaryMode')!
    expect(emits[emits.length - 1]).toEqual(['agent'])
  })

  it('总结标题开关切换发出 update:generateTopic', async () => {
    const wrapper = mountForm()
    const sw = wrapper.findAll('button').find((b) => b.attributes('role') === 'switch')!
    await sw.trigger('click')
    const emits = wrapper.emitted('update:generateTopic')!
    expect(emits[emits.length - 1]).toEqual([false])
  })
})

describe('UploadForm seam：本地路径输入清洗', () => {
  it('去除首尾空格与成对引号', async () => {
    const wrapper = mountForm({ isLocalClient: true })
    const input = wrapper.find('input[placeholder*="粘贴本机文件路径"]')
    await input.setValue('  "D:/videos/a.mp4"  ')
    const emits = wrapper.emitted('update:localFilePath')!
    expect(emits[emits.length - 1]).toEqual(['D:/videos/a.mp4'])
  })
})
