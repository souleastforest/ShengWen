/**
 * 对抗性测试：共享 axios 实例（P2-B，src/shared/api/client.ts）
 *
 * 1. 实例化配置：baseURL 收敛 VITE_API_BASE_URL（测试环境未设置 → ''）、
 *    timeout 默认 60s（DEFAULT_TIMEOUT_MS）；
 * 2. 上传调用点（/upload）显式不设超时（timeout: 0，保持旧行为）——XHR
 *    timeout 为整请求总时长（不被 onUploadProgress 重置），固定超时会误杀
 *    慢链路大文件上传；
 * 3. 错误提取统一：getAxiosErrorMessage / isCanceledRequest 从 client 导出，
 *    语义与既有逻辑一致（detail 字符串 / 数组 msg / fallback+message / 取消识别）；
 * 4. useTaskViewModel 全部 HTTP 走共享实例（无裸 axios 调用——错误路径
 *    不产生"获取任务列表失败"等 UI 错误即证明调用未落回裸 axios）。
 * 每个用例失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockInstance, createConfig, isAxiosErrorMock, isCancelMock } = vi.hoisted(() => {
  const mockInstance: Record<string, ReturnType<typeof vi.fn>> = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
  }
  const createConfig: Record<string, unknown> = {}
  const isAxiosErrorMock = vi.fn(() => false)
  const isCancelMock = vi.fn(() => false)
  return { mockInstance, createConfig, isAxiosErrorMock, isCancelMock }
})

vi.mock('axios', () => ({
  default: {
    create: vi.fn((config: unknown) => {
      Object.assign(createConfig, config)
      return mockInstance
    }),
    isAxiosError: isAxiosErrorMock,
    isCancel: isCancelMock,
  },
}))

import {
  apiClient,
  getAxiosErrorMessage,
  isCanceledRequest,
  DEFAULT_TIMEOUT_MS,
} from '../shared/api/client'
import { useTaskViewModel } from '../composables/useTaskViewModel'

const mockedInstance = vi.mocked(apiClient)

const mountViewModel = () => {
  let viewModel!: ReturnType<typeof useTaskViewModel>

  const TestComponent = defineComponent({
    setup() {
      viewModel = useTaskViewModel()
      return () => null
    },
  })

  const wrapper = mount(TestComponent)
  return { viewModel, wrapper }
}

describe('P2-B 共享 axios 实例（shared/api/client）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    mockedInstance.get.mockResolvedValue({ data: [] })
  })

  it('B1 实例化配置：baseURL 收敛 + timeout 默认 60s', () => {
    expect(createConfig).toEqual({ baseURL: '', timeout: 60_000 })
    expect(DEFAULT_TIMEOUT_MS).toBe(60_000)
    expect(apiClient).toBe(mockInstance) // useTaskViewModel 与 client 共用同一实例
  })

  it('B2 上传调用点（/upload）不设超时（timeout: 0，保持旧行为）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    await flushPromises()

    const file = new File(['content'], 'a.mp4', { type: 'video/mp4' })
    await viewModel.uploadFile(file)

    const postCall = mockedInstance.post.mock.calls.find(([url]) => url === '/upload')
    expect(postCall).toBeDefined()
    const [, , config] = postCall!
    // XHR timeout 为整请求总时长（不被 onUploadProgress 重置）：600s 会误杀
    // <3.4MB/s 慢链路大文件上传；显式 timeout: 0 = 无总时长限制（旧行为）
    expect((config as { timeout?: number }).timeout).toBe(0)

    wrapper.unmount()
  })

  it('B3 错误提取统一：getAxiosErrorMessage 语义与既有逻辑一致', () => {
    // detail 字符串
    isAxiosErrorMock.mockReturnValue(true)
    expect(
      getAxiosErrorMessage(
        { response: { data: { detail: '文件过大' } }, message: 'Request failed' },
        'fallback',
      ),
    ).toBe('文件过大')

    // detail 数组 → 参数错误前缀
    expect(
      getAxiosErrorMessage(
        { response: { data: { detail: [{ msg: 'field required' }] } }, message: 'x' },
        'fallback',
      ),
    ).toBe('请求参数错误：field required')

    // 无 detail → fallback：message
    expect(
      getAxiosErrorMessage({ response: {}, message: 'timeout' }, 'fallback'),
    ).toBe('fallback：timeout')

    // 非 axios 错误 → 原样 fallback
    isAxiosErrorMock.mockReturnValue(false)
    expect(getAxiosErrorMessage({ nope: true }, 'fallback')).toBe('fallback')
  })

  it('B4 取消识别统一：isCanceledRequest 语义保持', () => {
    // axios.isCancel 命中
    isCancelMock.mockReturnValue(true)
    expect(isCanceledRequest({ message: 'canceled' })).toBe(true)

    // ERR_CANCELED 命中 / 其他 code 不命中
    isCancelMock.mockReturnValue(false)
    isAxiosErrorMock.mockReturnValue(true)
    expect(isCanceledRequest({ code: 'ERR_CANCELED' })).toBe(true)
    expect(isCanceledRequest({ code: 'ERR_NETWORK' })).toBe(false)

    // 非 axios 错误不命中
    isAxiosErrorMock.mockReturnValue(false)
    expect(isCanceledRequest({ code: 'ERR_CANCELED' })).toBe(false)
  })

  it('B5 useTaskViewModel 全部 HTTP 走共享实例（无裸 axios 调用）', async () => {
    const { viewModel, wrapper } = mountViewModel()
    await flushPromises()

    const calledUrls = mockedInstance.get.mock.calls.map(([url]) => url)
    expect(calledUrls).toContain('/tasks/')
    expect(calledUrls).toContain('/tasks/queue')
    expect(calledUrls).toContain('/upload/config')
    expect(calledUrls).toContain('/llm/providers')
    expect(calledUrls).toContain('/transcription/settings')

    // 裸 axios 的 default 无 get/post 等方法——若仍有调用点落回裸 axios，
    // fetchTasks 等会抛 TypeError 并设置 error.value（行为可观测）。
    expect(viewModel.error.value).toBeNull()

    wrapper.unmount()
  })
})
