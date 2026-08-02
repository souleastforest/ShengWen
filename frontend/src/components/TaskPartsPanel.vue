<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { TaskPart } from '../types'

const props = defineProps<{
  parts: TaskPart[]
  loading?: boolean
}>()

const emit = defineEmits<{
  retry: []
}>()

const expandedPart = ref<number | null>(null)
const currentPage = ref(1)
const pageSize = 20
const failedParts = computed(() => props.parts.filter((part) => part.status === 'FAILED'))
const totalPages = computed(() => Math.max(1, Math.ceil(props.parts.length / pageSize)))
const paginatedParts = computed(() => {
  const start = (currentPage.value - 1) * pageSize
  return props.parts.slice(start, start + pageSize)
})
const pageNumbers = computed(() =>
  Array.from({ length: totalPages.value }, (_, index) => index + 1)
)

watch(
  () => props.parts.length,
  () => {
    currentPage.value = Math.min(currentPage.value, totalPages.value)
    if (expandedPart.value !== null && !props.parts.some((part) => part.part_index === expandedPart.value)) {
      expandedPart.value = null
    }
  },
)

watch(
  () => props.parts[0]?.task_id,
  () => {
    currentPage.value = 1
    expandedPart.value = null
  },
)

const goToPage = (page: number) => {
  const nextPage = Math.max(1, Math.min(page, totalPages.value))
  if (nextPage === currentPage.value) return
  currentPage.value = nextPage
  expandedPart.value = null
}

const statusLabel = (status: TaskPart['status']) => {
  switch (status) {
    case 'COMPLETED': return '已完成'
    case 'FAILED': return '失败'
    case 'PROCESSING':
    case 'DOWNLOADING':
    case 'TRANSCRIBING':
    case 'SUMMARIZING': return '处理中'
    default: return '等待中'
  }
}

const statusClass = (status: TaskPart['status']) => {
  if (status === 'COMPLETED') return 'text-emerald-600 bg-emerald-50'
  if (status === 'FAILED') return 'text-red-600 bg-red-50'
  if (status === 'PENDING') return 'text-slate-400 bg-slate-50'
  return 'text-blue-600 bg-blue-50'
}

const formatDuration = (seconds?: number) => {
  const value = Math.max(0, Math.round(Number(seconds || 0)))
  const minutes = Math.floor(value / 60)
  const remaining = value % 60
  return minutes ? `${minutes}分${String(remaining).padStart(2, '0')}秒` : `${remaining}秒`
}
</script>

<template>
  <section v-if="parts.length" class="mx-4 mb-4 shrink-0 rounded-xl border border-slate-200 bg-white shadow-sm">
    <header class="flex items-center justify-between border-b border-slate-100 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-slate-800">分P处理进度</h2>
        <p class="mt-0.5 text-xs text-slate-500">
          按顺序串行下载、转录和总结，共 {{ parts.length }} 个分P；当前第 {{ currentPage }}/{{ totalPages }} 页
        </p>
      </div>
      <button
        v-if="failedParts.length"
        type="button"
        class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        :disabled="loading"
        @click="emit('retry')"
      >
        {{ loading ? '提交中...' : `重试失败分P（${failedParts.length}）` }}
      </button>
    </header>

    <div class="max-h-[40vh] overflow-y-auto overscroll-contain divide-y divide-slate-100">
      <article v-for="part in paginatedParts" :key="part.part_index" class="px-4 py-3">
        <button type="button" class="flex w-full items-center gap-3 text-left" @click="expandedPart = expandedPart === part.part_index ? null : part.part_index">
          <span class="w-10 shrink-0 text-xs font-semibold text-slate-500">P{{ part.part_index + 1 }}</span>
          <span class="min-w-0 flex-1 truncate text-sm text-slate-700">{{ part.title || '未命名分P' }}</span>
          <span class="shrink-0 text-xs text-slate-400">{{ formatDuration(part.duration) }}</span>
          <span class="shrink-0 rounded-full px-2 py-0.5 text-xs" :class="statusClass(part.status)">{{ statusLabel(part.status) }}</span>
          <span class="w-10 shrink-0 text-right text-xs text-slate-400">{{ Math.round(part.progress || 0) }}%</span>
        </button>
        <div v-if="part.status === 'FAILED' && part.error_message" class="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
          {{ part.error_message }}
        </div>
        <div v-if="expandedPart === part.part_index" class="mt-3 space-y-3 rounded-lg bg-slate-50 p-3 text-sm">
          <div v-if="part.summary">
            <div class="mb-1 text-xs font-semibold text-slate-500">单P总结</div>
            <div class="whitespace-pre-wrap text-slate-700">{{ part.summary }}</div>
          </div>
          <div v-if="part.transcript">
            <div class="mb-1 text-xs font-semibold text-slate-500">转录文本</div>
            <div class="max-h-48 overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-600">{{ part.transcript }}</div>
          </div>
          <div v-if="!part.summary && !part.transcript" class="text-xs text-slate-400">暂无可展示内容</div>
        </div>
      </article>
    </div>

    <footer
      v-if="totalPages > 1"
      class="flex items-center justify-center gap-1 border-t border-slate-100 px-4 py-3"
      aria-label="分P分页"
    >
      <button
        type="button"
        class="rounded-lg px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
        :disabled="currentPage === 1"
        @click="goToPage(currentPage - 1)"
      >
        上一页
      </button>
      <button
        v-for="page in pageNumbers"
        :key="page"
        type="button"
        class="min-w-8 rounded-lg px-2 py-1.5 text-xs"
        :class="page === currentPage ? 'bg-blue-600 text-white' : 'text-slate-600 hover:bg-slate-100'"
        :aria-current="page === currentPage ? 'page' : undefined"
        @click="goToPage(page)"
      >
        {{ page }}
      </button>
      <button
        type="button"
        class="rounded-lg px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
        :disabled="currentPage === totalPages"
        @click="goToPage(currentPage + 1)"
      >
        下一页
      </button>
    </footer>
  </section>
</template>
