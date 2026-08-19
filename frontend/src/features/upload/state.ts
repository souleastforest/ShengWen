/**
 * P7 上传域状态（features/upload/state.ts）——从 useTaskViewModel 按域拆分。
 *
 * 语义逐条平移（行为等价铁律，p7-composable-spec.md §3.2）：
 * - 三通道提交优先级：localhost 本地路径 → 文件 → URL；
 * - quality='audio_only' 显式常量；generate_topic 仅 summary_mode==='none' 发送；
 * - payload 取消走 isCanceledRequest 静默返回；提交成功后不主动 fetchTasks
 *   （WS 通知，行为不变）；
 * - uploadFile：onUploadProgress 真实进度 + timeout:0（整请求总时长误杀慢链路
 *   大文件上传）+ 413 清空 selectedFile；
 * - cancelSubmitting：AbortController.abort + isSubmitting/uploadProgress 复位；
 * - fetchUploadConfig：失败静默回退 DEFAULT_MAX_UPLOAD_BYTES；
 * - submitLocalPathTasks merge 模式报"开发中"；
 * - summaryMode/generateTopic 归属（★ D1）：reTranscribe/reSummarize 的
 *   summary_mode 由装配层显式传参，本域不 import task；
 * - syncWithWs：status==='open' → fetchUploadConfig（上传配置对账，P2-C）。
 *
 * 依赖方向：仅 shared/api、shared/ws、src/types。
 */
import { ref, watch } from 'vue'
import type { Ref } from 'vue'
import {
  apiClient,
  getAxiosErrorMessage,
  isCanceledRequest,
} from '../../shared/api/client'
import type { WsClient } from '../../shared/ws'
import type {
  CreateTaskRequest,
  LocalPathCreateTaskRequest,
  SummaryMode,
  BilibiliVideoInfo,
  BilibiliPartsConfig,
  LocalPathCheckResult,
  LocalFolderScanResult,
} from '../../types'

// 传统 URL 提取：从自由文本中解析首个可提交链接（含裸域名补 https:// 前缀）。
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

/** 单文件上传大小上限回退值（后端 GET /upload/config 下发前的初始值/不可达时的兜底，默认 2GB） */
export const DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

/**
 * B 站分P探针结果（P0-3）：结构化区分"失败"与"单P"，调用方不得再以
 * null 吞掉探针异常后静默提交（2026-08-19 缺陷：DNS 故障 + 前端吞错 →
 * 盲建单P任务，多P视频静默只处理第一P）。
 */
export type BilibiliVideoInfoCheckResult =
  | { ok: true; info: BilibiliVideoInfo }
  | { ok: false; error: unknown }

export interface UploadState {
  videoUrl: Ref<string>
  selectedFile: Ref<File | null>
  localFilePath: Ref<string>
  /** env 判定：localhost 场景优先本地路径直读（Sidebar 显示 + submitTask 路由） */
  isLocalClient: Ref<boolean>
  summaryMode: Ref<Exclude<SummaryMode, 'auto'>>
  generateTopic: Ref<boolean>
  isSubmitting: Ref<boolean>
  uploadProgress: Ref<number>
  uploadMaxBytes: Ref<number>
  error: Ref<string | null>

  submitTask(): Promise<void>
  submitLocalPathTask(filePath: string): Promise<void>
  uploadFile(file: File): Promise<void>
  cancelSubmitting(): void
  fetchUploadConfig(): Promise<void>
  submitLocalPathTasks(paths: string[], mode: 'merge' | 'separate'): Promise<void>
  submitTaskWithParts(videoUrl: string, partsConfig: BilibiliPartsConfig, abortSignal?: AbortSignal): Promise<void>
  checkBilibiliVideoInfo(url: string): Promise<BilibiliVideoInfoCheckResult>
  checkLocalPath(filePath: string): Promise<LocalPathCheckResult | null>
  scanLocalFolder(folderPath: string): Promise<LocalFolderScanResult | null>
  isBilibiliUrl(url: string): boolean

  /** WS 挂接（装配层调用一次）：status==='open' → fetchUploadConfig 对账 */
  syncWithWs(ws: WsClient): void
}

// api adapter 注入点（规格 §4 方案 A：模块级单例；可选签名保留为未来 seam 扩展）
export type UploadApiAdapter = typeof apiClient

export function useUploadState(_options?: { api?: UploadApiAdapter }): UploadState {
  // env 判定（与 useTaskViewModel 内嵌实现逐条等价）：
  // localhost 场景（浏览器与 API 同机回环）→ 提交优先本地路径直读
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
  const apiHost = resolveHost(apiBaseUrl)
  const isLocalClient = ref(
    isLoopbackHost(window.location.hostname) && isLoopbackHost(apiHost)
  )

  // --- UI State ---
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

  let submitAbortController: AbortController | null = null

  const submitTask = async () => {
    // localhost 场景优先使用本地路径直读（避免文件上传复制）
    if (isLocalClient.value && localFilePath.value.trim()) {
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
      const payload: LocalPathCreateTaskRequest = {
        file_path: normalized,
        summary_mode: summaryMode.value,
        ...(summaryMode.value === 'none'
          ? { generate_topic: generateTopic.value }
          : {}),
      }
      await apiClient.post('/upload/local-path', payload, {
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

  const checkBilibiliVideoInfo = async (url: string): Promise<BilibiliVideoInfoCheckResult> => {
    try {
      const response = await apiClient.post('/bilibili/video-info', { url })
      return { ok: true, info: response.data as BilibiliVideoInfo }
    } catch (err) {
      console.error('Failed to check Bilibili video info:', err)
      // 结构化失败结果：调用方可区分"探针失败"与"确认单P"，不得静默提交
      return { ok: false, error: err }
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
      // 形状与 submitTask 共用 CreateTaskRequest；bilibili_parts 恢复类型检查
      const payload: CreateTaskRequest = {
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

  // --- WS 挂接（规格 §5.2）：上传配置对账（P2-C） ---
  // 后端运行期改 max_upload_mb 后，WS 重连 status==='open' → 重新拉取，
  // 使 uploadMaxBytes 与后端同源刷新。
  let wsSynced = false
  const syncWithWs = (ws: WsClient) => {
    if (wsSynced) return
    wsSynced = true
    watch(() => ws.status.value === 'open', (isOpen) => {
      if (!isOpen) return
      fetchUploadConfig()
    })
  }

  return {
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
    submitTask,
    submitLocalPathTask,
    uploadFile,
    cancelSubmitting,
    fetchUploadConfig,
    submitLocalPathTasks,
    submitTaskWithParts,
    checkBilibiliVideoInfo,
    checkLocalPath,
    scanLocalFolder,
    isBilibiliUrl,
    syncWithWs,
  }
}
