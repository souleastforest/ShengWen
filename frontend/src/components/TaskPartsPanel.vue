<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { TaskPart } from '../types'
import { formatDuration } from '../utils/formatters'
import { getPartStatusLabel, getPartStatusClass } from '../shared/utils/taskStatus'
import { compileMarkdownText } from '../features/transcription/useMarkdownCompile'

const props = defineProps<{
  parts: TaskPart[]
  partDetails?: Record<number, TaskPart>
  loading?: boolean
  loadingPartIndex?: number | null
  /** 当前选中任务 id：任务切换时重置展开状态，防止旧任务的展开索引残留到新任务 */
  taskId?: string | null
  /** 分P内容刷新信号（如重试失败分P，后端将清空分P content）：变化时收起展开区 */
  refreshKey?: number
}>()

const emit = defineEmits<{
  retry: []
  expand: [partIndex: number]
}>()

const expandedPart = ref<number | null>(null)
const panelRef = ref<HTMLElement | null>(null)

// 任务切换（taskId 变化）时重置展开状态：parts 直接替换（面板保持挂载）的场景
// 下，旧任务的展开索引不得残留到新任务的分P列表
watch(
  () => props.taskId,
  () => {
    expandedPart.value = null
  },
)

// 内容刷新（重试失败分P等）时收起展开区：后端已置分P content 为 NULL，
// 展开区不得滞留旧值
watch(
  () => props.refreshKey,
  () => {
    expandedPart.value = null
  },
)
const panelHeight = ref<number | null>(null)
const isResizing = ref(false)
const failedParts = computed(() => props.parts.filter((part) => part.status === 'FAILED'))

const getPartDetail = (part: TaskPart) => props.partDetails?.[part.part_index] || part

// 展开区 markdown 编译（P0 修复 B）：summary/transcript 经 compileMarkdownText
// 净化管线编译后 v-html 渲染（XSS 防线：v-html 只允许消费该管线产物，禁止
// 绕过出口直接赋值，契约见 useMarkdownCompile.ts）。只编译当前展开分P的内容，
// 随 parts/partDetails 变化自动重算。
const expandedDetail = computed(() => {
  const part = props.parts.find((p) => p.part_index === expandedPart.value)
  if (!part) return null
  return getPartDetail(part)
})
const expandedSummaryHtml = computed(() => {
  const summary = expandedDetail.value?.summary
  return summary ? compileMarkdownText(summary) : ''
})
const expandedTranscriptHtml = computed(() => {
  const transcript = expandedDetail.value?.transcript
  return transcript ? compileMarkdownText(transcript) : ''
})

const togglePart = (partIndex: number) => {
  const expanding = expandedPart.value !== partIndex
  expandedPart.value = expanding ? partIndex : null
  if (expanding) {
    emit('expand', partIndex)
  }
}

const getMaxPanelHeight = () => Math.max(180, Math.round(window.innerHeight * 0.8))

const clampPanelHeight = (height: number) => {
  const minHeight = 180
  return Math.min(getMaxPanelHeight(), Math.max(minHeight, Math.round(height)))
}

let stopResize: (() => void) | null = null

const stopPanelResize = () => {
  stopResize?.()
  stopResize = null
}

const startPanelResize = (event: PointerEvent) => {
  const panel = panelRef.value
  if (!panel) return

  event.preventDefault()
  stopPanelResize()

  const startY = event.clientY
  const startHeight = panel.getBoundingClientRect().height
  const previousUserSelect = document.body.style.userSelect
  const previousCursor = document.body.style.cursor
  panelHeight.value = clampPanelHeight(startHeight)
  isResizing.value = true
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'ns-resize'

  const onMove = (moveEvent: PointerEvent) => {
    panelHeight.value = clampPanelHeight(startHeight + moveEvent.clientY - startY)
  }

  const onStop = () => {
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onStop)
    window.removeEventListener('pointercancel', onStop)
    document.body.style.userSelect = previousUserSelect
    document.body.style.cursor = previousCursor
    isResizing.value = false
  }

  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onStop)
  window.addEventListener('pointercancel', onStop)
  stopResize = onStop
}

const adjustPanelHeight = (delta: number) => {
  const currentHeight = panelHeight.value || panelRef.value?.getBoundingClientRect().height || 0
  panelHeight.value = clampPanelHeight(currentHeight + delta)
}

const onResizeKeydown = (event: KeyboardEvent) => {
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    adjustPanelHeight(-24)
  } else if (event.key === 'ArrowDown') {
    event.preventDefault()
    adjustPanelHeight(24)
  }
}

