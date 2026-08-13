import { ref, onMounted, onUnmounted, watch } from 'vue'
import axios from 'axios'
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

const getAxiosErrorMessage = (err: unknown, fallback: string): string => {
  if (!axios.isAxiosError(err)) return fallback

  const detail = err.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }

  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0]
    if (first && typeof first === 'object' && 'msg' in first) {
      const message = String((first as { msg?: unknown }).msg || '').trim()
      if (message) {
        return `请求参数错误：${message}`
      }
    }
  }

  return err.message ? `${fallback}：${err.message}` : fallback
}

const isCanceledRequest = (err: unknown): boolean => {
  if (axios.isCancel(err)) return true
  if (!axios.isAxiosError(err)) return false
  return err.code === 'ERR_CANCELED'
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

/** 单文件上传大小上限（与后端 config.storage.max_upload_mb 保持一致，默认 2GB） */
export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

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
  const quality = ref('audio_only')
  // UI 三态默认仅转录；'auto' 仅由后端/历史任务使用
  const summaryMode = ref<Exclude<SummaryMode, 'auto'>>('none')
  // 仅转录模式的"总结标题"开关：默认开启（与后端 generate_topic 默认一致）
  const generateTopic = ref(true)
  const isSubmitting = ref(false)
  // 文件上传真实进度（0-100，onUploadProgress 驱动；非上传提交时为 0）
  const uploadProgress = ref(0)
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

  // --- Actions ---
  const fetchTasks = async () => {
    try {
      const response = await axios.get(apiBaseUrl + "/tasks/")
      tasks.value = response.data
      
      // Sync selected task details
      if (selectedTask.value) {
        const current = tasks.value.find(t => t.id === selectedTask.value?.id)
        if (current) {
          // Merge updates so detail fields and part statistics are not lost.
          selectedTask.value = { ...selectedTask.value, ...current }
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
      const response = await axios.get<QueueResponse>(apiBaseUrl + "/tasks/queue")
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
        quality: quality.value,
        summary_mode: summaryMode.value,
        ...(summaryMode.value === 'none'
          ? { generate_topic: generateTopic.value }
          : {}),
      }
      await axios.post(`${apiBaseUrl}/tasks/`, payload, {
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
      await axios.post(`${apiBaseUrl}/upload/local-path`, {
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

      await axios.post(`${apiBaseUrl}/upload`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        },
        signal: controller.signal,
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
        const response = await axios.get(`${apiBaseUrl}/tasks/${taskId}?include_content=true`)
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
    const response = await axios.get(apiBaseUrl + "/tasks/" + taskId + "/parts")
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
      const response = await axios.get(
        apiBaseUrl + "/tasks/" + taskId + "/parts/" + partIndex,
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
      // 由 watch 在原文 tab 下重新按需加载（自愈）。
      selectedTask.value = {
        ...task,
        ...current,
        summary: hasContent(current.summary) ? current.summary : task.summary,
        transcript: hasContent(current.transcript) ? current.transcript : task.transcript,
      }
    } else {
      // 切到新任务：立即用 per-task 缓存补齐已加载过的完整内容（A→B→A 往返恢复）
      const cached = taskFullContentCache.get(task.id)
      if (cached) {
        selectedTask.value = {
          ...cached,
          ...task,
          transcript: task.transcript ?? cached.transcript,
          summary: task.summary ?? cached.summary,
        }
      } else {
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
        const detailPromise = axios.get(`${apiBaseUrl}/tasks/${task.id}?include_content=false`)
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
        const cached = taskFullContentCache.get(task.id)
        if (cached) {
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

  const retryFailedParts = async (taskId: string) => {
    await axios.post(apiBaseUrl + "/tasks/" + taskId + "/retry-failed-parts")
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
      // Fetch latest state on reconnection to sync any missed updates
      fetchTasks()
      fetchQueueSnapshot()
      // Also refresh the selected task details if one is selected
      const currentTask = selectedTask.value
      if (currentTask) {
        selectTask(currentTask)
      }
    }
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      if (data.type === 'task_update') {
        const updatedTask = data.task
        const index = tasks.value.findIndex(t => t.id === updatedTask.id)
        if (index !== -1) {
          tasks.value[index] = { ...tasks.value[index], ...updatedTask }
        } else {
          tasks.value.unshift(updatedTask)
        }

        if (selectedTask.value?.id === updatedTask.id) {
          // Merge updates to preserve details that might not be in the broadcast.
          selectedTask.value = { ...selectedTask.value, ...updatedTask }
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
      console.log('WebSocket disconnected, retrying in 3s...')
      setTimeout(connectWebSocket, 3000)
    }

    ws.onerror = (err) => {
      console.error('WebSocket error:', err)
      ws?.close()
    }
  }

  const fetchLlmProviders = async () => {
    try {
      const response = await axios.get(`${apiBaseUrl}/llm/providers`)
      llmProviders.value = response.data
    } catch (err) {
      console.error('Failed to fetch LLM providers:', err)
      error.value = '获取 LLM 供应商列表失败'
    }
  }

  const fetchLlmSettings = async () => {
    try {
      const response = await axios.get(`${apiBaseUrl}/llm/settings`)
      llmSettings.value = response.data
    } catch (err) {
      console.error('Failed to fetch LLM settings:', err)
      error.value = '获取 LLM 配置失败'
    }
  }

  const updateLlmSettings = async (payload: UpdateLLMSettingsRequest) => {
    isUpdatingLlmSettings.value = true
    try {
      const response = await axios.put(`${apiBaseUrl}/llm/settings`, payload)
      llmSettings.value = response.data
      return response.data as LLMSettings
    } catch (err) {
      console.error('Failed to update LLM settings:', err)
      if (axios.isAxiosError(err) && err.response) {
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
      const response = await axios.get(`${apiBaseUrl}/transcription/settings`)
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
      const response = await axios.put(`${apiBaseUrl}/transcription/settings`, payload)
      transcriptionSettings.value = response.data
      return response.data as TranscriptionSettings
    } catch (err) {
      console.error('Failed to update transcription settings:', err)
      if (axios.isAxiosError(err) && err.response) {
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
      const response = await axios.post(`${apiBaseUrl}/transcription/settings/validate-model-path`, request)
      modelPathValidationResult.value = response.data
      return response.data as ModelPathValidationResult
    } catch (err) {
      console.error('Failed to validate model path:', err)
      const result: ModelPathValidationResult = {
        valid: false,
        message: axios.isAxiosError(err) && err.response?.data?.detail
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
      const response = await axios.post(`${apiBaseUrl}/transcription/settings/vibevoice-scan`)
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
      await axios.post(`${apiBaseUrl}/transcription/settings/vibevoice-service/start`, {
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
      await axios.post(`${apiBaseUrl}/transcription/settings/vibevoice-service/stop`)
    } finally {
      isStoppingVibeVoice.value = false
    }
  }

  const fetchVibeVoiceServiceStatus = async (): Promise<VibeVoiceServiceStatus> => {
    const response = await axios.get(`${apiBaseUrl}/transcription/settings/vibevoice-service/status`)
    vibevoiceServiceStatus.value = response.data
    return response.data as VibeVoiceServiceStatus
  }

  const testLlm = async () => {
    try {
      const response = await axios.post(`${apiBaseUrl}/llm/test`)
      return response.data
    } catch (err) {
      console.error('Failed to test LLM:', err)
      if (axios.isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '测试 LLM 失败'
      } else {
        error.value = '测试 LLM 失败'
      }
      throw err
    }
  }

  const fetchSummarizationSettings = async () => {
    try {
      const response = await axios.get(`${apiBaseUrl}/summarization/settings`)
      summarizationSettings.value = response.data
    } catch (err) {
      console.error('Failed to fetch summarization settings:', err)
      error.value = '获取总结配置失败'
    }
  }

  const updateSummarizationSettings = async (payload: UpdateSummarizationSettingsRequest) => {
    isUpdatingSummarizationSettings.value = true
    try {
      const response = await axios.put(`${apiBaseUrl}/summarization/settings`, payload)
      summarizationSettings.value = response.data
      return response.data as SummarizationSettings
    } catch (err) {
      console.error('Failed to update summarization settings:', err)
      if (axios.isAxiosError(err) && err.response) {
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
      const response = await axios.post(`${apiBaseUrl}/transcription/settings/bilibili-cookie/from-browser`)
      const result = response.data as BilibiliCookieFromBrowserResult
      if (result.success) {
        // Refresh transcription settings to reflect the new cookie
        await fetchTranscriptionSettings()
      }
      return result
    } catch (err) {
      console.error('Failed to read Bilibili cookie from browser:', err)
      if (axios.isAxiosError(err) && err.response) {
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
      const response = await axios.post(`${apiBaseUrl}/bilibili/video-info`, { url })
      return response.data as BilibiliVideoInfo
    } catch (err) {
      console.error('Failed to check Bilibili video info:', err)
      return null
    }
  }

  const checkLocalPath = async (filePath: string): Promise<LocalPathCheckResult | null> => {
    try {
      const response = await axios.get(`${apiBaseUrl}/local-path/check`, {
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
      const response = await axios.get(`${apiBaseUrl}/local-folder/scan`, {
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
          await axios.post(`${apiBaseUrl}/upload/local-path`, {
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
        quality: quality.value,
        summary_mode: summaryMode.value,
        bilibili_parts: partsConfig,
        ...(summaryMode.value === 'none'
          ? { generate_topic: generateTopic.value }
          : {}),
      }
      await axios.post(`${apiBaseUrl}/tasks/`, payload, {
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

    fetchLlmProviders()
    fetchLlmSettings()
    fetchTranscriptionSettings()
    fetchSummarizationSettings()
    connectWebSocket()
  })

  onUnmounted(() => {
    if (ws) {
      ws.close()
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
    selectedTask,
    taskParts,
    taskPartDetails,
    loadingPartIndex,
    videoUrl,
    selectedFile,
    localFilePath,
    isLocalClient,
    quality,
    summaryMode,
    generateTopic,
    isSubmitting,
    uploadProgress,
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
        await axios.delete(`${apiBaseUrl}/tasks/${taskId}`)
        tasks.value = tasks.value.filter(t => t.id !== taskId)
        if (selectedTask.value?.id === taskId) {
          selectedTask.value = null
        }
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
        await axios.post(`${apiBaseUrl}/tasks/${taskId}/re-summarize`, {
          summary_mode: mode ?? summaryMode.value
        })
        // No need to do more, WS will update the status
      } catch (err) {
        console.error('Failed to re-summarize task:', err)
        error.value = axios.isAxiosError(err) ? err.response?.data?.detail || "重新总结失败" : "重新总结失败"
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
        await axios.post(`${apiBaseUrl}/tasks/${taskId}/re-transcribe`, {
          summary_mode: summaryMode.value
        })
        // No need to do more, WS will update the status
      } catch (err) {
        console.error('Failed to re-transcribe task:', err)
        if (axios.isAxiosError(err) && err.response) {
          error.value = err.response.data?.detail || '重新转录失败'
        } else {
          error.value = '重新转录失败'
        }
      }
    },
    reDownloadAudio: async (taskId: string) => {
      try {
        await axios.post(`${apiBaseUrl}/tasks/${taskId}/re-download`)
        // No need to do more, WS will update the status
      } catch (err) {
        console.error('Failed to re-download task audio:', err)
        if (axios.isAxiosError(err) && err.response) {
          error.value = err.response.data?.detail || '重新下载音频失败'
        } else {
          error.value = '重新下载音频失败'
        }
      }
    },
    updateTaskTopic: async (taskId: string, newTopic: string) => {
      try {
        await axios.patch(`${apiBaseUrl}/tasks/${taskId}`, { topic: newTopic })
        // WS will update the task list and selected task
      } catch (err) {
        console.error('Failed to update task topic:', err)
        error.value = '更新主题失败'
        throw err
      }
    }
  }
}
