/**
 * P7 迁移：useTaskViewModel.generateTopic.test.ts（7）+ .adversarial（4）
 * → features/upload/state.ts（提交 payload 的 generate_topic 语义）。
 *
 * 设计决策：开关仅在 summaryMode === 'none' 时随提交显式发送
 * generate_topic（true/false）；standard/agent 模式不发送该字段。
 * 用例涉及 reSummarize/reTranscribe/updateTaskTopic 的（task 域动作）
 * 按 D1 以 task 域 state 显式传参验证（无泄漏断言）。
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { useUploadState } from '../state'
import { useTaskState } from '../../task/state'
import type { UploadState } from '../state'
import type { TaskState } from '../../task/state'

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    isAxiosError: vi.fn(),
    isCancel: vi.fn(),
  },
}))

const mockedAxios = vi.mocked(axios)

const mountUploadState = () => {
  let upload!: UploadState
  const TestComponent = defineComponent({
    setup() {
      upload = useUploadState()
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  return { upload, wrapper }
}

// 无泄漏断言需要 task 域动作（reSummarize/reTranscribe/updateTaskTopic/reDownloadAudio）
const mountStates = () => {
  let upload!: UploadState
  let task!: TaskState
  const TestComponent = defineComponent({
    setup() {
      upload = useUploadState()
      task = useTaskState()
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  return { upload, task, wrapper }
}

const setup = () => {
  vi.clearAllMocks()
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})

  vi.stubGlobal('WebSocket', vi.fn(function () { return { close: vi.fn() } }))
  vi.stubGlobal('navigator', {
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  })

  mockedAxios.get.mockResolvedValue({ data: [] })
  mockedAxios.post.mockResolvedValue({ data: {} })
  mockedAxios.patch.mockResolvedValue({ data: {} })
  mockedAxios.isCancel.mockReturnValue(false)
  mockedAxios.isAxiosError.mockImplementation((value): value is Error => {
    return Boolean(value && typeof value === 'object' && 'isAxiosError' in value)
  })
}

describe('对抗性：仅转录"总结标题"开关 payload — upload 域', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  beforeEach(setup)

  it('[G1] 默认 generateTopic=true；none + 开关开启 → URL 提交 payload 显式 generate_topic=true', async () => {
    const { upload, wrapper } = mountUploadState()
    expect(upload.generateTopic.value).toBe(true)

    upload.videoUrl.value = 'https://example.com/video.mp4'
    await upload.submitTask()

    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none', generate_topic: true }),
      expect.anything(),
    )
    wrapper.unmount()
  })

  it('[G1] none + 开关关闭 → 三条路径 payload 均携带 generate_topic=false', async () => {
    const { upload, wrapper } = mountUploadState()
    upload.generateTopic.value = false

    // URL
    upload.videoUrl.value = 'https://example.com/video.mp4'
    await upload.submitTask()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none', generate_topic: false }),
      expect.anything(),
    )

    // local-path
    mockedAxios.post.mockClear()
    upload.localFilePath.value = '/data/video.mp4'
    upload.videoUrl.value = ''
    await upload.submitTask()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/upload/local-path',
      expect.objectContaining({ summary_mode: 'none', generate_topic: false }),
      expect.anything(),
    )

    // 文件上传 multipart
    mockedAxios.post.mockClear()
    upload.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
    upload.localFilePath.value = ''
    await upload.submitTask()
    const [, formData] = mockedAxios.post.mock.calls.find(([u]) => u === '/upload')!
    expect(formData).toBeInstanceOf(FormData)
    expect((formData as FormData).get('summary_mode')).toBe('none')
    expect((formData as FormData).get('generate_topic')).toBe('false')

    wrapper.unmount()
  })

  it('[G1] none + 开关开启 → 三条路径 payload 均携带 generate_topic=true（含 multipart 字符串 "true"）', async () => {
    const { upload, wrapper } = mountUploadState()
    upload.generateTopic.value = true

    // URL
    upload.videoUrl.value = 'https://example.com/video.mp4'
    await upload.submitTask()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none', generate_topic: true }),
      expect.anything(),
    )

    // local-path
    mockedAxios.post.mockClear()
    upload.localFilePath.value = '/data/video.mp4'
    upload.videoUrl.value = ''
    await upload.submitTask()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/upload/local-path',
      expect.objectContaining({ summary_mode: 'none', generate_topic: true }),
      expect.anything(),
    )

    // 文件上传 multipart
    mockedAxios.post.mockClear()
    upload.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
    upload.localFilePath.value = ''
    await upload.submitTask()
    const [, formData] = mockedAxios.post.mock.calls.find(([u]) => u === '/upload')!
    expect((formData as FormData).get('summary_mode')).toBe('none')
    expect((formData as FormData).get('generate_topic')).toBe('true')

    wrapper.unmount()
  })

  it('[G2] standard/agent 模式：三条路径 payload 均不含 generate_topic（开关不生效）', async () => {
    const { upload, wrapper } = mountUploadState()
    upload.generateTopic.value = false // 开关状态不得泄漏到非 none 模式

    for (const mode of ['standard', 'agent'] as const) {
      upload.summaryMode.value = mode

      // URL
      upload.videoUrl.value = 'https://example.com/video.mp4'
      await upload.submitTask()
      const urlPayload = mockedAxios.post.mock.calls.find(([u]) => u === '/tasks/')![1] as Record<string, unknown>
      expect(urlPayload).toEqual(expect.objectContaining({ summary_mode: mode }))
      expect('generate_topic' in urlPayload).toBe(false)

      // local-path
      mockedAxios.post.mockClear()
      upload.localFilePath.value = '/data/video.mp4'
      upload.videoUrl.value = ''
      await upload.submitTask()
      const localPayload = mockedAxios.post.mock.calls.find(([u]) => u === '/upload/local-path')![1] as Record<string, unknown>
      expect('generate_topic' in localPayload).toBe(false)

      // 文件上传 multipart
      mockedAxios.post.mockClear()
      upload.selectedFile.value = new File(['data'], 'a.mp3', { type: 'audio/mpeg' })
      upload.localFilePath.value = ''
      await upload.submitTask()
      const [, formData] = mockedAxios.post.mock.calls.find(([u]) => u === '/upload')!
      expect((formData as FormData).get('generate_topic')).toBeNull()
    }

    wrapper.unmount()
  })

  it('[G3] 分P提交（submitTaskWithParts）：none 时携带 generate_topic，standard 时不携带', async () => {
    const { upload, wrapper } = mountUploadState()
    upload.summaryMode.value = 'none'
    upload.generateTopic.value = false
    await upload.submitTaskWithParts('https://www.bilibili.com/video/BV1xx', {
      mode: 'separate',
      indices: [0],
    })
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/tasks/',
      expect.objectContaining({ summary_mode: 'none', generate_topic: false }),
      expect.anything(),
    )

    upload.summaryMode.value = 'standard'
    mockedAxios.post.mockClear()
    await upload.submitTaskWithParts('https://www.bilibili.com/video/BV1xx', {
      mode: 'merge',
      indices: [0, 1],
    })
    const standardPayload = mockedAxios.post.mock.calls[0]![1] as Record<string, unknown>
    expect('generate_topic' in standardPayload).toBe(false)

    wrapper.unmount()
  })

  it('[G3] 多文件批量提交（submitLocalPathTasks）：none 时每条均携带 generate_topic', async () => {
    const { upload, wrapper } = mountUploadState()
    upload.summaryMode.value = 'none'
    upload.generateTopic.value = true
    await upload.submitLocalPathTasks(['/a.mp3', '/b.mp3'], 'separate')

    const calls = mockedAxios.post.mock.calls.filter(([u]) => u === '/upload/local-path')
    expect(calls).toHaveLength(2)
    for (const [, payload] of calls) {
      expect(payload).toEqual(expect.objectContaining({ summary_mode: 'none', generate_topic: true }))
    }

    wrapper.unmount()
  })

  it('[G4] 无泄漏：updateTaskTopic（PATCH）/ reSummarize 等非创建请求不受开关影响', async () => {
    const { upload, task, wrapper } = mountStates()
    upload.generateTopic.value = false

    await task.updateTaskTopic('task-a', '新主题')
    const patchPayload = mockedAxios.patch.mock.calls[0]![1] as Record<string, unknown>
    expect(patchPayload).toEqual({ topic: '新主题' })

    mockedAxios.post.mockClear()
    upload.summaryMode.value = 'none'
    await task.reSummarize('task-a', upload.summaryMode.value)
    const [, reSummarizePayload] = mockedAxios.post.mock.calls[0]!
    expect(reSummarizePayload).toEqual({ summary_mode: 'none' })
    expect('generate_topic' in (reSummarizePayload as Record<string, unknown>)).toBe(false)

    wrapper.unmount()
  })
})

describe('对抗性：generate_topic 状态持久性与非创建请求泄漏 — upload 域', () => {
  beforeEach(setup)

  it('开关关闭后切到 standard 再切回 none：关闭状态保留，payload 仍为 false', async () => {
    const { upload, wrapper } = mountUploadState()
    upload.summaryMode.value = 'none'
    upload.generateTopic.value = false
    upload.videoUrl.value = 'https://example.com/persist.mp4'

    // standard 模式提交：不带 generate_topic
    upload.summaryMode.value = 'standard'
    await upload.submitTask()
    const standardPayload = mockedAxios.post.mock.calls[0]![1] as Record<string, unknown>
    expect(standardPayload.summary_mode).toBe('standard')
    expect('generate_topic' in standardPayload).toBe(false)

    // 切回 none：开关仍为关闭，提交携带 false（submitTask 成功后清空 videoUrl，需重设）
    mockedAxios.post.mockClear()
    upload.summaryMode.value = 'none'
    upload.videoUrl.value = 'https://example.com/persist.mp4'
    await upload.submitTask()
    const nonePayload = mockedAxios.post.mock.calls[0]![1] as Record<string, unknown>
    expect(nonePayload.generate_topic).toBe(false)

    wrapper.unmount()
  })

  it('reTranscribe payload 只含 summary_mode，不携带 generate_topic（开关开着也不泄漏）', async () => {
    const { upload, task, wrapper } = mountStates()
    upload.generateTopic.value = true
    upload.summaryMode.value = 'none'
    await task.reTranscribe('task-a', upload.summaryMode.value)

    const [, reTranscribePayload] = mockedAxios.post.mock.calls[0]!
    const payload = reTranscribePayload as Record<string, unknown>
    expect(payload).toEqual({ summary_mode: 'none' })
    expect('generate_topic' in payload).toBe(false)

    wrapper.unmount()
  })

  it('reTranscribe 携带 UI 当前 summary_mode（standard 时不发 none，不按任务已存模式）', async () => {
    const { upload, task, wrapper } = mountStates()
    // 用户当前在 standard 模式对某 FAILED 任务重跑
    upload.summaryMode.value = 'standard'
    upload.generateTopic.value = false
    await task.reTranscribe('task-a', upload.summaryMode.value)

    const [, reTranscribePayload] = mockedAxios.post.mock.calls[0]!
    expect((reTranscribePayload as Record<string, unknown>).summary_mode).toBe('standard')
    expect('generate_topic' in (reTranscribePayload as Record<string, unknown>)).toBe(false)

    wrapper.unmount()
  })

  it('reDownloadAudio 无 body（不含 generate_topic）', async () => {
    const { upload, task, wrapper } = mountStates()
    upload.generateTopic.value = false
    await task.reDownloadAudio('task-a')

    const [url, body] = mockedAxios.post.mock.calls[0]!
    expect(String(url)).toContain('/re-download')
    expect(body ?? null).toBeNull()

    wrapper.unmount()
  })
})
