/**
 * P7 任务域状态（features/task/state.ts）——从 useTaskViewModel 按域拆分。
 *
 * 语义逐条平移（行为等价铁律，p7-composable-spec.md §3.1）：
 * - per-task 完整内容缓存（A→B→A 往返恢复）+ in-flight 去重 + 内容版本计数；
 * - mergeTaskWithPreservedContent 合并守卫（默认 preserve / 广播权威 preserve:false）；
 * - 懒加载 watch：原文 tab 与 summary tab 按需补拉 include_content=true；
 * - deleteTask 墓碑（60s TTL，防在途广播复活）；
 * - reTranscribe 清空总结确认窗口（★ D1：summaryMode 改为显式 mode 参数，
 *   装配层传 upload.summaryMode.value，本域对 upload 零 import）；
 * - WS 订阅（syncWithWs）：task_update 合并/墓碑过滤/缓存守卫/queues 可选字段、
 *   progress_update 写进度、status==='open' 重连对账（fetchTasks +
 *   fetchQueueSnapshot + selectTask(currentTask)）；
 * - 轮询兜底 60s（startPolling/stopPolling，由装配层生命周期驱动）。
 *
 * 依赖方向：仅 shared/api、shared/ws、src/types。
 */
import { ref, watch } from 'vue'
import type { Ref } from 'vue'
import { apiClient, isAxiosError } from '../../shared/api/client'
import type { WsClient } from '../../shared/ws'
import type {
  Task,
  TaskPart,
  QueueSnapshot,
  QueueResponse,
  SummaryMode,
  ReSummarizeRequest,
  ReTranscribeRequest,
} from '../../types'

// 传统复制方法（兼容非安全上下文，如局域网 HTTP）——copyContent 降级路径。
// 注：仅本域 copyContent 消费，故随 task 域（规格 §1.2 行区间为近似划分）。
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

export interface TaskState {
  // --- state（全部 Ref，语义与现状逐键一致）---
  tasks: Ref<Task[]>
  queues: Ref<QueueSnapshot[]>
  selectedTask: Ref<Task | null>
  taskParts: Ref<TaskPart[]>
  taskPartDetails: Ref<Record<number, TaskPart>>
  loadingPartIndex: Ref<number | null>
  activeTab: Ref<'summary' | 'transcript'>
  isSidebarOpen: Ref<boolean>
  error: Ref<string | null>

  // --- actions（签名 = 现状 export 面，逐键不变）---
  fetchTasks(): Promise<void>
  fetchQueueSnapshot(): Promise<void>
  selectTask(task: Task): void
  fetchTaskFullContent(taskId: string): Promise<Task>
  fetchTaskParts(taskId: string): Promise<TaskPart[]>
  fetchTaskPart(taskId: string, partIndex: number): Promise<TaskPart | undefined>
  retryFailedParts(taskId: string): Promise<void>
  downloadContent(type: 'summary' | 'transcript'): Promise<void>
  copyContent(type: 'summary' | 'transcript'): Promise<boolean>
  deleteTask(taskId: string): Promise<boolean>
  reSummarize(taskId: string, mode?: SummaryMode): Promise<void>
  reTranscribe(taskId: string, mode?: SummaryMode): Promise<void>
  reDownloadAudio(taskId: string): Promise<void>
  updateTaskTopic(taskId: string, newTopic: string): Promise<void>

  // --- WS 挂接（装配层调用一次；重复调用幂等） ---
  syncWithWs(ws: WsClient): void

  // --- 生命周期（D4：装配层驱动；各 state 不自行注册生命周期钩子） ---
  /** 启动 60s 轮询兜底（WS 断流时列表/队列低频刷新），幂等 */
  startPolling(): void
  /** 停止轮询兜底，幂等 */
  stopPolling(): void
  /** 清理轮询/墓碑过期/分P刷新定时器（装配层 onUnmounted 调用），幂等 */
  dispose(): void
}

// api adapter 注入点（规格 §4 方案 A：模块级单例；可选签名保留为未来 seam 扩展）
export type TaskApiAdapter = typeof apiClient

