import { ref, onMounted, onUnmounted, watch } from 'vue'
import {
  apiClient,
  getAxiosErrorMessage,
  isCanceledRequest,
  isAxiosError,
} from '../shared/api/client'
import type {
  Task,
  TaskPart,
  CreateTaskRequest,
  SummaryMode,
  LLMProvider,
  LLMSettings,
  UpdateLLMSettingsRequest,
  TranscriptionSettings,
  UpdateTranscriptionSettingsRequest,
  SummarizationSettings,
  UpdateSummarizationSettingsRequest,
  BilibiliCookieFromBrowserResult,
  BilibiliVideoInfo,
  BilibiliPartsConfig,
  LocalPathCheckResult,
  LocalFolderScanResult,
  ModelPathValidationRequest,
  ModelPathValidationResult,
  VibeVoiceServiceScanResult,
  VibeVoiceServiceStatus,
  QueueSnapshot,
  QueueResponse,
} from '../types'

// 传统复制方法（兼容非安全上下文，如局域网 HTTP）
const fallbackCopyToClipboard = (text: string): boolean => {
  const textarea = document.createElement('textarea')
  textarea.value = text

  // 设置样式使其不可见但仍可操作
  textarea.style.position = 'fixed'
  textarea.style.top = '0'
  textarea.style.left = '0'
  textarea.style.width = '2em'
  textarea.style.height = '2em'
  textarea.style.padding = '0'
  textarea.style.border = 'none'
  textarea.style.outline = 'none'
  textarea.style.boxShadow = 'none'
  textarea.style.background = 'transparent'
  textarea.style.opacity = '0'

  document.body.appendChild(textarea)

  try {
    // 选中文本
    textarea.focus()
    textarea.select()
    textarea.setSelectionRange(0, text.length)

    // 执行复制命令
    const success = document.execCommand('copy')

    return success
  } finally {
    // 清理 DOM
    document.body.removeChild(textarea)
  }
}

const extractFirstUrl = (raw: string): string | null => {
  const text = (raw || '').trim()
  if (!text) return null

  const directMatch = text.match(/https?:\/\/[^\s]+/i)
  let candidate = directMatch?.[0] || ''

  if (!candidate) {
    const domainMatch = text.match(/((?:www\.)?(?:b23\.tv|bilibili\.com|youtube\.com|youtu\.be)\/[^\s]+)/i)
    if (!domainMatch?.[1]) return null
    candidate = `https://${domainMatch[1]}`
  }

  candidate = candidate.replace(/[)\]}>，。！？；：”’】）》]+$/g, '')

  try {
    return new URL(candidate).toString()
  } catch {
    return null
  }
}

const isBilibiliUrl = (url: string): boolean => {
  try {
    const hostname = new URL(url).hostname.toLowerCase()
    return hostname.includes('bilibili.com') || hostname.includes('b23.tv')
  } catch {
    return false
  }
}

// per-task 完整内容缓存（include_content=true 的结果，key=taskId）。
// 用于 A→B→A 往返切换时恢复已加载的完整 transcript/summary，以及
// 防止轻量响应的截断版 summary 覆盖完整版。内容可能变化的操作
// （重新转录/重新总结/重试失败分P）会显式失效对应条目。
const taskFullContentCache = new Map<string, Task>()

// include_content=true 请求的 in-flight 去重：同 id 并发（tab watch 与
// copyContent/downloadContent 同时触发）只发一次 GET，后续调用共享同一 Promise。
const taskFullContentPending = new Map<string, Promise<Task>>()

// 任务内容版本计数：WS 广播（内容/状态变化）与重试等操作递增版本。
// fetchTaskFullContent 发起时记录版本，响应返回时版本已变则跳过写缓存，
// 防止在途的旧完整响应重新污染缓存（re-transcribe 清缓存后的竞态）。
const taskContentVersion = new Map<string, number>()

const bumpTaskContentVersion = (taskId: string) => {
  taskContentVersion.set(taskId, (taskContentVersion.get(taskId) ?? 0) + 1)
}

// 内容判定辅助：'' 空串（如 re-transcribe 后端重置 transcript/summary）与 null
// 同样视为"无内容"，避免把 '' 当"已加载"导致滞留"暂无转录内容"。
const hasContent = (value?: string | null): boolean => value != null && value !== ''

// 任务内容合并守卫：把新到的任务数据（轻量列表项 / 广播）合并到已选任务时，
// 保护已加载的完整内容不被截断版 _summary_overview / 空值覆盖。
// - 默认（preserve）：base 的 transcript/summary 已加载（null 与 '' 均视为
//   未加载）时保留 base，防止轻量响应覆盖完整版；
// - preserveContent: false：内容以 incoming 为准（WS task_update 广播为 DB
//   全量权威行，含 re-transcribe/re-summarize 的 '' 重置必须传播；或内容版本
//   latest_modified_at 已变化，陈旧内容不得保留——incoming 无该字段时结果为
//   undefined，由懒加载 watch 补拉新内容）。
const mergeTaskWithPreservedContent = (
  base: Task,
  incoming: Task,
  options?: { preserveContent?: boolean },
): Task => {
  if (options?.preserveContent === false) {
    return { ...base, ...incoming, summary: incoming.summary, transcript: incoming.transcript }
  }
  return {
    ...base,
    ...incoming,
    summary: hasContent(base.summary) ? base.summary : incoming.summary,
    transcript: hasContent(base.transcript) ? base.transcript : incoming.transcript,
  }
}

// per-task 缓存条目可用性：缓存记录的是 fetch 时的内容版本（latest_modified_at）。
// 与当前任务值不一致说明内容已被其他端（双开标签页/断线期间 re-transcribe 等）
// 重置，不得恢复陈旧内容。
const isTaskCacheUsable = (cached: Task, task: Task): boolean =>
  cached.latest_modified_at == null
  || task.latest_modified_at == null
  || cached.latest_modified_at === task.latest_modified_at

// 处理中状态：内容将变化，命中时失效 per-task 缓存
const PROCESSING_STATUSES = new Set([
  'PENDING',
  'DOWNLOADING',
  'UPLOADING',
  'TRANSCRIBING',
  'SUMMARIZING',
])

// 测试专用钩子：清空 module 级缓存（生产代码不调用；防止测试间相互污染）
export const __resetTaskContentCaches = () => {
  taskFullContentCache.clear()
  taskFullContentPending.clear()
  taskContentVersion.clear()
}

/** 单文件上传大小上限回退值（后端 GET /upload/config 下发前的初始值/不可达时的兜底，默认 2GB） */
export const DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

