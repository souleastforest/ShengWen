<script setup lang="ts">
import { computed, watch, ref, onMounted, onBeforeUnmount } from 'vue'
import { PhMonitorPlay, PhList } from '@phosphor-icons/vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useTaskViewModel } from './composables/useTaskViewModel'
import { useMermaidViewer } from './composables/useMermaidViewer'
import {
  useSummaryImageExporter,
  createDefaultSummaryImageExportSettings,
  type SummaryImagePreviewPage,
  type SummaryImageExportSettings,
  type SummaryImageLayoutPreset,
  type SummaryImageFormat,
  type SummaryImageMetaMode,
  type SummaryImageRenderProgress,
} from './composables/useSummaryImageExporter'
import { useToast } from './composables/useToast'
import { stripDoubleBracePlaceholders } from './utils/formatters'
import { postProcessCompiledMarkdown } from './utils/markdownPostProcessor'
import type { Task, MarkdownHeadingItem, BilibiliVideoInfo, BilibiliPartsConfig, LocalFolderScanResult } from './types'
import Sidebar from './components/Sidebar.vue'
import FloatingToolbar from './components/FloatingToolbar.vue'
import TaskInfoModal from './components/TaskInfoModal.vue'
import TaskContentArea from './components/TaskContentArea.vue'
import TaskPartsPanel from './components/TaskPartsPanel.vue'
import MermaidViewerModal from './components/MermaidViewerModal.vue'
import SummaryImageWorkbenchModal from './components/SummaryImageWorkbenchModal.vue'
import SettingsModal from './components/SettingsModal.vue'
import BilibiliPartsSelector from './components/BilibiliPartsSelector.vue'
import LocalFolderSelector from './components/LocalFolderSelector.vue'
import ToastContainer from './components/ToastContainer.vue'

const {
  tasks,
  selectedTask,
  taskParts,
  taskPartDetails,
  loadingPartIndex,
  fetchTaskPart,
  retryFailedParts,
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
  llmProviders,
  llmSettings,
  isUpdatingLlmSettings,
  transcriptionSettings,
  isUpdatingTranscriptionSettings,
  summarizationSettings,
  isUpdatingSummarizationSettings,
  isReadingBilibiliCookieFromBrowser,
  modelPathValidationResult,
  isValidatingModelPath,
  vibevoiceServiceStatus,
  isScanningVibeVoice,
  isStartingVibeVoice,
  isStoppingVibeVoice,
  queues,
  submitTask,
  cancelSubmitting,
  selectTask,
  fetchTaskFullContent,
  downloadContent,
  copyContent,
  isSidebarOpen,
  deleteTask,
  reSummarize,
  reTranscribe,
  reDownloadAudio,
  updateTaskTopic,
  updateLlmSettings,
  updateTranscriptionSettings,
  validateModelPath,
  scanVibeVoiceServices,
  startVibeVoiceService,
  stopVibeVoiceService,
  fetchVibeVoiceServiceStatus,
  clearModelPathValidation,
  updateSummarizationSettings,
  testLlm,
  readBilibiliCookieFromBrowser,
  checkBilibiliVideoInfo,
  submitTaskWithParts,
  isBilibiliUrl,
  checkLocalPath,
  scanLocalFolder,
  submitLocalPathTasks,
} = useTaskViewModel()

// 状态变量
const showInfoModal = ref(false)
const isSettingsModalOpen = ref(false)
const isEditingTopic = ref(false)
const editingTopicValue = ref('')
const isTestingLlm = ref(false)
const isRedownloading = ref(false)
// 分P内容刷新信号：重试失败分P时递增，TaskPartsPanel 据此收起残留展开
// （重试后后端置分P content 为 NULL，展开区不得滞留旧值）
const taskPartsRefreshKey = ref(0)
const summaryHighlightRequest = ref<{
  taskId: string
  keyword: string
  source: 'topic' | 'summary'
  requestId: number
} | null>(null)
const markdownHeadings = ref<MarkdownHeadingItem[]>([])
const activeHeadingId = ref('')
const headingJumpRequest = ref<{ id: string; requestId: number } | null>(null)
const headingJumpSeq = ref(0)

// B站分P选择器状态
const isBilibiliPartsSelectorOpen = ref(false)
const bilibiliVideoInfo = ref<BilibiliVideoInfo | null>(null)
const isCheckingBilibiliVideoInfo = ref(false)
const pendingBilibiliUrl = ref('')

// 本地文件夹选择器状态
const isLocalFolderSelectorOpen = ref(false)
const localFolderInfo = ref<LocalFolderScanResult | null>(null)
const isScanningLocalFolder = ref(false)

// Mermaid 查看器
const mermaidViewerModalRef = ref<{
  stage: HTMLElement | null
  viewport: HTMLElement | null
} | null>(null)

// Toast 通知
const { toasts, removeToast, success, info, error: toastError } = useToast()

// Toast 容器响应式定位：桌面 bottom-right / 移动端（<768px）bottom-center。
// P5 合并双容器（CSS `hidden md:block` / `block md:hidden`）为单实例 + position 切换——
// 隐藏实例不再挂载、不再绑定 hover/pause 事件；匹配 Tailwind md 断点（768px）。
const TOAST_MOBILE_QUERY = '(max-width: 767px)'
const toastPosition = ref<'bottom-right' | 'bottom-center'>('bottom-right')
let toastMediaQuery: MediaQueryList | null = null
let detachToastMediaListener: (() => void) | null = null

const handleToastViewportChange = (e: { matches: boolean }) => {
  toastPosition.value = e.matches ? 'bottom-center' : 'bottom-right'
}

