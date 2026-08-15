/**
 * 总结一键成图工作台编排（从 App.vue 下沉，行为逐行等价）
 *
 * 职责：SummaryImageWorkbenchModal 的状态与编排——设置持久化、预览生成
 * （渲染序号防竞态）、脏标记、分页浏览。注入 selectedTask/topic/compiledMarkdown
 * 三个响应式源；toast 使用全局单例 useToast。
 */
import { computed, ref, watch, type Ref } from 'vue'
import {
  useSummaryImageExporter,
  createDefaultSummaryImageExportSettings,
  type SummaryImagePreviewPage,
  type SummaryImageExportSettings,
  type SummaryImageLayoutPreset,
  type SummaryImageMetaMode,
  type SummaryImageFormat,
  type SummaryImageRenderProgress,
} from '../../../composables/useSummaryImageExporter'
import { useToast } from '../../../composables/useToast'
import type { Task } from '../../../types'

const SUMMARY_IMAGE_SETTINGS_STORAGE_KEY = 'ShengWen:summary-image-export-settings'

export const summaryLayoutOptions: Array<{ label: string; value: SummaryImageLayoutPreset }> = [
  { label: '9:16 手机竖版', value: 'mobile-9-16' },
  { label: '9:32 长屏', value: 'mobile-9-32' },
  { label: '9:64 超长图', value: 'mobile-9-64' },
  { label: '长图原始比例', value: 'long' },
]

export const summaryMetaModeOptions: Array<{ label: string; value: SummaryImageMetaMode }> = [
  { label: '每个图片顶端都显示元信息', value: 'all-pages' },
  { label: '仅第一张图顶部显示元信息', value: 'first-page-only' },
]

export const summaryFormatOptions: Array<{ label: string; value: SummaryImageFormat }> = [
  { label: 'JPEG（推荐）', value: 'jpeg' },
  { label: 'WebP（更小）', value: 'webp' },
  { label: 'PNG（无损）', value: 'png' },
]

export const summaryWidthOptions = [960, 1080, 1242, 1440]
export const summaryPixelRatioOptions = [1, 1.25, 1.5, 1.8, 2]

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

export function useSummaryImageWorkbench(options: {
  selectedTask: Readonly<Ref<Task | null>>
  topic: Readonly<Ref<string>>
  compiledMarkdown: Readonly<Ref<string>>
}) {
  const { selectedTask, topic, compiledMarkdown } = options
  const { info, success, error: toastError } = useToast()
  const { generateSummaryImagePreview } = useSummaryImageExporter()

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

  return {
    summaryLayoutOptions,
    summaryMetaModeOptions,
    summaryFormatOptions,
    summaryWidthOptions,
    summaryPixelRatioOptions,
    summaryImageSettings,
    isSummaryImageSettingsOpen,
    isSummaryPreviewRendering,
    summaryRenderProgress,
    summaryPreviewDirty,
    summaryPreviewPages,
    summaryPreviewActiveIndex,
    summaryPreviewTotalSizeKB,
    showAllPreviewPages,
    canRefreshSummaryPreview,
    summaryRefreshButtonLabel,
    getSummaryImageExportPayload,
    handleOpenSummaryImageSettings,
    handleRefreshSummaryImagePreview,
    handleSelectPreviewPage,
    handlePreviewPagePrev,
    handlePreviewPageNext,
    handleCloseSummaryImageSettings,
  }
}
