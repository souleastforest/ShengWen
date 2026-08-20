<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import type { TaskPart } from '../types'
import { formatDuration } from '../utils/formatters'
import { getPartStatusLabel, getPartStatusClass } from '../shared/utils/taskStatus'

const props = defineProps<{
  parts: TaskPart[]
  loading?: boolean
}>()

const emit = defineEmits<{
  retry: []
  /** 点击分P行：跳转到下方 markdown 渲染器对应页（一P一页） */
  jump: [partIndex: number]
}>()

const panelRef = ref<HTMLElement | null>(null)
const panelHeight = ref<number | null>(null)
const isResizing = ref(false)
const failedParts = computed(() => props.parts.filter((part) => part.status === 'FAILED'))

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
        <button
          type="button"
          class="flex w-full items-center gap-3 text-left"
          title="点击跳转到该分P总结"
          @click="emit('jump', part.part_index)"
        >
          <span class="w-10 shrink-0 text-xs font-semibold text-slate-500">P{{ part.part_index + 1 }}</span>
          <span class="min-w-0 flex-1 truncate text-sm text-slate-700">{{ part.title || '未命名分P' }}</span>
          <span class="shrink-0 text-xs text-slate-400">{{ formatDuration(part.duration, { format: 'chinese' }) }}</span>
          <span class="shrink-0 rounded-full px-2 py-0.5 text-xs" :class="getPartStatusClass(part.status)">{{ getPartStatusLabel(part.status) }}</span>
          <span class="w-10 shrink-0 text-right text-xs text-slate-400">{{ Math.round(part.progress || 0) }}%</span>
        </button>
        <div v-if="part.status === 'FAILED' && part.error_message" class="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
          {{ part.error_message }}
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