export function useTaskViewModel() {
  const normalizeBase = (base?: string) => (base || '').trim().replace(/\/+$/, '')
  const isLoopbackHost = (host: string) => {
    const normalized = (host || '').trim().toLowerCase()
    return normalized === 'localhost' || normalized === '127.0.0.1' || normalized === '::1' || normalized === '[::1]'
  }
  const resolveHost = (baseUrl: string) => {
    try {
      if (!baseUrl) return window.location.hostname
      return new URL(baseUrl, window.location.origin).hostname
    } catch {
      return window.location.hostname
    }
  }
  const apiBaseUrl = normalizeBase(import.meta.env.VITE_API_BASE_URL)
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const wsBaseUrl = normalizeBase(import.meta.env.VITE_WS_BASE_URL) || `${wsProtocol}://${window.location.host}/ws`
  const apiHost = resolveHost(apiBaseUrl)
  const isLocalClient = isLoopbackHost(window.location.hostname) && isLoopbackHost(apiHost)
  
  // --- UI State ---
  const tasks = ref<Task[]>([])
  const queues = ref<QueueSnapshot[]>([])
  const selectedTask = ref<Task | null>(null)
  const taskParts = ref<TaskPart[]>([])
  const taskPartDetails = ref<Record<number, TaskPart>>({})
  const loadingPartIndex = ref<number | null>(null)
  const videoUrl = ref('')
  const selectedFile = ref<File | null>(null)
  const localFilePath = ref('')
  // UI 三态默认仅转录；'auto' 仅由后端/历史任务使用
  const summaryMode = ref<Exclude<SummaryMode, 'auto'>>('none')
  // 仅转录模式的"总结标题"开关：默认开启（与后端 generate_topic 默认一致）
  const generateTopic = ref(true)
  const isSubmitting = ref(false)
  // 文件上传真实进度（0-100，onUploadProgress 驱动；非上传提交时为 0）
  const uploadProgress = ref(0)
  // 上传大小上限（字节）：后端 /upload/config 下发，与 storage.max_upload_mb 同源
  const uploadMaxBytes = ref(DEFAULT_MAX_UPLOAD_BYTES)
  const error = ref<string | null>(null)
  const activeTab = ref<'summary' | 'transcript'>('summary')
  const isSidebarOpen = ref(false)
  const llmProviders = ref<LLMProvider[]>([])
  const llmSettings = ref<LLMSettings | null>(null)
  const isUpdatingLlmSettings = ref(false)
  const transcriptionSettings = ref<TranscriptionSettings | null>(null)
  const isUpdatingTranscriptionSettings = ref(false)
  const summarizationSettings = ref<SummarizationSettings | null>(null)
  const isUpdatingSummarizationSettings = ref(false)
  const isReadingBilibiliCookieFromBrowser = ref(false)
  const modelPathValidationResult = ref<ModelPathValidationResult | null>(null)
  const isValidatingModelPath = ref(false)
  const vibevoiceInferenceMode = ref<'local' | 'api'>(
    transcriptionSettings.value?.vibevoice_inference_mode || 'local'
  )
  const vibevoiceApiUrl = ref(
    transcriptionSettings.value?.vibevoice_api_url || ''
  )
  const vibevoiceServiceStatus = ref<VibeVoiceServiceStatus | null>(null)
  const isScanningVibeVoice = ref(false)
  const isStartingVibeVoice = ref(false)
  const isStoppingVibeVoice = ref(false)

  const clearModelPathValidation = () => {
    modelPathValidationResult.value = null
  }

  watch(transcriptionSettings, (settings) => {
    if (!settings) return
    vibevoiceInferenceMode.value = settings.vibevoice_inference_mode || 'local'
    vibevoiceApiUrl.value = settings.vibevoice_api_url || ''
  })

  let ws: WebSocket | null = null
  let submitAbortController: AbortController | null = null
  let taskPartsRefreshTimer: ReturnType<typeof setTimeout> | null = null

  // --- WS 生命周期（P2-A / S1 / S3）---
  // 组件已卸载标志：onUnmounted 置位；重连定时器回调先检查——防止卸载后
  // 每 3s 泄漏新连接（当前因 App 永不卸载而潜伏）。
  let wsDisposed = false
  // 重连指数退避：3s→6s→12s→24s→封顶 30s；onopen 成功后复位到 3s。
  const RECONNECT_BASE_DELAY_MS = 3_000
  const RECONNECT_MAX_DELAY_MS = 30_000
  // onerror 兜底（S1）：仅发 error 不发 close 的环境 ~5s 后重连一次
  const RECONNECT_ERROR_FALLBACK_MS = 5_000
  let reconnectAttempts = 0
  // 重连定时器 id（S1/S3）：onclose 退避与 onerror 兜底共用同一槽位互斥——
  // 后到者作废（防双调度）；onUnmounted 统一 clearTimeout 清理。
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  const scheduleReconnect = () => {
    if (wsDisposed) return
    if (reconnectTimer != null) return // 已排定（含 onerror 兜底）：不重复排定
    const delay = Math.min(
      RECONNECT_BASE_DELAY_MS * 2 ** reconnectAttempts,
      RECONNECT_MAX_DELAY_MS,
    )
    reconnectAttempts += 1
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      // 卸载前已排定的定时器：到期时组件可能已卸载，须再检查一次
      if (wsDisposed) return
      connectWebSocket()
    }, delay)
  }
  const scheduleReconnectErrorFallback = () => {
    if (wsDisposed) return
    if (reconnectTimer != null) return // onclose 已先行排定重连 → 兜底作废
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      if (wsDisposed) return
      connectWebSocket()
    }, RECONNECT_ERROR_FALLBACK_MS)
  }

  // 删除任务墓碑（P2-D）：删除成功后记录 id → 过期时间；WS task_update 广播
  // 命中时忽略，防止已删任务被在途广播"复活"；60s 后过期清理，防无限增长。
  const TOMBSTONE_TTL_MS = 60_000
  const deletedTaskIds = new Map<string, number>()
  // 墓碑过期清理定时器（S3）：id 留存，onUnmounted 统一 clearTimeout，
  // 防止卸载后残留定时器。
  const tombstoneCleanupTimers = new Map<string, ReturnType<typeof setTimeout>>()
  const isTaskDeleted = (taskId: string): boolean => {
    const expiresAt = deletedTaskIds.get(taskId)
    if (expiresAt == null) return false
    if (expiresAt <= Date.now()) {
      deletedTaskIds.delete(taskId) // 惰性过期清理
      return false
    }
    return true
  }

  // 轮询兜底（生产事故 93b857d0 修复）：WS 断流/丢广播时任务列表与队列仍能
  // 低频刷新。60s 一次，与 WS 并行无害；onUnmounted 清理。
  const POLL_INTERVAL_MS = 60_000
  let pollingTimer: ReturnType<typeof setInterval> | null = null

  // --- Actions ---
  const fetchTasks = async () => {
    try {
      const response = await apiClient.get('/tasks/')
      tasks.value = response.data
      
      // Sync selected task details
      if (selectedTask.value) {
        const current = tasks.value.find(t => t.id === selectedTask.value?.id)
        if (current) {
          // Merge updates so detail fields and part statistics are not lost.
          // 守卫：轻量列表项（后端剥离/可能携带截断版 summary）不得覆盖已加载的
          // 完整内容（WS 重连对账后详情回退截断版的修复点）。
          // latest_modified_at 变化（双开标签页/断线期间内容被另一标签页重置）
          // 时内容已陈旧：不得保留（以 incoming 为准），并失效 per-task 缓存，
          // 由懒加载 watch 补拉新内容。
          const selected = selectedTask.value
          const contentStale = selected.latest_modified_at != null
            && current.latest_modified_at != null
            && selected.latest_modified_at !== current.latest_modified_at
          selectedTask.value = mergeTaskWithPreservedContent(selected, current, {
            preserveContent: !contentStale,
          })
          if (contentStale) {
            taskFullContentCache.delete(current.id)
            bumpTaskContentVersion(current.id)
          }
          scheduleTaskPartsRefresh(current.id)
        }
      }
    } catch (err) {
      console.error('Failed to fetch tasks:', err)
      error.value = '获取任务列表失败'
    }
  }

  const fetchQueueSnapshot = async () => {
    try {
      const response = await apiClient.get<QueueResponse>('/tasks/queue')
      queues.value = response.data?.queues ?? []
    } catch (err) {
      // 队列快照为辅助信息：失败时保留上一次快照，不影响任务列表主流程
      console.error('Failed to fetch queue snapshot:', err)
    }
  }

  const submitTask = async () => {
    // localhost 场景优先使用本地路径直读（避免文件上传复制）
    if (isLocalClient && localFilePath.value.trim()) {
      await submitLocalPathTask(localFilePath.value)
      return
    }

    // 文件上传优先
    if (selectedFile.value) {
      await uploadFile(selectedFile.value)
      return
    }

    // URL 提交
    const resolvedUrl = extractFirstUrl(videoUrl.value)
    if (!resolvedUrl) {
      error.value = '请输入有效视频链接，或粘贴包含链接的文本。'
      return
    }

    uploadProgress.value = 0
    const controller = new AbortController()
    submitAbortController = controller
    isSubmitting.value = true
    error.value = null
    try {
      // summary_mode：UI 三态（none=仅转录 / standard=标准 / agent=Agent）。
      // 'auto' 仅作后端/历史任务兼容值，UI 不再发送。
      // generate_topic 仅在仅转录模式下随开关显式发送（true/false）；
      // 标准/Agent 模式本就会生成总结并带出标题，不发送该字段。
      const payload: CreateTaskRequest = {
        video_url: resolvedUrl,
        // quality 恒为默认值（无 UI 消费），显式常量保持行为等价
        quality: 'audio_only',
        summary_mode: summaryMode.value,
        ...(summaryMode.value === 'none'
          ? { generate_topic: generateTopic.value }
          : {}),
      }
      await apiClient.post('/tasks/', payload, {
        signal: controller.signal
      })
      videoUrl.value = ''
      // No need to fetchTasks here, WS will notify
    } catch (err) {
      if (isCanceledRequest(err)) {
        return
      }
      console.error('Failed to submit task:', err)
      error.value = getAxiosErrorMessage(err, '提交任务失败')
    } finally {
      if (submitAbortController === controller) {
        submitAbortController = null
        isSubmitting.value = false
      }
    }
  }

  const submitLocalPathTask = async (filePath: string) => {
    const normalized = filePath.trim()
    if (!normalized) {
      error.value = '请输入本地文件路径'
      return
    }

    uploadProgress.value = 0
    const controller = new AbortController()
    submitAbortController = controller
    isSubmitting.value = true
    error.value = null
    try {
      await apiClient.post('/upload/local-path', {
        file_path: normalized,
        summary_mode: summaryMode.value,
        ...(summaryMode.value === 'none'
          ? { generate_topic: generateTopic.value }
          : {}),
      }, {
        signal: controller.signal
      })
      localFilePath.value = ''
      selectedFile.value = null
    } catch (err) {
      if (isCanceledRequest(err)) {
        return
      }
      console.error('Failed to submit local path task:', err)
      error.value = getAxiosErrorMessage(err, '本地路径提交失败')
    } finally {
      if (submitAbortController === controller) {
        submitAbortController = null
        isSubmitting.value = false
      }
    }
  }

  // 拉取上传配置（大小上限与后端同源）；失败静默回退默认值，不阻塞 UI
  const fetchUploadConfig = async () => {
    try {
      const resp = await apiClient.get('/upload/config')
      const mb = Number(resp.data?.max_upload_mb)
      if (mb && mb > 0) {
        uploadMaxBytes.value = Math.round(mb * 1024 * 1024)
      }
    } catch (err) {
      console.warn('获取上传配置失败，使用默认上限:', err)
    }
  }

  const uploadFile = async (file: File) => {
    const controller = new AbortController()
    submitAbortController = controller
    isSubmitting.value = true
    error.value = null
    uploadProgress.value = 0
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('summary_mode', summaryMode.value)
      if (summaryMode.value === 'none') {
        formData.append('generate_topic', String(generateTopic.value))
      }

      await apiClient.post('/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        },
        signal: controller.signal,
        // 上传不设总时长超时（timeout: 0，保持旧行为）：axios timeout 为
        // "整请求总时长"（不被 onUploadProgress 重置），600s 会误杀 <3.4MB/s
        // 慢链路大文件上传。不无限挂起的三层兜底：进度条实时可见 +
        // 后端 Content-Length 预检/流式 413 + M3 断连核对。
        timeout: 0,
        onUploadProgress: (event) => {
          if (event.total && event.total > 0) {
            uploadProgress.value = Math.round((event.loaded / event.total) * 100)
          }
        }
      })

      selectedFile.value = null
      localFilePath.value = ''
      // No need to fetchTasks here, WS will notify
    } catch (err) {
      if (isCanceledRequest(err)) {
        return
      }
      console.error('Failed to upload file:', err)
      // 上传失败（含后端 413）清空文件选择，避免残留文件可重复提交
      selectedFile.value = null
      error.value = getAxiosErrorMessage(err, '上传失败')
    } finally {
      if (submitAbortController === controller) {
        submitAbortController = null
        isSubmitting.value = false
        uploadProgress.value = 0
      }
    }
  }

  const cancelSubmitting = () => {
    if (submitAbortController) {
      submitAbortController.abort()
      submitAbortController = null
    }
    isSubmitting.value = false
    uploadProgress.value = 0
  }

  const fetchTaskFullContent = (taskId: string): Promise<Task> => {
    // 同 id 并发请求去重：直接复用进行中的 Promise，避免重复 GET
    const existing = taskFullContentPending.get(taskId)
    if (existing) return existing

    // 发起时记录内容版本：请求期间任务内容若发生变化（WS 广播递增版本），
    // 响应返回时跳过写缓存，避免在途的旧内容重新污染缓存。
    const version = taskContentVersion.get(taskId) ?? 0
    const promise = (async () => {
      try {
        const response = await apiClient.get(`/tasks/${taskId}?include_content=true`)
        const data = response.data as Task
        if ((taskContentVersion.get(taskId) ?? 0) === version) {
          // 拷贝后入缓存：避免与响应对象共享引用（响应对象可能在别处被合并修改）
          taskFullContentCache.set(taskId, { ...data })
        }
        if (selectedTask.value?.id === taskId) {
          if ((taskContentVersion.get(taskId) ?? 0) === version) {
            selectedTask.value = { ...selectedTask.value, ...data }
          } else {
            // 版本已变（期间收到过内容/状态广播）：旧响应只合并非内容字段，
            // transcript/summary 以最新广播为准，防止过期在途响应覆盖新内容。
            selectedTask.value = {
              ...selectedTask.value,
              ...data,
              transcript: selectedTask.value.transcript,
              summary: selectedTask.value.summary,
            }
          }
        }
        return data
      } finally {
        taskFullContentPending.delete(taskId)
      }
    })()

    taskFullContentPending.set(taskId, promise)
    return promise
  }

  const fetchTaskParts = async (taskId: string) => {
    const response = await apiClient.get(`/tasks/${taskId}/parts`)
    if (selectedTask.value?.id === taskId) {
      taskParts.value = response.data
    }
    return response.data as TaskPart[]
  }

  const fetchTaskPart = async (taskId: string, partIndex: number) => {
    const cached = taskPartDetails.value[partIndex]
    if (cached?.task_id === taskId && (cached.transcript !== undefined || cached.summary !== undefined)) {
      return cached
    }

    loadingPartIndex.value = partIndex
    try {
      const response = await apiClient.get(
        `/tasks/${taskId}/parts/${partIndex}`,
      )
      const detail = response.data as TaskPart
      if (selectedTask.value?.id === taskId) {
        taskPartDetails.value = {
          ...taskPartDetails.value,
          [partIndex]: detail,
        }
      }
      return detail
    } finally {
      if (loadingPartIndex.value === partIndex) {
        loadingPartIndex.value = null
      }
    }
  }

  const scheduleTaskPartsRefresh = (taskId: string) => {
    if (!selectedTask.value || selectedTask.value.id !== taskId || !selectedTask.value.has_parts) {
      return
    }
    if (taskPartsRefreshTimer) {
      clearTimeout(taskPartsRefreshTimer)
    }
    taskPartsRefreshTimer = setTimeout(() => {
      taskPartsRefreshTimer = null
      fetchTaskParts(taskId).catch(err => {
        console.error('Failed to refresh task parts:', err)
      })
    }, 400)
  }

  const selectTask = (task: Task) => {
    // Show lightweight metadata immediately. Preserve already loaded content when
    // WebSocket reconnects or the same task is selected again.
    const current = selectedTask.value
    const preserveLoadedContent = current?.id === task.id
      && (current.summary !== undefined || current.transcript !== undefined)
    if (preserveLoadedContent) {
      // 同任务重选/WS 重连：合并轻量更新，保留已加载的完整内容
      // （轻量响应的 summary 是 _summary_overview 截断版，不能覆盖完整版）。
      // '' 空串（re-transcribe 重置）视为未加载：回退到轻量值，
      // 由 watch 在原文/summary tab 下重新按需加载（自愈）。
      selectedTask.value = mergeTaskWithPreservedContent(task, current)
    } else {
      // 切到新任务：立即用 per-task 缓存补齐已加载过的完整内容（A→B→A 往返恢复）。
      // latest_modified_at 不一致说明缓存内容已陈旧（双开标签页/断线期间被重置），
      // 不得恢复，由懒加载 watch 补拉新内容。
      const cached = taskFullContentCache.get(task.id)
      if (cached && isTaskCacheUsable(cached, task)) {
        selectedTask.value = {
          ...cached,
          ...task,
          transcript: task.transcript ?? cached.transcript,
          summary: task.summary ?? cached.summary,
        }
      } else {
        if (cached) {
          taskFullContentCache.delete(task.id)
        }
        selectedTask.value = task
      }
      taskParts.value = []
      taskPartDetails.value = {}
      loadingPartIndex.value = null
    }
    if (PROCESSING_STATUSES.has(task.status)) {
      activeTab.value = 'summary'
    }

    void (async () => {
      try {
        const detailPromise = apiClient.get(`/tasks/${task.id}?include_content=false`)
        const partsPromise = task.has_parts
          ? fetchTaskParts(task.id)
          : Promise.resolve([] as TaskPart[])
        const [response] = await Promise.all([detailPromise, partsPromise])
        if (selectedTask.value?.id !== task.id) return
        // 浅拷贝响应对象：后续合并会写 transcript/summary 字段，
        // 不能污染 axios 响应（同一响应对象可能被复用/共享）。
        const data = { ...(response.data as Task) }
        // 1) 保留当前选中任务已加载的完整内容（WS 广播合并等路径可能不经缓存）。
        //    '' 空串（re-transcribe 重置）不视为已加载，避免滞留"暂无转录内容"。
        if (hasContent(selectedTask.value.transcript) && data.transcript == null) {
          data.transcript = selectedTask.value.transcript
        }
        if (hasContent(selectedTask.value.summary)) {
          data.summary = selectedTask.value.summary
        }
        // 2) per-task 缓存补齐：切走再回来时恢复该任务已加载的完整内容。
        //    轻量响应的 summary 是 _summary_overview 截断版，缓存有完整版时优先。
        //    latest_modified_at 不一致（缓存陈旧）时不得恢复。
        const cached = taskFullContentCache.get(task.id)
        if (cached && isTaskCacheUsable(cached, task)) {
          if (data.transcript == null && hasContent(cached.transcript)) {
            data.transcript = cached.transcript
          }
          if (hasContent(cached.summary)) {
            data.summary = cached.summary
          }
        }
        selectedTask.value = data
      } catch (err) {
        console.error('Failed to fetch task details:', err)
        if (selectedTask.value?.id === task.id) {
          error.value = '获取任务详情失败'
          // 详情加载失败：summary 标记为"未加载"（null），使 summary watch 判定
          // 补拉完整内容（否则停留在列表项 summary=undefined，watch 永不触发）。
          // 已加载内容不覆盖（避免详情刷新失败误清显示）。transcript watch 对
          // undefined 同样视为未加载，天然对称，无需处理。
          if (!hasContent(selectedTask.value.summary)) {
            selectedTask.value = { ...selectedTask.value, summary: null }
          }
        }
      }
    })()
  }

  // 选中任务后详情接口只返回轻量内容（transcript 被后端剥离为 null）。
  // 用户切换到“原文”tab 或切换任务时（activeTab 保持 'transcript' 不触发
  // 单一 activeTab 依赖的 watch）按需加载完整内容（含转录原文）。
  // hasContent 守卫：null 与 ''（re-transcribe 后端重置）都视为未加载，
  // 触发一次加载后依赖（[activeTab, id]）不变不会重发；同时防止跨任务
  // stale 泄漏（新任务的轻量对象 transcript 为 null，旧内容不会被误判为已加载）。
  watch(
    [activeTab, () => selectedTask.value?.id],
    ([tab]) => {
      if (tab !== 'transcript') return
      const task = selectedTask.value
      if (!task || hasContent(task.transcript)) return
      fetchTaskFullContent(task.id).catch((err) => {
        console.error('Failed to load full content for transcript tab:', err)
      })
    },
    { immediate: true },
  )

  // summary tab 按需加载（与原文 tab 同款懒加载）：摘要未加载（null / ''，
  // 如 re-summarize 重置后断线丢广播）时补拉完整内容，修复总结 tab 长期显示
  // 截断版 _summary_overview / 空内容的问题。
  // - summary 依赖纳入 watch：轻量详情合并（undefined → null/截断版）后重新判定；
  // - 轻量详情在途（summary === undefined，详情接口未返回）时不抢跑，
  //   保持"summary tab 轻量预览"语义（截断版预览不触发加载）；
  // - 进行中任务（内容将变化，广播会携带新内容）不触发加载。
  watch(
    [activeTab, () => selectedTask.value?.id, () => selectedTask.value?.summary],
    ([tab]) => {
      if (tab !== 'summary') return
      const task = selectedTask.value
      if (!task) return
      if (PROCESSING_STATUSES.has(task.status)) return
      if (task.summary === undefined) return
      if (hasContent(task.summary)) return
      fetchTaskFullContent(task.id).catch((err) => {
        console.error('Failed to load full content for summary tab:', err)
      })
    },
    { immediate: true },
  )

  const retryFailedParts = async (taskId: string) => {
    await apiClient.post(`/tasks/${taskId}/retry-failed-parts`)
    // 重试后分片内容会变化（DB 中 content 已置 NULL）：
    // 失效 per-task 完整内容缓存与分片详情缓存，避免返回旧内容；
    // 同时递增版本，使在途的旧完整响应不写回缓存。
    taskFullContentCache.delete(taskId)
    bumpTaskContentVersion(taskId)
    taskPartDetails.value = {}
    const current = selectedTask.value
    if (current && current.id === taskId) {
      await selectTask(current)
    }
  }

  const downloadContent = async (type: 'summary' | 'transcript') => {
    if (!selectedTask.value) return
    const taskId = selectedTask.value.id

    // 轻量详情不含完整内容：下载前按需加载，避免下载到截断版 summary / 空 transcript
    if (type === 'transcript' && !selectedTask.value.transcript) {
      try {
        await fetchTaskFullContent(taskId)
      } catch (err) {
        console.error('Failed to load transcript before download:', err)
      }
    } else if (type === 'summary' && !selectedTask.value.summary) {
      try {
        await fetchTaskFullContent(taskId)
      } catch (err) {
        console.error('Failed to load summary before download:', err)
      }
    }

    // 等待期间用户可能已切换任务：中止下载
    if (!selectedTask.value || selectedTask.value.id !== taskId) return

    const topic = selectedTask.value.topic || selectedTask.value.title || new Date().toLocaleString('zh-CN').replace(/[/:]/g, '-')
    let content = ''
    let filename = ''

    if (type === 'summary') {
      content = selectedTask.value.summary || ''
      filename = `AI总结-${topic}.md`
    } else {
      content = selectedTask.value.transcript || ''
      filename = `视频转录-${topic}.txt`
    }

    const blob = new Blob([content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  const connectWebSocket = () => {
    ws = new WebSocket(wsBaseUrl)

    ws.onopen = () => {
      console.log('WebSocket connected')
      // 重连成功：退避复位到 3s
      reconnectAttempts = 0
      // Fetch latest state on reconnection to sync any missed updates
      fetchTasks()
      fetchQueueSnapshot()
      // 上传配置对账（P2-C）：后端运行期改 max_upload_mb 后预检不陈旧
      fetchUploadConfig()
      // Also refresh the selected task details if one is selected
      const currentTask = selectedTask.value
      if (currentTask) {
        selectTask(currentTask)
      }
    }

    ws.onmessage = (event) => {
      // 畸形帧（非 JSON）防护（P2-A）：console.warn + 跳过该帧，
      // 不影响后续消息；ping 分支在 parse 之后——防护包裹整个消息处理。
      // 协议边界：WS 帧为宽松结构，沿用原 any 语义（无隐式类型契约）。
      let data: any
      try {
        data = JSON.parse(event.data)
      } catch (parseErr) {
        console.warn('忽略非 JSON 的 WS 消息:', event.data, parseErr)
        return
      }
      if (data.type === 'ping') {
        // 双向心跳：服务端每 ~30s 发 ping，回 pong 保持连接活性；
        // 非 OPEN 状态（连接建立中/关闭中）静默忽略。
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'pong' }))
        }
        return
      }
      // 未知 type 消息（旧客户端不认识的新协议扩展）走默认分支忽略，不崩溃。
      if (data.type === 'task_update') {
        const updatedTask = data.task
        // 删除墓碑（P2-D）：已删任务的在途广播不得复活（合并/unshift 均忽略）
        if (isTaskDeleted(updatedTask.id)) return
        const index = tasks.value.findIndex(t => t.id === updatedTask.id)
        if (index !== -1) {
          tasks.value[index] = { ...tasks.value[index], ...updatedTask }
        } else {
          tasks.value.unshift(updatedTask)
        }

        const currentSelected = selectedTask.value
        if (currentSelected && currentSelected.id === updatedTask.id) {
          // Merge updates to preserve details that might not be in the broadcast.
          // 广播内容权威：WS task_update 为 DB 全量行（含 re-transcribe 的 '' 重置），
          // 内容字段以广播为准，不做 hasContent 保留（守卫统一但语义不弱化）。
          selectedTask.value = mergeTaskWithPreservedContent(
            currentSelected,
            updatedTask,
            { preserveContent: false },
          )
          scheduleTaskPartsRefresh(updatedTask.id)
        }

        // WS 广播是任务内容的权威来源：
        // - 处理中状态广播（重试/重新转录/重新总结）清空缓存（内容将变化），
        //   并递增版本，使在途旧完整响应不写回缓存；
        // - 非处理中广播若携带完整内容（后端广播原始 DB 任务，含完整
        //   transcript/summary），无条件同步缓存（无旧条目时直接写入），
        //   避免仅收到最终 COMPLETED 广播时 A→B→A 回退到旧内容；
        //   同时递增版本，防止在途旧响应覆盖刚同步的新内容。
        const hasCached = taskFullContentCache.has(updatedTask.id)
        if (hasCached || taskFullContentPending.has(updatedTask.id)) {
          bumpTaskContentVersion(updatedTask.id)
        }
        if (PROCESSING_STATUSES.has(updatedTask.status)) {
          taskFullContentCache.delete(updatedTask.id)
        } else if (updatedTask.transcript != null || updatedTask.summary != null) {
          const prev = taskFullContentCache.get(updatedTask.id)
          taskFullContentCache.set(updatedTask.id, prev
            ? {
                ...prev,
                ...updatedTask,
                transcript: updatedTask.transcript ?? prev.transcript,
                summary: updatedTask.summary ?? prev.summary,
              }
            : { ...updatedTask })
          bumpTaskContentVersion(updatedTask.id)
        }

        // 可选字段：后端广播的队列快照（旧版本无该字段时忽略，向后兼容）
        if (Array.isArray(data.queues)) {
          queues.value = data.queues as QueueSnapshot[]
        }
      } else if (data.type === 'progress_update') {
        const { task_id, progress } = data
        const task = tasks.value.find(t => t.id === task_id)
        if (task) {
          task.progress = progress
        }
        if (selectedTask.value && selectedTask.value.id === task_id) {
          selectedTask.value.progress = progress
        }
      }
    }

    ws.onclose = () => {
      console.log('WebSocket disconnected, retrying...')
      // 重连统一由 onclose 排定（指数退避）；卸载后不再重连
      scheduleReconnect()
    }

    ws.onerror = (err) => {
      // 不主动 close（P2-A）：浏览器在 error 后必发 close 事件，由 onclose
      // 统一排定重连；onerror 里 close 会再触发 onclose → 双调度。
      console.error('WebSocket error:', err)
      // 兜底重连（S1）：部分环境只发 error 不发 close（重连永久停滞风险）——
      // 排定 ~5s 兜底定时器；onclose 已先行排定重连时作废（槽位互斥）。
      scheduleReconnectErrorFallback()
    }
  }

  const fetchLlmProviders = async () => {
    try {
      const response = await apiClient.get('/llm/providers')
      llmProviders.value = response.data
    } catch (err) {
      console.error('Failed to fetch LLM providers:', err)
      error.value = '获取 LLM 供应商列表失败'
    }
  }

  const fetchLlmSettings = async () => {
    try {
      const response = await apiClient.get('/llm/settings')
      llmSettings.value = response.data
    } catch (err) {
      console.error('Failed to fetch LLM settings:', err)
      error.value = '获取 LLM 配置失败'
    }
  }

  const updateLlmSettings = async (payload: UpdateLLMSettingsRequest) => {
    isUpdatingLlmSettings.value = true
    try {
      const response = await apiClient.put('/llm/settings', payload)
      llmSettings.value = response.data
      return response.data as LLMSettings
    } catch (err) {
      console.error('Failed to update LLM settings:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '更新 LLM 配置失败'
      } else {
        error.value = '更新 LLM 配置失败'
      }
      throw err
    } finally {
      isUpdatingLlmSettings.value = false
    }
  }

  const fetchTranscriptionSettings = async () => {
    try {
      const response = await apiClient.get('/transcription/settings')
      transcriptionSettings.value = response.data
    } catch (err) {
      console.error('Failed to fetch transcription settings:', err)
      error.value = '获取转录配置失败'
    }
  }

  const updateTranscriptionSettings = async (payload: UpdateTranscriptionSettingsRequest) => {
    isUpdatingTranscriptionSettings.value = true
    error.value = null
    try {
      const response = await apiClient.put('/transcription/settings', payload)
      transcriptionSettings.value = response.data
      return response.data as TranscriptionSettings
    } catch (err) {
      console.error('Failed to update transcription settings:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '更新转录配置失败'
      } else {
        error.value = '更新转录配置失败'
      }
      throw err
    } finally {
      isUpdatingTranscriptionSettings.value = false
    }
  }

  const validateModelPath = async (request: ModelPathValidationRequest): Promise<ModelPathValidationResult> => {
    isValidatingModelPath.value = true
    modelPathValidationResult.value = null
    try {
      const response = await apiClient.post('/transcription/settings/validate-model-path', request)
      modelPathValidationResult.value = response.data
      return response.data as ModelPathValidationResult
    } catch (err) {
      console.error('Failed to validate model path:', err)
      const result: ModelPathValidationResult = {
        valid: false,
        message: isAxiosError(err) && err.response?.data?.detail
          ? String(err.response.data.detail)
          : '验证请求失败',
        resolved_path: request.path,
        missing_files: [],
        has_processor_config: false,
        details: {},
      }
      modelPathValidationResult.value = result
      throw err
    } finally {
      isValidatingModelPath.value = false
    }
  }

  const scanVibeVoiceServices = async (): Promise<VibeVoiceServiceScanResult[]> => {
    isScanningVibeVoice.value = true
    try {
      const response = await apiClient.post('/transcription/settings/vibevoice-scan')
      return response.data as VibeVoiceServiceScanResult[]
    } catch (err) {
      console.error('Failed to scan VibeVoice services:', err)
      return []
    } finally {
      isScanningVibeVoice.value = false
    }
  }

  const startVibeVoiceService = async (modelPath: string, port: number, dtype: string): Promise<void> => {
    isStartingVibeVoice.value = true
    try {
      await apiClient.post('/transcription/settings/vibevoice-service/start', {
        model_path: modelPath,
        port,
        dtype,
      })
    } finally {
      isStartingVibeVoice.value = false
    }
  }

  const stopVibeVoiceService = async (): Promise<void> => {
    isStoppingVibeVoice.value = true
    try {
      await apiClient.post('/transcription/settings/vibevoice-service/stop')
    } finally {
      isStoppingVibeVoice.value = false
    }
  }

  const fetchVibeVoiceServiceStatus = async (): Promise<VibeVoiceServiceStatus> => {
    const response = await apiClient.get('/transcription/settings/vibevoice-service/status')
    vibevoiceServiceStatus.value = response.data
    return response.data as VibeVoiceServiceStatus
  }

  const testLlm = async () => {
    try {
      const response = await apiClient.post('/llm/test')
      return response.data
    } catch (err) {
      console.error('Failed to test LLM:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '测试 LLM 失败'
      } else {
        error.value = '测试 LLM 失败'
      }
      throw err
    }
  }

  const fetchSummarizationSettings = async () => {
    try {
      const response = await apiClient.get('/summarization/settings')
      summarizationSettings.value = response.data
    } catch (err) {
      console.error('Failed to fetch summarization settings:', err)
      error.value = '获取总结配置失败'
    }
  }

  const updateSummarizationSettings = async (payload: UpdateSummarizationSettingsRequest) => {
    isUpdatingSummarizationSettings.value = true
    try {
      const response = await apiClient.put('/summarization/settings', payload)
      summarizationSettings.value = response.data
      return response.data as SummarizationSettings
    } catch (err) {
      console.error('Failed to update summarization settings:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '更新总结配置失败'
      } else {
        error.value = '更新总结配置失败'
      }
      throw err
    } finally {
      isUpdatingSummarizationSettings.value = false
    }
  }

  const readBilibiliCookieFromBrowser = async (): Promise<BilibiliCookieFromBrowserResult> => {
    isReadingBilibiliCookieFromBrowser.value = true
    try {
      const response = await apiClient.post('/transcription/settings/bilibili-cookie/from-browser')
      const result = response.data as BilibiliCookieFromBrowserResult
      if (result.success) {
        // Refresh transcription settings to reflect the new cookie
        await fetchTranscriptionSettings()
      }
      return result
    } catch (err) {
      console.error('Failed to read Bilibili cookie from browser:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '从浏览器读取 Cookie 失败'
      } else {
        error.value = '从浏览器读取 Cookie 失败'
      }
      throw err
    } finally {
      isReadingBilibiliCookieFromBrowser.value = false
    }
  }

  const checkBilibiliVideoInfo = async (url: string): Promise<BilibiliVideoInfo | null> => {
    try {
      const response = await apiClient.post('/bilibili/video-info', { url })
      return response.data as BilibiliVideoInfo
    } catch (err) {
      console.error('Failed to check Bilibili video info:', err)
      return null
    }
  }

  const checkLocalPath = async (filePath: string): Promise<LocalPathCheckResult | null> => {
    try {
      const response = await apiClient.get('/local-path/check', {
        params: { file_path: filePath }
      })
      return response.data as LocalPathCheckResult
    } catch (err) {
      console.error('Failed to check local path:', err)
      return null
    }
  }

  const scanLocalFolder = async (folderPath: string): Promise<LocalFolderScanResult | null> => {
    try {
      const response = await apiClient.get('/local-folder/scan', {
        params: { folder_path: folderPath }
      })
      return response.data as LocalFolderScanResult
    } catch (err) {
      console.error('Failed to scan local folder:', err)
      return null
    }
  }

  const submitLocalPathTasks = async (
    paths: string[],
    mode: 'merge' | 'separate'
  ): Promise<void> => {
    const controller = new AbortController()
    submitAbortController = controller
    isSubmitting.value = true
    error.value = null

    try {
      if (mode === 'merge') {
        // 合并模式：暂时不支持，需要后端支持
        error.value = '合并多个文件功能开发中，请选择"拆分为多个任务"'
        throw new Error('Merge mode not supported yet')
      } else {
        // 分别模式：逐个提交
        for (const path of paths) {
          await apiClient.post('/upload/local-path', {
            file_path: path,
            summary_mode: summaryMode.value,
            ...(summaryMode.value === 'none'
              ? { generate_topic: generateTopic.value }
              : {}),
          }, {
            signal: controller.signal
          })
        }
      }
    } catch (err) {
      if (isCanceledRequest(err)) {
        return
      }
      console.error('Failed to submit local path tasks:', err)
      if (!error.value) {
        error.value = getAxiosErrorMessage(err, '提交任务失败')
      }
      throw err
    } finally {
      if (submitAbortController === controller) {
        submitAbortController = null
        isSubmitting.value = false
      }
    }
  }

  const submitTaskWithParts = async (
    videoUrl: string,
    partsConfig: BilibiliPartsConfig,
    abortSignal?: AbortSignal
  ): Promise<void> => {
    const controller = new AbortController()
    submitAbortController = controller
    isSubmitting.value = true
    error.value = null

    try {
      const payload = {
        video_url: videoUrl,
        // quality 恒为默认值（无 UI 消费），显式常量保持行为等价
        quality: 'audio_only',
        summary_mode: summaryMode.value,
        bilibili_parts: partsConfig,
        ...(summaryMode.value === 'none'
          ? { generate_topic: generateTopic.value }
          : {}),
      }
      await apiClient.post('/tasks/', payload, {
        signal: abortSignal || controller.signal
      })
    } catch (err) {
      if (isCanceledRequest(err)) {
        return
      }
      console.error('Failed to submit task with parts:', err)
      error.value = getAxiosErrorMessage(err, '提交任务失败')
      throw err
    } finally {
      if (submitAbortController === controller) {
        submitAbortController = null
        isSubmitting.value = false
      }
    }
  }

  // --- Lifecycle ---
  onMounted(() => {
    fetchTasks()
    fetchQueueSnapshot()
    fetchUploadConfig()

    fetchLlmProviders()
    fetchLlmSettings()
    fetchTranscriptionSettings()
    fetchSummarizationSettings()
    connectWebSocket()

    // 轮询兜底：WS 断流时列表/队列仍低频刷新（fetchTasks 已有 P1 守卫，
    // 不重置已选中任务的完整内容；与 WS 并行无害）。
    pollingTimer = setInterval(() => {
      fetchTasks()
      fetchQueueSnapshot()
    }, POLL_INTERVAL_MS)
  })

  onUnmounted(() => {
    // 先置位卸载标志：ws.close() 触发的 onclose 不得再排定重连（P2-A）
    wsDisposed = true
    // 主动清理排定的重连定时器（S3）：即使回调已有 wsDisposed 双检查
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    // 清理墓碑过期清理定时器（S3）
    for (const timer of tombstoneCleanupTimers.values()) {
      clearTimeout(timer)
    }
    tombstoneCleanupTimers.clear()
    if (ws) {
      ws.close()
    }
    if (pollingTimer) {
      clearInterval(pollingTimer)
      pollingTimer = null
    }
    if (taskPartsRefreshTimer) {
      clearTimeout(taskPartsRefreshTimer)
      taskPartsRefreshTimer = null
    }
  })

  return {
    // State
    tasks,
    queues,
    fetchQueueSnapshot,
    fetchUploadConfig,
    selectedTask,
    taskParts,
    taskPartDetails,
    loadingPartIndex,
    videoUrl,
    selectedFile,
    localFilePath,
    isLocalClient,
    summaryMode,
    generateTopic,
    isSubmitting,
    uploadProgress,
    uploadMaxBytes,
    error,
    activeTab,
    isSidebarOpen,
    llmProviders,
    llmSettings,
    isUpdatingLlmSettings,
    fetchTaskParts,
    fetchTaskPart,
    fetchTaskFullContent,
    retryFailedParts,

    transcriptionSettings,
    isUpdatingTranscriptionSettings,
    summarizationSettings,
    isUpdatingSummarizationSettings,
    isReadingBilibiliCookieFromBrowser,
    modelPathValidationResult,
    isValidatingModelPath,
    vibevoiceInferenceMode,
    vibevoiceApiUrl,
    vibevoiceServiceStatus,
    isScanningVibeVoice,
    isStartingVibeVoice,
    isStoppingVibeVoice,

    // Actions
    submitTask,
    submitLocalPathTask,
    uploadFile,
    cancelSubmitting,
    selectTask,
    fetchTasks,
    fetchLlmProviders,
    fetchLlmSettings,
    updateLlmSettings,
    fetchTranscriptionSettings,
    updateTranscriptionSettings,
    validateModelPath,
    scanVibeVoiceServices,
    startVibeVoiceService,
    stopVibeVoiceService,
    fetchVibeVoiceServiceStatus,
    clearModelPathValidation,
    fetchSummarizationSettings,
    updateSummarizationSettings,
    testLlm,
    readBilibiliCookieFromBrowser,
    checkBilibiliVideoInfo,
    submitTaskWithParts,
    checkLocalPath,
    scanLocalFolder,
    submitLocalPathTasks,
    isBilibiliUrl,
    downloadContent,
    copyContent: async (type: 'summary' | 'transcript') => {
      if (!selectedTask.value) return false
      // await 前捕获任务身份：等待期间用户可能已切换任务
      const taskId = selectedTask.value.id

      // 轻量详情不包含转录原文（transcript 为 null），复制前先按需加载完整内容；
      // summary 为 null（如“仅转录”模式）时同样先加载完整内容。
      if (type === 'transcript' && selectedTask.value.transcript == null) {
        try {
          await fetchTaskFullContent(taskId)
        } catch (err) {
          console.error('Failed to load transcript before copy:', err)
        }
      } else if (type === 'summary' && selectedTask.value.summary == null) {
        try {
          await fetchTaskFullContent(taskId)
        } catch (err) {
          console.error('Failed to load summary before copy:', err)
        }
      }

      // 等待期间切换了任务：中止复制，不复制旧任务内容、不弹错误提示
      if (!selectedTask.value || selectedTask.value.id !== taskId) return false

      // 直接使用当前 selectedTask 的数据，与 compiledMarkdown 保持一致
      let text = ''
      if (type === 'summary') {
        text = selectedTask.value.summary || ''
      } else {
        text = selectedTask.value.transcript || ''
      }

      if (!text) return false

      try {
        // 优先使用现代 Clipboard API（需要安全上下文：HTTPS 或 localhost）
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text)
          return true
        }

        // 降级到传统方法（兼容非安全上下文，如局域网 HTTP）
        return fallbackCopyToClipboard(text)
      } catch (err) {
        console.error('Failed to copy text:', err)
        // 如果现代 API 失败，尝试降级方法
        try {
          return fallbackCopyToClipboard(text)
        } catch (fallbackErr) {
          console.error('Fallback copy also failed:', fallbackErr)
          return false
        }
      }
    },
    deleteTask: async (taskId: string) => {
      if (!confirm('确定要删除这个任务吗？此操作不可恢复。')) {
        return false
      }
      try {
        await apiClient.delete(`/tasks/${taskId}`)
        tasks.value = tasks.value.filter(t => t.id !== taskId)
        if (selectedTask.value?.id === taskId) {
          selectedTask.value = null
        }
        // 删除墓碑（P2-D）：记录 id 防止在途广播复活；60s 后过期清理（S3：
        // 定时器 id 留存，onUnmounted 统一清理）
        deletedTaskIds.set(taskId, Date.now() + TOMBSTONE_TTL_MS)
        tombstoneCleanupTimers.set(taskId, setTimeout(() => {
          tombstoneCleanupTimers.delete(taskId)
          deletedTaskIds.delete(taskId)
        }, TOMBSTONE_TTL_MS))
        return true
      } catch (err) {
        console.error('Failed to delete task:', err)
        error.value = '删除任务失败'
        return false
      }
    },
    reSummarize: async (taskId: string, mode?: SummaryMode) => {
      try {
        // summary_mode：优先显式模式（如补总结入口指定 standard/agent）；
        // 缺省沿用 UI 三态（none/standard/agent）。'none' 时后端会按
        // auto 判定兜底生成总结（见 llm_worker._resolve_effective_mode）。
        await apiClient.post(`/tasks/${taskId}/re-summarize`, {
          summary_mode: mode ?? summaryMode.value
        })
        // No need to do more, WS will update the status
      } catch (err) {
        console.error('Failed to re-summarize task:', err)
        error.value = isAxiosError(err) ? err.response?.data?.detail || "重新总结失败" : "重新总结失败"
      }
    },
    reTranscribe: async (taskId: string) => {
      // summary_mode：UI 三态（none/standard/agent）。'none' 重新转录会清空
      // 现有 AI 总结（后端重置 summary 为空且不再生成），确认条件为"content 或
      // 模式信号"：
      // - content 信号：target.summary 有内容；
      // - 模式信号：target.summary_mode ∈ {standard, agent}（此类任务大概率已有
      //   总结）。覆盖两个静默跳过窗口——轻量详情在途/失败（selectedTask 缺
      //   summary）与传非选中任务 id（列表接口剥离 summary 字段）。
      const target = selectedTask.value?.id === taskId
        ? selectedTask.value
        : tasks.value.find((t) => t.id === taskId) ?? null
      const hasSummarySignal = target
        && (hasContent(target.summary)
          || target.summary_mode === 'standard'
          || target.summary_mode === 'agent')
      if (summaryMode.value === 'none' && hasSummarySignal) {
        if (!confirm('重新转录将清除现有 AI 总结，确定继续？')) return
      }
      try {
        await apiClient.post(`/tasks/${taskId}/re-transcribe`, {
          summary_mode: summaryMode.value
        })
        // No need to do more, WS will update the status
      } catch (err) {
        console.error('Failed to re-transcribe task:', err)
        if (isAxiosError(err) && err.response) {
          error.value = err.response.data?.detail || '重新转录失败'
        } else {
          error.value = '重新转录失败'
        }
      }
    },
    reDownloadAudio: async (taskId: string) => {
      try {
        await apiClient.post(`/tasks/${taskId}/re-download`)
        // No need to do more, WS will update the status
      } catch (err) {
        console.error('Failed to re-download task audio:', err)
        if (isAxiosError(err) && err.response) {
          error.value = err.response.data?.detail || '重新下载音频失败'
        } else {
          error.value = '重新下载音频失败'
        }
      }
    },
    updateTaskTopic: async (taskId: string, newTopic: string) => {
      try {
        await apiClient.patch(`/tasks/${taskId}`, { topic: newTopic })
        // WS will update the task list and selected task
      } catch (err) {
        console.error('Failed to update task topic:', err)
        error.value = '更新主题失败'
        throw err
      }
    }
  }
}