onMounted(() => {
  if (typeof window === 'undefined') return
  toastMediaQuery = window.matchMedia(TOAST_MOBILE_QUERY)
  handleToastViewportChange(toastMediaQuery)
  if (typeof toastMediaQuery.addEventListener === 'function') {
    toastMediaQuery.addEventListener('change', handleToastViewportChange)
    detachToastMediaListener = () => {
      toastMediaQuery?.removeEventListener('change', handleToastViewportChange)
    }
  } else {
    // 旧浏览器回退（legacy addListener）
    toastMediaQuery.addListener(handleToastViewportChange)
    detachToastMediaListener = () => {
      toastMediaQuery?.removeListener(handleToastViewportChange)
    }
  }
})

onBeforeUnmount(() => {
  detachToastMediaListener?.()
  detachToastMediaListener = null
  toastMediaQuery = null
})

const {
  showMermaidViewer,
  currentMermaidSvg,
  currentZoom,
  openMermaidViewer,
  closeMermaidViewer,
  resetView,
  fitView,
  zoomIn,
  zoomOut,
} = useMermaidViewer(mermaidViewerModalRef)

const { exportSummaryAsImage, generateSummaryImagePreview } = useSummaryImageExporter()

const SUMMARY_IMAGE_SETTINGS_STORAGE_KEY = 'ShengWen:summary-image-export-settings'

const summaryLayoutOptions: Array<{ label: string; value: SummaryImageLayoutPreset }> = [
  { label: '9:16 手机竖版', value: 'mobile-9-16' },
  { label: '9:32 长屏', value: 'mobile-9-32' },
  { label: '9:64 超长图', value: 'mobile-9-64' },
  { label: '长图原始比例', value: 'long' },
]

const summaryMetaModeOptions: Array<{ label: string; value: SummaryImageMetaMode }> = [
  { label: '每个图片顶端都显示元信息', value: 'all-pages' },
  { label: '仅第一张图顶部显示元信息', value: 'first-page-only' },
]

const summaryFormatOptions: Array<{ label: string; value: SummaryImageFormat }> = [
  { label: 'JPEG（推荐）', value: 'jpeg' },
  { label: 'WebP（更小）', value: 'webp' },
  { label: 'PNG（无损）', value: 'png' },
]

const summaryWidthOptions = [960, 1080, 1242, 1440]
const summaryPixelRatioOptions = [1, 1.25, 1.5, 1.8, 2]

const loadSummaryImageSettings = (): SummaryImageExportSettings => {
  const defaults = createDefaultSummaryImageExportSettings()
  try {
    const raw = localStorage.getItem(SUMMARY_IMAGE_SETTINGS_STORAGE_KEY)
    if (!raw) return defaults
    const parsed = JSON.parse(raw) as Partial<SummaryImageExportSettings>
    const merged: SummaryImageExportSettings = {
      ...defaults,
      ...parsed,
    }
    if (!summaryLayoutOptions.some((option) => option.value === merged.layoutPreset)) {
      merged.layoutPreset = defaults.layoutPreset
    }
    if (!summaryMetaModeOptions.some((option) => option.value === merged.metaMode)) {
      merged.metaMode = defaults.metaMode
    }
    return merged
  } catch {
    return defaults
  }
}

const summaryImageSettings = ref<SummaryImageExportSettings>(loadSummaryImageSettings())
const isSummaryImageSettingsOpen = ref(false)
const isSummaryPreviewRendering = ref(false)
const summaryRenderProgress = ref<{ current: number; total: number } | null>(null)
const summaryPreviewDirty = ref(true)
const summaryPreviewPages = ref<SummaryImagePreviewPage[]>([])
const summaryPreviewActiveIndex = ref(0)
const summaryPreviewTotalSizeKB = ref(0)
const showAllPreviewPages = ref(false)

const getSummaryImageExportPayload = () => {
  if (!selectedTask.value?.summary) return null
  return {
    task: selectedTask.value,
    topic: topic.value || selectedTask.value.title || 'AI 总结',
    compiledMarkdown: compiledMarkdown.value,
    rawSummary: selectedTask.value.summary,
  }
}

const canRefreshSummaryPreview = computed(() => {
  if (!isSummaryImageSettingsOpen.value) return false
  if (!summaryPreviewDirty.value) return false
  if (isSummaryPreviewRendering.value) return false
  return !!getSummaryImageExportPayload()
})

const summaryRefreshButtonLabel = computed(() => {
  if (isSummaryPreviewRendering.value) return '重新生成中...'
  if (summaryPreviewDirty.value) return '参数已变更，点击刷新'
  return '预览已是最新'
})

let summaryPreviewSequence = 0

