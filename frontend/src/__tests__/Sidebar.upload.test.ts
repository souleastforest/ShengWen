/**
 * 对抗性测试：本地视频上传通道升级（feat/local-video-upload）
 *
 * 需求（用户决策）：
 * 1. 本机（localhost）客户端也显示文件选择上传按钮（路径输入保留，互斥）
 * 2. 文件大小预检：>2GB 拒绝并提示（与后端 max_upload_mb 一致），正常文件照常选中
 * 3. 提交中显示真实上传进度条（onUploadProgress 驱动，0-100%）
 * 4. 上传任务展示原始文件名 source_name（file:// 任务不显示完整路径）
 * 失败 = 实现缺陷。
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Sidebar from '../components/Sidebar.vue'
import TaskMetaCard from '../components/TaskMetaCard.vue'
import type { Task } from '../types'

const baseProps = {
  isLocalClient: false,
  tasks: [] as Task[],
  queues: [],
  selectedTask: null as Task | null,
  isSubmitting: false,
  uploadProgress: 0,
  llmProviders: [],
  llmSettings: null,
  isUpdatingLlmSettings: false,
  isTestingLlm: false,
  transcriptionSettings: null,
  isUpdatingTranscriptionSettings: false,
  summarizationSettings: null,
  isUpdatingSummarizationSettings: false,
}

const mountSidebar = (opts?: { isLocalClient?: boolean; isSubmitting?: boolean; uploadProgress?: number }) => {
  return mount(Sidebar, {
    props: {
      ...baseProps,
      videoUrl: '',
      selectedFile: null,
      localFilePath: '',
      isSidebarOpen: true,
      ...(opts?.isLocalClient !== undefined ? { isLocalClient: opts.isLocalClient } : {}),
      ...(opts?.isSubmitting !== undefined ? { isSubmitting: opts.isSubmitting } : {}),
      ...(opts?.uploadProgress !== undefined ? { uploadProgress: opts.uploadProgress } : {}),
    },
    global: {
      stubs: {
        ThemeSelector: true,
      },
    },
  })
}

const findUploadButton = (wrapper: VueWrapper) =>
  wrapper.find('button[title="上传文件"]')

describe('对抗性：本机上传按钮可见性', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('__APP_VERSION__', 'test-version')
  })

  it('本机（isLocalClient=true）也显示文件上传按钮', () => {
    const wrapper = mountSidebar({ isLocalClient: true })
    expect(findUploadButton(wrapper).exists()).toBe(true)
  })

  it('远程客户端显示上传按钮（现状保持）', () => {
    const wrapper = mountSidebar({ isLocalClient: false })
    expect(findUploadButton(wrapper).exists()).toBe(true)
  })

  it('本机同时保留本地路径输入区（两种方式并存）', () => {
    const wrapper = mountSidebar({ isLocalClient: true })
    expect(wrapper.text()).toContain('根据本地文件路径创建')
  })
})

describe('对抗性：文件大小预检（2GB 上限）', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  const makeFile = (size: number, name = 'video.mp4') => {
    const file = new File(['x'], name, { type: 'video/mp4' })
    Object.defineProperty(file, 'size', { value: size })
    return file
  }

  it('超过 2GB 的文件被拒绝并显示错误提示', async () => {
    const wrapper = mountSidebar()
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      value: [makeFile(2 * 1024 * 1024 * 1024 + 1)],
    })
    await input.trigger('change')

    expect(wrapper.text()).toContain('文件过大')
    expect(wrapper.text()).toContain('2GB')
    // 超限文件不进入选中态
    expect(wrapper.find('button[title="清除文件"]').exists()).toBe(false)
  })

  it('2GB 内的文件正常选中', async () => {
    const wrapper = mountSidebar()
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      value: [makeFile(1024 * 1024, 'ok.mp4')],
    })
    await input.trigger('change')

    expect(wrapper.find('button[title="清除文件"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('ok.mp4')
  })
})

describe('对抗性：上传进度条', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('上传中显示百分比文案', () => {
    const wrapper = mountSidebar({ isSubmitting: true, uploadProgress: 42 })
    expect(wrapper.text()).toContain('上传中 42%')
  })

  it('进度条宽度与进度一致', () => {
    const wrapper = mountSidebar({ isSubmitting: true, uploadProgress: 42 })
    const bar = wrapper.find('.h-full.bg-primary')
    expect(bar.exists()).toBe(true)
    expect(bar.attributes('style')).toContain('width: 42%')
  })

  it('非上传提交不显示进度条', () => {
    const wrapper = mountSidebar({ isSubmitting: true, uploadProgress: 0 })
    expect(wrapper.find('.h-full.bg-primary').exists()).toBe(false)
  })
})

describe('对抗性：source_name 展示', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
  })

  const makeTask = (overrides: Partial<Task>): Task => ({
    id: 'task-1',
    video_url: 'file://temp/abc123_temp.mp4',
    status: 'COMPLETED',
    created_at: '2026-08-13T10:00:00Z',
    progress: 100,
    ...overrides,
  })

  it('上传任务展示原始文件名而非 file:// 路径', () => {
    const wrapper = mount(TaskMetaCard, {
      props: { task: makeTask({ source_name: '我的视频.mp4' }), topic: '测试主题' },
    })
    expect(wrapper.text()).toContain('我的视频.mp4')
    expect(wrapper.text()).not.toContain('temp/abc123')
  })

  it('无 source_name 的 file:// 任务回退展示路径（存量数据兼容）', () => {
    const wrapper = mount(TaskMetaCard, {
      props: { task: makeTask({ source_name: undefined }), topic: '测试主题' },
    })
    expect(wrapper.text()).toContain('file://temp/abc123_temp.mp4')
  })
})
