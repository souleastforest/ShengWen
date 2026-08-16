/**
 * P7 迁移：useTaskViewModel.uploadConfigReconcile.test.ts → upload/task 域
 *
 * 需求：后端运行期修改 storage.max_upload_mb 后，前端上传预检不得陈旧——
 * WS 重连 onopen 全量对账（fetchTasks + fetchQueueSnapshot + selectTask）
 * 应并入 fetchUploadConfig()（P2-C）。拆分后对账由各域订阅层
 * watch(status==='open') 驱动：task 域 fetchTasks/fetchQueueSnapshot，
 * upload 域 fetchUploadConfig。
 * 失败 = 实现缺陷。
 */
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { useTaskState } from '../../task/state'
import { useUploadState } from '../state'
import { useWebSocket } from '../../../shared/ws'
import type { TaskState } from '../../task/state'
import type { UploadState } from '../state'

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

const mountStates = () => {
  let task!: TaskState
  let upload!: UploadState
  let ws!: ReturnType<typeof useWebSocket>
  const TestComponent = defineComponent({
    setup() {
      task = useTaskState()
      upload = useUploadState()
      ws = useWebSocket()
      task.syncWithWs(ws)
      upload.syncWithWs(ws)
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  // 模拟 App.vue onMounted：初始拉取 + WS 连接
  task.fetchTasks()
  task.fetchQueueSnapshot()
  upload.fetchUploadConfig()
  ws.connect()
  return { task, upload, ws, wrapper }
}

describe('WS onopen 对账并入 fetchUploadConfig（P2-C，域拆分迁移）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

    const { upload, wrapper } = mountStates()
    await flushPromises()
    expect(upload.uploadMaxBytes.value).toBe(512 * 1024 * 1024)

    // 后端运行期上调上限 → WS 重连 onopen 对账应重新拉取（不陈旧）
    configMb = 1024
    wsInstance!.onopen?.()
    await flushPromises()

    expect(upload.uploadMaxBytes.value).toBe(1024 * 1024 * 1024)

    const configCalls = () =>
      mockedAxios.get.mock.calls.filter(([u]) => String(u).endsWith('/upload/config')).length
    expect(configCalls()).toBe(2) // 挂载 1 次 + onopen 对账 1 次

    wrapper.unmount()
  })

  it('onopen 对账其余部分不受影响：fetchTasks + fetchQueueSnapshot 仍被调用', async () => {
    const { wrapper } = mountStates()
    await flushPromises()

    wsInstance!.onopen?.()
    await flushPromises()

    const tasksCalls = () =>
      mockedAxios.get.mock.calls.filter(([url]) => url === '/tasks/').length
    const queueCalls = () =>
      mockedAxios.get.mock.calls.filter(([url]) => url === '/tasks/queue').length
    expect(tasksCalls()).toBe(2) // 挂载 + onopen
    expect(queueCalls()).toBe(2) // 挂载 + onopen

    wrapper.unmount()
  })
})