const refreshSummaryImagePreview = async (options?: { manual?: boolean; force?: boolean }) => {
  if (!isSummaryImageSettingsOpen.value) return

  const payload = getSummaryImageExportPayload()
  if (!payload) return

  if (!options?.force && !summaryPreviewDirty.value) return

  if (options?.manual) {
    info('正在重新生成预览图...')
  }

  const currentSequence = ++summaryPreviewSequence
  isSummaryPreviewRendering.value = true
  summaryRenderProgress.value = null
  summaryPreviewPages.value = []

  try {
    const preview = await generateSummaryImagePreview(
      payload,
      summaryImageSettings.value,
      (progress: SummaryImageRenderProgress) => {
        summaryRenderProgress.value = { current: progress.current, total: progress.total }
        if (progress.page) {
          summaryPreviewPages.value = [...summaryPreviewPages.value, progress.page]
        }
      },
    )
    if (currentSequence !== summaryPreviewSequence) return

    summaryPreviewTotalSizeKB.value = preview.totalSizeKB
    summaryPreviewActiveIndex.value = Math.min(
      summaryPreviewActiveIndex.value,
      Math.max(0, preview.pages.length - 1),
    )
    summaryPreviewDirty.value = false

    if (options?.manual) {
      success(`预览已更新：共 ${preview.pages.length} 张，约 ${preview.totalSizeKB}KB`)
    }
  } catch (_error) {
    if (currentSequence === summaryPreviewSequence) {
      toastError('预览生成失败，请调整参数后重试')
    }
  } finally {
    if (currentSequence === summaryPreviewSequence) {
      isSummaryPreviewRendering.value = false
      summaryRenderProgress.value = null
    }
  }
}

const handleOpenSummaryImageSettings = () => {
  if (!selectedTask.value?.summary) {
    toastError('暂无可导出的 AI 总结')
    return
  }

  isSummaryImageSettingsOpen.value = true
  showAllPreviewPages.value = false
  summaryPreviewActiveIndex.value = 0
  summaryPreviewDirty.value = true
  void refreshSummaryImagePreview({ force: true })
}

const handleRefreshSummaryImagePreview = () => {
  void refreshSummaryImagePreview({ manual: true })
}

const handleSelectPreviewPage = (index: number) => {
  if (index < 0 || index >= summaryPreviewPages.value.length) return
  summaryPreviewActiveIndex.value = index
}

const handlePreviewPagePrev = () => {
  if (summaryPreviewActiveIndex.value <= 0) return
  summaryPreviewActiveIndex.value -= 1
}

const handlePreviewPageNext = () => {
  if (summaryPreviewActiveIndex.value >= summaryPreviewPages.value.length - 1) return
  summaryPreviewActiveIndex.value += 1
}

const handleCloseSummaryImageSettings = () => {
  isSummaryImageSettingsOpen.value = false
}

const handleCloseViewer = () => {
  closeMermaidViewer()
}

// 包装删除任务，添加成功反馈
const handleDeleteTask = async (taskId: string) => {
  const result = await deleteTask(taskId)
  if (result) {
    success('任务已删除')
  }
}

const handleCopySummary = async () => {
  const result = await copyContent('summary')
  if (result) {
    success('AI 总结已复制到剪贴板')
  } else {
    toastError('复制失败，内容为空')
  }
}

const handleCopyTranscript = async () => {
  const result = await copyContent('transcript')
  if (result) {
    success('转录文本已复制到剪贴板')
  } else {
    toastError('复制失败，内容为空')
  }
}

const handleReSummarize = (taskId: string, mode?: 'standard' | 'agent') => {
  const { info } = useToast()
  info(mode ? `正在以${mode === 'agent' ? ' Agent' : ''}模式生成 AI 总结...` : '正在重新生成 AI 总结...')
  reSummarize(taskId, mode)
}

const handleReTranscribe = (taskId: string) => {
  const { info } = useToast()
  info('正在重新转录原文...')
  reTranscribe(taskId)
}

// Sidebar 快速重跑：FAILED 任务行的小圆钮 → 复用同一 re-transcribe 通道
const handleRetryTask = (task: Task) => {
  handleReTranscribe(task.id)
}

// 重试失败分P：先发刷新信号收起展开区（后端将清空分P content），再提交重试
const handleRetryFailedParts = async () => {
  if (!selectedTask.value) return
  taskPartsRefreshKey.value += 1
  await retryFailedParts(selectedTask.value.id)
}

// 重新下载音频：await 以便按钮 loading 防抖（reDownloadAudio 内部吞掉错误并写入 error）
const handleReDownload = async (taskId: string) => {
  const { info } = useToast()
  info('正在重新下载音频...')
  isRedownloading.value = true
  try {
    await reDownloadAudio(taskId)
  } finally {
    isRedownloading.value = false
  }
}

const handleDownloadMarkdown = async () => {
  success('开始下载 AI 总结...')
  // downloadContent 为异步：未加载完整内容时会先按需请求（避免下载到截断版）
  await downloadContent('summary')
}

const handleDownloadTxt = async () => {
  success('开始下载转录文本...')
  await downloadContent('transcript')
}

const handleTestLlm = async () => {
  isTestingLlm.value = true
  const { info, success, error, warning } = useToast()
  info('正在测试 LLM 连接...')

  try {
    const result = await testLlm()

    if (result.status === 'success') {
      success(result.message)
    } else if (result.status === 'warning') {
      warning(result.message)
    } else {
      error(result.message)
    }
  } catch (err) {
    error(`测试失败: ${err instanceof Error ? err.message : '未知错误'}`)
  } finally {
    isTestingLlm.value = false
  }
}

const handleExportSummaryImage = async () => {
  const payload = getSummaryImageExportPayload()
  if (!payload) {
    toastError('暂无可导出的 AI 总结')
    return
  }

  const { info } = useToast()
  info('正在生成分享图片...')

  try {
    const result = await exportSummaryAsImage(payload, summaryImageSettings.value)
    if (result.files > 1) {
      success(`导出成功：共 ${result.files} 张，约 ${result.totalSizeKB}KB`)
    } else {
      const first = result.pages[0]
      if (first) {
        success(`导出成功：${first.width}x${first.height} · ${first.sizeKB}KB`)
      } else {
        success('导出成功')
      }
    }
  } catch (_error) {
    toastError('成图失败，请稍后重试')
  }
}

