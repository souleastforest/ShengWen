/**
 * 对抗性测试：WS onopen 对账并入 fetchUploadConfig（P2-C）
 *
 * 需求：后端运行期修改 storage.max_upload_mb 后，前端上传预检不得陈旧——
 * 现仅 onMounted 拉取一次 /upload/config；WS 重连 onopen 全量对账
 * （fetchTasks + fetchQueueSnapshot + selectTask）应并入 fetchUploadConfig()，
 * 使 uploadMaxBytes 与后端同源刷新。
 * 失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { __resetTaskContentCaches, useTaskViewModel } from '../composables/useTaskViewModel'

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    isAxiosError: vi.fn(() => false),
    isCancel: vi.fn(() => false),
  },
}))

const mockedAxios = vi.mocked(axios)

interface MockWebSocketInstance {
  send: ReturnType<typeof vi.fn>
  readyState: number
  close: ReturnType<typeof vi.fn>
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: ((err?: unknown) => void) | null
}

let wsInstance: MockWebSocketInstance | null = null

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

describe('useTaskViewModel WS onopen 对账并入 fetchUploadConfig（P2-C）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    __resetTaskContentCaches()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    mockedAxios.get.mockResolvedValue({ data: [] })
    mockedAxios.isCancel.mockReturnValue(false)

    wsInstance = null
    const WebSocketMock = vi.fn(function MockWebSocket(this: MockWebSocketInstance) {
      wsInstance = this
      this.send = vi.fn()
      this.readyState = 1 // WebSocket.OPEN
      this.close = vi.fn()
      this.onopen = null
      this.onmessage = null
      this.onclose = null
      this.onerror = null
    })
    Object.assign(WebSocketMock, { OPEN: 1 })
    vi.stubGlobal('WebSocket', WebSocketMock)
  })

  it('onopen 对账并入 fetchUploadConfig：运行期改上限后重连刷新 uploadMaxBytes', async () => {
    let configMb = 512
    mockedAxios.get.mockImplementation((url: string) => {
      if (String(url).endsWith('/upload/config')) {
        return Promise.resolve({ data: { max_upload_mb: configMb } })
      }
      return Promise.resolve({ data: [] })
    })

    const { viewModel, wrapper } = mountViewModel()
    await flushPromises()
    expect(viewModel.uploadMaxBytes.value).toBe(512 * 1024 * 1024)

    // 后端运行期上调上限 → WS 重连 onopen 对账应重新拉取（不陈旧）
    configMb = 1024
    wsInstance!.onopen?.()
    await flushPromises()

    expect(viewModel.uploadMaxBytes.value).toBe(1024 * 1024 * 1024)

    const configCalls = () =>
      mockedAxios.get.mock.calls.filter(([u]) => String(u).endsWith('/upload/config')).length
    expect(configCalls()).toBe(2) // onMounted 1 次 + onopen 对账 1 次

    wrapper.unmount()
  })

  it('onopen 对账其余部分不受影响：fetchTasks + fetchQueueSnapshot 仍被调用', async () => {
    const { wrapper } = mountViewModel()
    await flushPromises()

    wsInstance!.onopen?.()
    await flushPromises()

    const tasksCalls = () =>
      mockedAxios.get.mock.calls.filter(([url]) => url === '/tasks/').length
    const queueCalls = () =>
      mockedAxios.get.mock.calls.filter(([url]) => url === '/tasks/queue').length
    expect(tasksCalls()).toBe(2) // onMounted + onopen
    expect(queueCalls()).toBe(2) // onMounted + onopen

    wrapper.unmount()
  })
})