export function useTaskState(_options?: { api?: TaskApiAdapter }): TaskState {
  // --- UI State ---
  const tasks = ref<Task[]>([])
  const queues = ref<QueueSnapshot[]>([])
  const selectedTask = ref<Task | null>(null)
  const taskParts = ref<TaskPart[]>([])
  const taskPartDetails = ref<Record<number, TaskPart>>({})
  const loadingPartIndex = ref<number | null>(null)
  const error = ref<string | null>(null)
  const activeTab = ref<'summary' | 'transcript'>('summary')
  const isSidebarOpen = ref(false)

  // 删除任务墓碑（P2-D）：删除成功后记录 id → 过期时间；WS task_update 广播
  // 命中时忽略，防止已删任务被在途广播"复活"；60s 后过期清理，防无限增长。
  const TOMBSTONE_TTL_MS = 60_000
  const deletedTaskIds = new Map<string, number>()
  // 墓碑过期清理定时器（S3）：id 留存，dispose 统一 clearTimeout，
  // 防止残留定时器。
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
  // 低频刷新。60s 一次，与 WS 并行无害；装配层生命周期驱动启停。
  const POLL_INTERVAL_MS = 60_000
  let pollingTimer: ReturnType<typeof setInterval> | null = null
  // 分P内容刷新防抖定时器
  let taskPartsRefreshTimer: ReturnType<typeof setTimeout> | null = null

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
  // 用户切换到"原文"tab 或切换任务时（activeTab 保持 'transcript' 不触发
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

  const copyContent = async (type: 'summary' | 'transcript'): Promise<boolean> => {
    if (!selectedTask.value) return false
    // await 前捕获任务身份：等待期间用户可能已切换任务
    const taskId = selectedTask.value.id

    // 轻量详情不包含转录原文（transcript 为 null），复制前先按需加载完整内容；
    // summary 为 null（如"仅转录"模式）时同样先加载完整内容。
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
  }

  const deleteTask = async (taskId: string): Promise<boolean> => {
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
      // 定时器 id 留存，dispose 统一清理）
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
  }

  const reSummarize = async (taskId: string, mode?: SummaryMode) => {
    try {
      // summary_mode：优先显式模式（如补总结入口指定 standard/agent）；
      // 缺省即 'none'（D1 决策，与 reTranscribe 的缺省一致）——'none' 时后端
      // 会按 auto 判定兜底生成总结（见 llm_worker._resolve_effective_mode）。
      // App 装配层 handleReSummarize(taskId, mode ?? upload.summaryMode.value)
      // 覆盖全部生产调用路径（mode 恒有值），域层缺省 'none' 仅为防御性兜底
      // （对应旧实现 summaryMode.value 的 UI 初始值，不构成行为偏差）。
      // D1：显式 mode 参数由装配层传入 upload.summaryMode.value，本域不读 upload。
      const payload: ReSummarizeRequest = {
        summary_mode: mode ?? 'none'
      }
      await apiClient.post(`/tasks/${taskId}/re-summarize`, payload)
      // No need to do more, WS will update the status
    } catch (err) {
      console.error('Failed to re-summarize task:', err)
      error.value = isAxiosError(err) ? err.response?.data?.detail || "重新总结失败" : "重新总结失败"
    }
  }

  const reTranscribe = async (taskId: string, mode?: SummaryMode) => {
    // summary_mode：UI 三态（none/standard/agent）。'none' 重新转录会清空
    // 现有 AI 总结（后端重置 summary 为空且不再生成），确认条件为"content 或
    // 模式信号"：
    // - content 信号：target.summary 有内容；
    // - 模式信号：target.summary_mode ∈ {standard, agent}（此类任务大概率已有
    //   总结）。覆盖两个静默跳过窗口——轻量详情在途/失败（selectedTask 缺
    //   summary）与传非选中任务 id（列表接口剥离 summary 字段）。
    // D1：显式 mode 参数由装配层传入 upload.summaryMode.value，本域对 upload 零 import。
    const effectiveMode = mode ?? 'none'
    const target = selectedTask.value?.id === taskId
      ? selectedTask.value
      : tasks.value.find((t) => t.id === taskId) ?? null
    const hasSummarySignal = target
      && (hasContent(target.summary)
        || target.summary_mode === 'standard'
        || target.summary_mode === 'agent')
    if (effectiveMode === 'none' && hasSummarySignal) {
      if (!confirm('重新转录将清除现有 AI 总结，确定继续？')) return
    }
    try {
      // generate_topic 不发送：缺省沿用任务已存值（见 ReTranscribeRequest 契约）
      const payload: ReTranscribeRequest = {
        summary_mode: effectiveMode
      }
      await apiClient.post(`/tasks/${taskId}/re-transcribe`, payload)
      // No need to do more, WS will update the status
    } catch (err) {
      console.error('Failed to re-transcribe task:', err)
      if (isAxiosError(err) && err.response) {
        error.value = err.response.data?.detail || '重新转录失败'
      } else {
        error.value = '重新转录失败'
      }
    }
  }

  const reDownloadAudio = async (taskId: string) => {
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
  }

  const updateTaskTopic = async (taskId: string, newTopic: string) => {
    try {
      await apiClient.patch(`/tasks/${taskId}`, { topic: newTopic })
      // WS will update the task list and selected task
    } catch (err) {
      console.error('Failed to update task topic:', err)
      error.value = '更新主题失败'
      throw err
    }
  }

  // --- WS 挂接（规格 §5.2：事件 → 状态更新在订阅层做） ---
  let wsSynced = false
  const syncWithWs = (ws: WsClient) => {
    if (wsSynced) return
    wsSynced = true

    // task_update：墓碑过滤 → 合并列表/选中任务 → 缓存守卫与版本递增 → 分P刷新
    ws.subscribe('task_update', (msg) => {
      const updatedTask = msg.task
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
      if (Array.isArray(msg.queues)) {
        queues.value = msg.queues as QueueSnapshot[]
      }
    })

    // progress_update：写列表与选中任务进度
    ws.subscribe('progress_update', (msg) => {
      const { task_id, progress } = msg
      const task = tasks.value.find(t => t.id === task_id)
      if (task) {
        task.progress = progress
      }
      if (selectedTask.value && selectedTask.value.id === task_id) {
        selectedTask.value.progress = progress
      }
    })

    // 重连对账（现 onopen 行为，触发权从 ws.ts 转移到订阅层）：
    // status==='open'（首次连接与每次重连）→ fetchTasks + fetchQueueSnapshot +
    // selectTask(currentTask)。任务域各自决定重连时做什么，ws.ts 保持业务无关。
    watch(() => ws.status.value === 'open', (isOpen) => {
      if (!isOpen) return
      // Fetch latest state on reconnection to sync any missed updates
      fetchTasks()
      fetchQueueSnapshot()
      // Also refresh the selected task details if one is selected
      const currentTask = selectedTask.value
      if (currentTask) {
        selectTask(currentTask)
      }
    })
  }

  // --- 生命周期（D4：装配层驱动） ---
  const startPolling = () => {
    if (pollingTimer != null) return
    // 轮询兜底：WS 断流时列表/队列仍低频刷新（fetchTasks 已有 P1 守卫，
    // 不重置已选中任务的完整内容；与 WS 并行无害）。
    pollingTimer = setInterval(() => {
      fetchTasks()
      fetchQueueSnapshot()
    }, POLL_INTERVAL_MS)
  }

  const stopPolling = () => {
    if (pollingTimer != null) {
      clearInterval(pollingTimer)
      pollingTimer = null
    }
  }

  const dispose = () => {
    // 清理墓碑过期清理定时器（S3）
    for (const timer of tombstoneCleanupTimers.values()) {
      clearTimeout(timer)
    }
    tombstoneCleanupTimers.clear()
    if (taskPartsRefreshTimer) {
      clearTimeout(taskPartsRefreshTimer)
      taskPartsRefreshTimer = null
    }
    stopPolling()
  }

  return {
    // State
    tasks,
    queues,
    selectedTask,
    taskParts,
    taskPartDetails,
    loadingPartIndex,
    activeTab,
    isSidebarOpen,
    error,
    // Actions
    fetchTasks,
    fetchQueueSnapshot,
    selectTask,
    fetchTaskFullContent,
    fetchTaskParts,
    fetchTaskPart,
    retryFailedParts,
    downloadContent,
    copyContent,
    deleteTask,
    reSummarize,
    reTranscribe,
    reDownloadAudio,
    updateTaskTopic,
    // WS 挂接
    syncWithWs,
    // 生命周期
    startPolling,
    stopPolling,
    dispose,
  }
}