const handleUpdateLlmSettings = async (payload: {
  provider: string
  base_url?: string
  api_key?: string
  model_id?: string
  temperature?: number
  extra_headers?: Record<string, string>
}) => {
  try {
    await updateLlmSettings(payload)
    success('LLM 配置已更新')
  } catch (_e) {
    // 错误信息由 useTaskViewModel + Toast 统一处理
  }
}

const handleUpdateLlmSettingsAndTest = async (payload: {
  provider: string
  base_url?: string
  api_key?: string
  model_id?: string
  temperature?: number
  extra_headers?: Record<string, string>
}) => {
  try {
    // 先保存配置
    await updateLlmSettings(payload)
    success('LLM 配置已更新')

    // 配置保存成功后立即测试
    await handleTestLlm()
  } catch (_e) {
    // 错误信息由 useTaskViewModel + Toast 统一处理
  }
}

const handleUpdateTranscriptionSettings = async (payload: {
  device?: 'cpu' | 'cuda'
  transcriber_type?: 'fast_whisper' | 'vibe_voice_asr'
  model_source?: 'auto_download' | 'manual_path'
  model_size?: 'tiny' | 'base' | 'small' | 'medium' | 'large'
  model_path?: string
  vibevoice_language_model?: string
  vibevoice_max_new_tokens?: number
  vibevoice_dtype?: 'bfloat16' | 'float16'
  vibevoice_inference_mode?: 'local' | 'api'
  vibevoice_api_url?: string
  enable_bilibili_subtitle_fetch?: boolean
  bilibili_sessdata?: string
  clear_bilibili_sessdata?: boolean
}) => {
  try {
    await updateTranscriptionSettings(payload)
    success('转录配置已更新')
  } catch (_e) {
    // 错误信息由 useTaskViewModel + Toast 统一处理
  }
}

const handleReadBilibiliCookieFromBrowser = async () => {
  try {
    const result = await readBilibiliCookieFromBrowser()
    if (result.success) {
      success(`已从 ${result.source_browser} 读取 Cookie`)
    } else {
      toastError(result.error || '读取失败')
    }
  } catch (_e) {
    // 错误信息由 useTaskViewModel + Toast 统一处理
  }
}

const handleValidateModelPath = async (request: {
  path: string
  transcriber_type: 'fast_whisper' | 'vibe_voice_asr'
}) => {
  try {
    await validateModelPath(request)
  } catch (_e) {
    // error state is set in the composable
  }
}

const handleScanVibeVoiceServices = async () => {
  try {
    const results = await scanVibeVoiceServices()
    const available = results.find((item) => item.status === 'available')
    if (available) {
      success(`发现可用服务：${available.url}`)
    } else if (results.length > 0) {
      info('已扫描本地服务，但未发现可用实例')
    } else {
      info('未发现本地 VibeVoice 服务')
    }
    await fetchVibeVoiceServiceStatus()
  } catch (_e) {
    // 错误已在 composable 中处理
  }
}

const handleStartVibeVoiceService = async (payload: {
  model_path: string
  port: number
  dtype: string
}) => {
  try {
    await startVibeVoiceService(payload.model_path, payload.port, payload.dtype)
    await fetchVibeVoiceServiceStatus()
    success('VibeVoice 服务已启动')
  } catch (_e) {
    // 错误信息由调用方或后续状态查询处理
  }
}

const handleStopVibeVoiceService = async () => {
  try {
    await stopVibeVoiceService()
    await fetchVibeVoiceServiceStatus()
    success('VibeVoice 服务已停止')
  } catch (_e) {
    // 错误信息由调用方或后续状态查询处理
  }
}

const handleFetchVibeVoiceServiceStatus = async () => {
  try {
    await fetchVibeVoiceServiceStatus()
  } catch (_e) {
    // 保持静默，避免打开设置时产生多余提示
  }
}

// B站分P处理
const handleSubmit = async () => {
  // localhost 场景优先使用本地路径直读（避免文件上传复制）
  if (isLocalClient && localFilePath.value.trim()) {
    // 先检查路径类型
    const pathCheck = await checkLocalPath(localFilePath.value.trim())

    // 处理检查失败的情况
    if (!pathCheck) {
      toastError('无法检查本地路径，请确认服务器是否正常运行')
      return
    }

    if (pathCheck.type === 'not_found') {
      toastError(`路径不存在: ${pathCheck.path}`)
      return
    }

    if (pathCheck.type === 'folder') {
      // 是文件夹，扫描并弹出选择器
      isScanningLocalFolder.value = true
      try {
        const scanResult = await scanLocalFolder(localFilePath.value.trim())
        if (scanResult && scanResult.files.length > 0) {
          localFolderInfo.value = scanResult
          isLocalFolderSelectorOpen.value = true
        } else if (scanResult && scanResult.files.length === 0) {
          toastError('文件夹中没有找到支持的视频/音频文件')
        } else {
          toastError('扫描文件夹失败，请重试')
        }
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : '扫描文件夹失败'
        toastError(errMsg)
      } finally {
        isScanningLocalFolder.value = false
      }
      return
    }

    // 是文件，直接提交
    await submitTask()
    return
  }

  // 文件上传
  if (selectedFile.value) {
    await submitTask()
    return
  }

  // URL 提交 - 检查是否为B站多P视频
  const url = videoUrl.value.trim()
  if (!url) {
    error.value = '请输入有效视频链接，或粘贴包含链接的文本。'
    return
  }

  if (isBilibiliUrl(url)) {
    // 检查是否为多P视频
    isCheckingBilibiliVideoInfo.value = true
    pendingBilibiliUrl.value = url
    try {
      const info = await checkBilibiliVideoInfo(url)
      if (info && info.is_multi_part) {
        // 是多P视频，显示选择器
        bilibiliVideoInfo.value = info
        isBilibiliPartsSelectorOpen.value = true
        isSubmitting.value = false
      } else {
        // 单P视频，直接提交
        await submitTask()
      }
    } catch (err) {
      // 检查失败，直接提交（后端会处理）
      console.error('Failed to check Bilibili video info:', err)
      await submitTask()
    } finally {
      isCheckingBilibiliVideoInfo.value = false
    }
  } else {
    // 非B站视频，直接提交
    await submitTask()
  }
}