onBeforeUnmount(stopPanelResize)
</script>

<template>
  <section
    v-if="parts.length"
    ref="panelRef"
    class="mt-28 mx-4 mb-4 flex shrink-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
    :class="{ 'select-none': isResizing }"
    :style="panelHeight ? { height: panelHeight + 'px' } : undefined"
  >
    <header class="flex shrink-0 items-center justify-between border-b border-slate-100 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-slate-800">分P处理进度</h2>
        <p class="mt-0.5 text-xs text-slate-500">按顺序串行下载、转录和总结，共 {{ parts.length }} 个分P</p>
      </div>
      <button
        v-if="failedParts.length"
        type="button"
        class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        :disabled="loading"
        @click="emit('retry')"
      >
        {{ loading ? '提交中...' : '重试失败分P（' + failedParts.length + '）' }}
      </button>
    </header>

    <div
      class="min-h-0 flex-1 divide-y divide-slate-100 overflow-y-auto overscroll-contain"
      :class="{ 'max-h-[40vh]': !panelHeight }"
    >
      <article v-for="part in parts" :key="part.part_index" class="px-4 py-3">
        <button type="button" class="flex w-full items-center gap-3 text-left" @click="togglePart(part.part_index)">
          <span class="w-10 shrink-0 text-xs font-semibold text-slate-500">P{{ part.part_index + 1 }}</span>
          <span class="min-w-0 flex-1 truncate text-sm text-slate-700">{{ part.title || '未命名分P' }}</span>
          <span class="shrink-0 text-xs text-slate-400">{{ formatDuration(part.duration, { format: 'chinese' }) }}</span>
          <span class="shrink-0 rounded-full px-2 py-0.5 text-xs" :class="getPartStatusClass(part.status)">{{ getPartStatusLabel(part.status) }}</span>
          <span class="w-10 shrink-0 text-right text-xs text-slate-400">{{ Math.round(part.progress || 0) }}%</span>
        </button>
        <div v-if="part.status === 'FAILED' && part.error_message" class="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
          {{ part.error_message }}
        </div>
        <div v-if="expandedPart === part.part_index" class="mt-3 space-y-3 rounded-lg bg-slate-50 p-3 text-sm">
          <div v-if="loadingPartIndex === part.part_index" class="text-xs text-slate-400">正在加载该分P预览...</div>
          <template v-else>
            <div v-if="getPartDetail(part).summary">
              <div class="mb-1 text-xs font-semibold text-slate-500">单P总结</div>
              <div
                class="prose prose-sm prose-slate prose-headings:font-bold prose-a:text-blue-600 hover:prose-a:underline max-w-none text-slate-700 [&_p]:my-0.5 [&_ul]:my-0.5 [&_ol]:my-0.5"
                v-html="expandedSummaryHtml"
              ></div>
            </div>
            <div v-if="getPartDetail(part).transcript">
              <div class="mb-1 text-xs font-semibold text-slate-500">转录文本</div>
              <div
                class="prose prose-sm prose-slate max-w-none max-h-48 overflow-auto text-xs leading-5 text-slate-600 [&_p]:my-0.5 [&_ul]:my-0.5 [&_ol]:my-0.5"
                v-html="expandedTranscriptHtml"
              ></div>
            </div>
            <div v-if="!getPartDetail(part).summary && !getPartDetail(part).transcript" class="text-xs text-slate-400">暂无可展示内容</div>
          </template>
        </div>
      </article>
    </div>

    <div
      data-testid="parts-panel-resizer"
      role="separator"
      aria-label="调整分P面板高度"
      aria-orientation="horizontal"
      :aria-valuenow="panelHeight || undefined"
      aria-valuemin="180"
      :aria-valuemax="getMaxPanelHeight()"
      tabindex="0"
      class="group relative flex h-3 shrink-0 cursor-ns-resize items-center justify-center border-t border-slate-100 bg-white text-slate-400 transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none"
      title="上下拖动调整分P面板高度"
      @pointerdown="startPanelResize"
      @keydown="onResizeKeydown"
    >
      <span class="pointer-events-none absolute inset-x-0 -top-1 flex h-5 items-center justify-center opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
        <span class="flex h-5 w-8 items-center justify-center rounded-full border border-slate-200 bg-white text-xs shadow-sm">↕</span>
      </span>
      <span class="sr-only">上下拖动调整分P面板高度，使用上下方向键也可以调整</span>
    </div>
  </section>
</template>
