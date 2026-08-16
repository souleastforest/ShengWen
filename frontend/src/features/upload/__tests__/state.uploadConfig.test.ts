/**
 * P7 迁移：useTaskViewModel.uploadConfig.test.ts → features/upload/state.ts
 * 用例逻辑原样保留（fetchUploadConfig 语义）；uploadConfigReconcile（WS onopen
 * 对账）见同目录 state.uploadConfigReconcile.test.ts。
 */
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import axios from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  useUploadState,
  DEFAULT_MAX_UPLOAD_BYTES,
} from '../state'
import type { UploadState } from '../state'

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

describe('upload 域上传配置下发（useTaskViewModel 迁移）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    mockedAxios.get.mockResolvedValue({ data: [] })
    vi.stubGlobal('WebSocket', vi.fn(function () { return { close: vi.fn() } }))
  })

  it('装配层挂载时拉取 /upload/config 并应用后端上限', async () => {
    // GET 按路径返回：/upload/config 返回 1GB
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).endsWith('/upload/config')) {
        return Promise.resolve({ data: { max_upload_mb: 1024 } })
      }
      return Promise.resolve({ data: [] })
    })

    const { upload } = mountUploadState()
    await upload.fetchUploadConfig()
    await flushPromises()

    expect(upload.uploadMaxBytes.value).toBe(1024 * 1024 * 1024)
  })

  it('配置拉取失败时回退默认 2GB（不阻塞 UI）', async () => {
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).endsWith('/upload/config')) {
        return Promise.reject(new Error('network down'))
      }
      return Promise.resolve({ data: [] })
    })

    const { upload } = mountUploadState()
    await upload.fetchUploadConfig()
    await flushPromises()

    expect(upload.uploadMaxBytes.value).toBe(DEFAULT_MAX_UPLOAD_BYTES)
  })
})