const handleBilibiliPartsConfirm = async (config: BilibiliPartsConfig) => {
  isBilibiliPartsSelectorOpen.value = false
  try {
    await submitTaskWithParts(pendingBilibiliUrl.value, config)
    videoUrl.value = ''
    success('已提交任务')
  } catch (_e) {
    // 错误信息由 useTaskViewModel + Toast 统一处理
  }
}

const handleBilibiliPartsClose = () => {
  isBilibiliPartsSelectorOpen.value = false
  bilibiliVideoInfo.value = null
  pendingBilibiliUrl.value = ''
}

const handleLocalFolderConfirm = async (config: { mode: 'merge' | 'separate'; paths: string[] }) => {
  isLocalFolderSelectorOpen.value = false
  try {
    await submitLocalPathTasks(config.paths, config.mode)
    localFilePath.value = ''
    if (config.paths.length > 1) {
      success(`已提交 ${config.paths.length} 个任务`)
    } else {
      success('已提交任务')
    }
  } catch (_e) {
    // 错误信息由 useTaskViewModel + Toast 统一处理
  }
}

const handleLocalFolderClose = () => {
  isLocalFolderSelectorOpen.value = false
  localFolderInfo.value = null
}

const startEditingTopic = () => {
  editingTopicValue.value = topic.value || selectedTask.value?.title || ''
  isEditingTopic.value = true
}

const saveTopic = async () => {
  const targetTaskId = selectedTask.value?.id
  if (!targetTaskId) return
  try {
    await updateTaskTopic(targetTaskId, editingTopicValue.value)
    // 等待期间用户可能已切换任务并开始新编辑：身份校验通过才清编辑态，
    // 防止任务 A 的保存完成误关任务 B 的编辑态
    if (selectedTask.value?.id !== targetTaskId) return
    isEditingTopic.value = false
    success('主题已更新')
  } catch (e) {
    // Error handled in composable and shown via toast
  }
}

const cancelEditingTopic = () => {
  isEditingTopic.value = false
}

const handleSelectTask = (task: Task) => {
  summaryHighlightRequest.value = null
  markdownHeadings.value = []
  activeHeadingId.value = ''
  headingJumpRequest.value = null
  // 任务切换视图状态全重置：退出主题编辑态并清空编辑文本，
  // 防止任务 A 的编辑文本在保存时 PATCH 到任务 B
  isEditingTopic.value = false
  editingTopicValue.value = ''
  selectTask(task)
}

const handleFocusSearchMatch = (payload: {
  taskId: string
  keyword: string
  source: 'topic' | 'summary'
  requestId: number
}) => {
  activeTab.value = 'summary'
  summaryHighlightRequest.value = payload
}

const handleJumpHeading = (headingId: string) => {
  headingJumpSeq.value += 1
  headingJumpRequest.value = {
    id: headingId,
    requestId: headingJumpSeq.value,
  }
}

const handleUpdateSummarizationSettings = async (payload: {
  chunk_target_duration_sec?: number
  chunk_min_duration_sec?: number
  chunk_max_duration_sec?: number
  boundary_jump_sec?: number
  auto_chunk_min_audio_duration_sec?: number
  auto_chunk_min_transcript_lines?: number
  max_agent_value_chars?: number
  fallback_to_standard_on_agent_error?: boolean
}) => {
  try {
    await updateSummarizationSettings(payload)
    success('总结配置已更新')
  } catch (_e) {
    // 错误信息由 useTaskViewModel + Toast 统一处理
  }
}

const handleMarkdownHeadingsUpdate = (headings: MarkdownHeadingItem[]) => {
  markdownHeadings.value = headings
}

const handleActiveHeadingIdUpdate = (headingId: string) => {
  activeHeadingId.value = headingId
}

// 错误处理
watch(error, (newError) => {
  if (newError) {
    toastError(newError)
  }
})

watch(summaryImageSettings, (nextSettings) => {
  try {
    localStorage.setItem(SUMMARY_IMAGE_SETTINGS_STORAGE_KEY, JSON.stringify(nextSettings))
  } catch {
    // Ignore persistence failure.
  }
  if (isSummaryImageSettingsOpen.value) {
    summaryPreviewDirty.value = true
  }
}, { deep: true })

// 配置 marked 渲染器以支持 mermaid 类名
const renderer = new marked.Renderer()
renderer.code = ({ text, lang }) => {
  if (lang === 'mermaid') {
    return `<pre class="mermaid">${text}</pre>`
  }
  return `<pre><code class="language-${lang}">${text}</code></pre>`
}
marked.setOptions({ renderer })

// Defer large summary compilation so the multipart preview stays interactive.
const compiledMarkdown = ref('')
const showFullMultipartSummary = ref(false)
const multipartPage = ref(0)
const multipartPageSize = 10
let markdownCompileTimer: ReturnType<typeof setTimeout> | null = null
let markdownCompileGeneration = 0

