/**
 * 对抗性测试：上传配置下发（fetchUploadConfig）
 *
 * 需求（code-reviewer I2 修复）：前端上传大小上限与后端同源——onMounted 拉取
 * GET /upload/config（storage.max_upload_mb），失败静默回退默认 2GB，不阻塞 UI。
 * 失败 = 实现缺陷。
 */
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import axios from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  useTaskViewModel,
  DEFAULT_MAX_UPLOAD_BYTES,
} from '../composables/useTaskViewModel'

const mockedAxios = vi.mocked(axios)

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
    isAxiosError: vi.fn(() => false),
    isCancel: vi.fn(() => false),
  },
}))

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

describe('useTaskViewModel 上传配置下发', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    mockedAxios.get.mockResolvedValue({ data: [] })
  })

  it('onMounted 拉取 /upload/config 并应用后端上限', async () => {
    // GET 按路径返回：其他设置请求返回空数组，/upload/config 返回 1GB
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).endsWith('/upload/config')) {
        return Promise.resolve({ data: { max_upload_mb: 1024 } })
      }
      return Promise.resolve({ data: [] })
    })

    const { viewModel } = mountViewModel()
    await flushPromises()

    expect(viewModel.uploadMaxBytes.value).toBe(1024 * 1024 * 1024)
  })

  it('配置拉取失败时回退默认 2GB（不阻塞 UI）', async () => {
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).endsWith('/upload/config')) {
        return Promise.reject(new Error('network down'))
      }
      return Promise.resolve({ data: [] })
    })

    const { viewModel } = mountViewModel()
    await flushPromises()

    expect(viewModel.uploadMaxBytes.value).toBe(DEFAULT_MAX_UPLOAD_BYTES)
  })
})