const getMultipartOverview = (summary: string) => {
  const marker = summary.search(/^#\s*分P总结.*$/m)
  if (marker > 0) return summary.slice(0, marker).trim()
  return summary.slice(0, 12000).trim()
}

const getMultipartPages = (summary: string) => {
  const marker = summary.search(/^#\s*分P总结.*$/m)
  if (marker < 0) return [summary]
  const body = summary.slice(marker)
  const matches = Array.from(body.matchAll(/^##\s+P\d+[:：].*$/gm))
  if (!matches.length) return [body.trim()]
  const sections = matches.map((match, index) => {
    const start = match.index ?? 0
    const nextMatch = matches[index + 1]
    const end = nextMatch?.index ?? body.length
    return body.slice(start, end).trim()
  })
  const pages: string[] = []
  for (let index = 0; index < sections.length; index += multipartPageSize) {
    pages.push(`# 分P总结\\n\\n${sections.slice(index, index + multipartPageSize).join('\\n\\n')}`)
  }
  return pages
}

const multipartPageCount = computed(() => {
  const summary = selectedTask.value?.summary
  if (!summary || !selectedTask.value?.has_parts) return 0
  return getMultipartPages(summary).length
})

const scheduleMarkdownCompile = () => {
  markdownCompileGeneration += 1
  const generation = markdownCompileGeneration
  if (markdownCompileTimer) {
    clearTimeout(markdownCompileTimer)
    markdownCompileTimer = null
  }

  compiledMarkdown.value = ''
  const task = selectedTask.value
  if (!task?.summary) return

  markdownCompileTimer = setTimeout(() => {
    markdownCompileTimer = null
    if (generation !== markdownCompileGeneration || selectedTask.value?.id !== task.id) return

    const summary = task.summary
    if (!summary) return
    let previewSummary = summary
    if (task.has_parts) {
      if (!showFullMultipartSummary.value) {
        previewSummary = getMultipartOverview(summary)
      } else {
        const pages = getMultipartPages(summary)
        previewSummary = pages[multipartPage.value] || pages[0] || ''
      }
    }
    const cleanedSummary = stripDoubleBracePlaceholders(previewSummary)
    // XSS 单点净化（管线出口）：marked 不做净化，LLM 内容（含原始 HTML）经
    // marked 编译后立即 DOMPurify 白名单净化；postProcess 只追加可信 DOM
    // （类名/时间芯片），不会重新引入未净化内容。TaskContentArea 的 v-html
    // 只渲染本管线产物，禁止绕过此出口直接给 compiledMarkdown 赋值。
    // 配置：FORBID_ATTR: ['style']（默认配置不过滤 style，LLM 输出可携带
    // 追踪/遮罩 CSS）；USE_PROFILES: { html: true }（剔除 svg/mathML 面，
    // 本管线不需要；Mermaid SVG 走 DOM API 不经此出口）。
    const html = DOMPurify.sanitize(marked.parse(cleanedSummary) as string, {
      FORBID_ATTR: ['style'],
      USE_PROFILES: { html: true },
    })
    compiledMarkdown.value = postProcessCompiledMarkdown(html, {
      videoUrl: task.video_url || '',
    })
  }, 120)
}

watch(
  [() => selectedTask.value?.id, () => selectedTask.value?.summary, showFullMultipartSummary, multipartPage],
  scheduleMarkdownCompile,
  { immediate: true },
)

watch(
  () => selectedTask.value?.id,
  () => {
    showFullMultipartSummary.value = false
    multipartPage.value = 0
  },
)

const expandMultipartSummary = async () => {
  const task = selectedTask.value
  if (!task) return
  multipartPage.value = 0
  try {
    await fetchTaskFullContent(task.id)
  } catch (error) {
    console.error('Failed to load full multipart summary:', error)
    return
  }
  // 等待期间用户可能已切换任务：任务身份重校验（与 copyContent/downloadContent
  // 的 selectedTask.id 守卫模式一致），不得展开新任务的完整分P总结
  if (!selectedTask.value || selectedTask.value.id !== task.id) return
  showFullMultipartSummary.value = true
}

const collapseMultipartSummary = () => {
  showFullMultipartSummary.value = false
  multipartPage.value = 0
}

const changeMultipartPage = (page: number) => {
  multipartPage.value = Math.max(0, Math.min(page, Math.max(0, multipartPageCount.value - 1)))
}

const topic = computed(() => {
  if (selectedTask.value?.topic) return selectedTask.value.topic
  if (!selectedTask.value?.summary) return ''
  const match = selectedTask.value.summary.match(/\{\{(.*?)\}\}/i)
  if (match && match[1]) {
    return match[1].trim()
  }
  return ''
})

watch(
  [
    () => isSummaryImageSettingsOpen.value,
    () => selectedTask.value?.id,
    compiledMarkdown,
    topic,
  ],
  () => {
    if (isSummaryImageSettingsOpen.value) {
      summaryPreviewDirty.value = true
    }
  },
)
</script>

<template>
  <div class="flex h-[100dvh] w-full overflow-hidden bg-bg text-slate-800 font-sans relative">
    <!-- Toast 通知容器（单实例：position 响应式切换，桌面 bottom-right / 移动 bottom-center） -->
    <ToastContainer
      :toasts="toasts"
      :position="toastPosition"
      @close="removeToast"
    />

    <!-- 设置弹窗 -->
    <SettingsModal
      :isOpen="isSettingsModalOpen"
      :llmProviders="llmProviders"
      :llmSettings="llmSettings"
      :isUpdatingLlmSettings="isUpdatingLlmSettings"
      :isTestingLlm="isTestingLlm"
      :transcriptionSettings="transcriptionSettings"
      :isUpdatingTranscriptionSettings="isUpdatingTranscriptionSettings"
      :summarizationSettings="summarizationSettings"
      :isUpdatingSummarizationSettings="isUpdatingSummarizationSettings"
      :isReadingBilibiliCookieFromBrowser="isReadingBilibiliCookieFromBrowser"
      :modelPathValidationResult="modelPathValidationResult"
      :isValidatingModelPath="isValidatingModelPath"
      :vibevoiceServiceStatus="vibevoiceServiceStatus"
      :isScanningVibeVoice="isScanningVibeVoice"
      :isStartingVibeVoice="isStartingVibeVoice"
      :isStoppingVibeVoice="isStoppingVibeVoice"
      :clearModelPathValidation="clearModelPathValidation"
      @close="isSettingsModalOpen = false"
      @updateLlmSettings="handleUpdateLlmSettings"
      @updateLlmSettingsAndTest="handleUpdateLlmSettingsAndTest"
      @testLlm="handleTestLlm"
      @updateTranscriptionSettings="handleUpdateTranscriptionSettings"
      @readBilibiliCookieFromBrowser="handleReadBilibiliCookieFromBrowser"
      @validateModelPath="handleValidateModelPath"
      @scanVibeVoiceServices="handleScanVibeVoiceServices"
      @startVibeVoiceService="handleStartVibeVoiceService"
      @stopVibeVoiceService="handleStopVibeVoiceService"
      @fetchVibeVoiceServiceStatus="handleFetchVibeVoiceServiceStatus"
      @updateSummarizationSettings="handleUpdateSummarizationSettings"
    />

    <!-- 遮罩层 (Mobile Only) -->
    <transition name="fade">
      <div v-if="isSidebarOpen" @click="isSidebarOpen = false" class="fixed inset-0 bg-slate-900/50 z-30 backdrop-blur-sm md:hidden"></div>
    </transition>

    <!-- 左侧边栏 -->
    <Sidebar
      v-model:videoUrl="videoUrl"
      v-model:selectedFile="selectedFile"
      v-model:localFilePath="localFilePath"
      v-model:summaryMode="summaryMode"
      v-model:generateTopic="generateTopic"
      v-model:isSidebarOpen="isSidebarOpen"
      :isLocalClient="isLocalClient"
      :tasks="tasks"
      :queues="queues"
      :selectedTask="selectedTask"
      :isSubmitting="isSubmitting"
      :uploadProgress="uploadProgress"
      :maxUploadBytes="uploadMaxBytes"
      :llmProviders="llmProviders"
      :llmSettings="llmSettings"
      :isUpdatingLlmSettings="isUpdatingLlmSettings"
      :isTestingLlm="isTestingLlm"
      :transcriptionSettings="transcriptionSettings"
      :isUpdatingTranscriptionSettings="isUpdatingTranscriptionSettings"
      :summarizationSettings="summarizationSettings"
      :isUpdatingSummarizationSettings="isUpdatingSummarizationSettings"
      :isReadingBilibiliCookieFromBrowser="isReadingBilibiliCookieFromBrowser"
      :modelPathValidationResult="modelPathValidationResult"
      :isValidatingModelPath="isValidatingModelPath"
      :vibevoiceServiceStatus="vibevoiceServiceStatus"
      :isScanningVibeVoice="isScanningVibeVoice"
      :isStartingVibeVoice="isStartingVibeVoice"
      :isStoppingVibeVoice="isStoppingVibeVoice"
      :clearModelPathValidation="clearModelPathValidation"
      @submit="handleSubmit"
      @cancelSubmit="cancelSubmitting"
      @selectTask="handleSelectTask"
      @deleteTask="handleDeleteTask"
      @retryTask="handleRetryTask"
      @updateLlmSettings="handleUpdateLlmSettings"
      @updateTranscriptionSettings="handleUpdateTranscriptionSettings"
      @updateSummarizationSettings="handleUpdateSummarizationSettings"
      @startTestLlm="handleTestLlm"
      @readBilibiliCookieFromBrowser="handleReadBilibiliCookieFromBrowser"
      @validateModelPath="handleValidateModelPath"
      @scanVibeVoiceServices="handleScanVibeVoiceServices"
      @fetchVibeVoiceServiceStatus="handleFetchVibeVoiceServiceStatus"
      @focusSearchMatch="handleFocusSearchMatch"
      @showInfo="(task) => { handleSelectTask(task); showInfoModal = true; }"
      @openSettings="isSettingsModalOpen = true"
    />

    <!-- 右侧内容区 -->
    <main class="flex-1 flex flex-col h-full bg-gray-50 relative w-full overflow-hidden">
      <template v-if="selectedTask">
        <!-- 悬浮气泡工具栏 -->
        <FloatingToolbar
          v-model:activeTab="activeTab"
          :selectedTask="selectedTask"
          :isSidebarOpen="isSidebarOpen"
          :headings="markdownHeadings"
          :active-heading-id="activeHeadingId"
          @reSummarize="handleReSummarize(selectedTask.id, $event)"
          @reTranscribe="handleReTranscribe(selectedTask.id)"
          @reDownload="handleReDownload(selectedTask.id)"
          @copySummary="handleCopySummary"
          @copyTranscript="handleCopyTranscript"
          @downloadMarkdown="handleDownloadMarkdown"
          @downloadTxt="handleDownloadTxt"
          @exportSummaryImage="handleExportSummaryImage"
          @openSummaryImageSettings="handleOpenSummaryImageSettings"
          @toggleSidebar="isSidebarOpen = true"
          @jumpHeading="handleJumpHeading"
        />

        <!-- 内容滚动区 -->
        <TaskPartsPanel
          :parts="taskParts"
          :part-details="taskPartDetails"
          :loading-part-index="loadingPartIndex"
          :task-id="selectedTask.id"
          :refresh-key="taskPartsRefreshKey"
          @expand="(partIndex) => selectedTask && fetchTaskPart(selectedTask.id, partIndex)"
          @retry="handleRetryFailedParts"
        />
        <TaskContentArea
          :task="selectedTask"
          :active-tab="activeTab"
          :compiled-markdown="compiledMarkdown"
          :show-full-multipart-summary="showFullMultipartSummary"
          :multipart-page="multipartPage"
          :multipart-page-count="multipartPageCount"
          :summary-highlight-request="summaryHighlightRequest"
          :heading-jump-request="headingJumpRequest"
          :topic="topic"
          :is-editing-topic="isEditingTopic"
          :editing-topic-value="editingTopicValue"
          @open-mermaid-viewer="openMermaidViewer"
          @start-edit-topic="startEditingTopic"
          @save-topic="saveTopic"
          @cancel-edit-topic="cancelEditingTopic"
          @update:editing-topic-value="(val) => editingTopicValue = val"
          @update-markdown-headings="handleMarkdownHeadingsUpdate"
          @update-active-heading-id="handleActiveHeadingIdUpdate"
          @expand-multipart-summary="expandMultipartSummary"
          @collapse-multipart-summary="collapseMultipartSummary"
          @change-multipart-page="changeMultipartPage"
        />
      </template>

      <!-- 未选中状态 -->
      <div v-else class="flex-1 flex flex-col h-full">
        <!-- 移动端顶部栏（未选中任务时显示） -->
        <header class="h-14 bg-white border-b border-gray-100 px-4 flex items-center md:hidden shrink-0 sticky top-0 z-20">
          <button @click="isSidebarOpen = true" class="p-1.5 -ml-1.5 text-slate-600 hover:bg-gray-100 rounded-lg active:scale-90 transition-transform">
            <PhList :size="24" />
          </button>
        </header>

        <div class="flex-1 flex flex-col items-center justify-center text-slate-400">
          <PhMonitorPlay :size="64" weight="thin" class="mb-4 opacity-20" />
          <p>请从左侧选择一个任务查看详情</p>
        </div>
      </div>
    </main>

    <!-- 任务信息模态框 -->
    <TaskInfoModal
      v-model:show="showInfoModal"
      :selectedTask="selectedTask"
      :isRedownloading="isRedownloading"
      @reDownload="selectedTask && handleReDownload(selectedTask.id)"
      @reTranscribe="selectedTask && handleReTranscribe(selectedTask.id)"
    />
    
    <!-- Mermaid 查看器模态框 -->
    <MermaidViewerModal
      ref="mermaidViewerModalRef"
      :show="showMermaidViewer"
      :current-zoom="currentZoom"
      :svg-content="currentMermaidSvg"
      @close="handleCloseViewer"
      @fit-view="fitView"
      @zoom-in="zoomIn"
      @zoom-out="zoomOut"
      @reset-view="resetView"
    />

    <SummaryImageWorkbenchModal
      :show="isSummaryImageSettingsOpen"
      v-model:settings="summaryImageSettings"
      v-model:showAllPreviewPages="showAllPreviewPages"
      :layout-options="summaryLayoutOptions"
      :meta-mode-options="summaryMetaModeOptions"
      :format-options="summaryFormatOptions"
      :width-options="summaryWidthOptions"
      :pixel-ratio-options="summaryPixelRatioOptions"
      :is-preview-rendering="isSummaryPreviewRendering"
      :render-progress="summaryRenderProgress"
      :preview-dirty="summaryPreviewDirty"
      :can-refresh-preview="canRefreshSummaryPreview"
      :refresh-button-label="summaryRefreshButtonLabel"
      :preview-pages="summaryPreviewPages"
      :preview-active-index="summaryPreviewActiveIndex"
      :preview-total-size-kb="summaryPreviewTotalSizeKB"
      @close="handleCloseSummaryImageSettings"
      @refresh-preview="handleRefreshSummaryImagePreview"
      @export-image="handleExportSummaryImage"
      @preview-prev="handlePreviewPagePrev"
      @preview-next="handlePreviewPageNext"
      @select-preview-page="handleSelectPreviewPage"
    />

    <!-- B站分P选择器 -->
    <BilibiliPartsSelector
      :isOpen="isBilibiliPartsSelectorOpen"
      :videoInfo="bilibiliVideoInfo"
      :isLoading="isCheckingBilibiliVideoInfo"
      @close="handleBilibiliPartsClose"
      @confirm="handleBilibiliPartsConfirm"
    />

    <!-- 本地文件夹选择器 -->
    <LocalFolderSelector
      :isOpen="isLocalFolderSelectorOpen"
      :folderInfo="localFolderInfo"
      :isLoading="isScanningLocalFolder"
      @close="handleLocalFolderClose"
      @confirm="handleLocalFolderConfirm"
    />
  </div>
</template>

<style>
/* 移动端过渡动画 */
.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* 移动端点击高亮优化 */
html, body { -webkit-tap-highlight-color: transparent; }
</style>
