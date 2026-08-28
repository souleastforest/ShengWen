<script setup lang="ts">
import { PhXCircle } from '@phosphor-icons/vue'
import { computed, ref } from 'vue'
import type { Task, MarkdownHeadingItem, TaskPart } from '../types'
import { TaskStatus } from '../types'
import TaskMetaCard from './TaskMetaCard.vue'
import { countWords } from '../utils/formatters'
import MarkdownContent from '../features/transcription/components/MarkdownContent.vue'
import TranscriptViewer from '../features/transcription/components/TranscriptViewer.vue'
import { PART_PROCESSING_STATUSES } from '../features/transcription/useMarkdownCompile'

interface SummaryHighlightRequest {
  taskId: string
  keyword: string
  source: 'topic' | 'summary'
  requestId: number
}

interface Props {
  task: Task
  activeTab: 'summary' | 'transcript'
  /** 渲染用编译产物：总览段 + 当前分P页 summary 拼接（唯一 MarkdownContent 统一渲染） */
  compiledMarkdown: string
  multipartPage: number
  /** 一P一页：多P 任务 = parts 数量 */
  multipartPageCount: number
  /** 当前页解析出的分P（partDetails 优先回退 parts），供占位状态判断 */
  multipartPagePart?: TaskPart | null
  summaryHighlightRequest?: SummaryHighlightRequest | null
  headingJumpRequest?: { id: string; requestId: number } | null
  topic: string
  isEditingTopic: boolean
  editingTopicValue: string
}

const props = defineProps<Props>()

const emit = defineEmits<{
  'open-mermaid-viewer': [target: HTMLElement]
  'start-edit-topic': []
  'save-topic': []
  'cancel-edit-topic': []
  'update:editing-topic-value': [value: string]
  'update-markdown-headings': [headings: MarkdownHeadingItem[]]
  'update-active-heading-id': [headingId: string]
  'change-multipart-page': [page: number]
}>()

const isCompleted = computed(() => props.task.status === TaskStatus.COMPLETED)
const isFailed = computed(() => props.task.status === TaskStatus.FAILED)
const isLoading = computed(() => !isCompleted.value && !isFailed.value)
const contentScrollRef = ref<HTMLElement | null>(null)
// MarkdownContent 滚动容器 getter（惰性取值，避免模板 ref 解包）
const getScrollContainer = () => contentScrollRef.value

const summaryWordCount = computed(() => {
  if (!props.task.summary) return 0
  return countWords(props.task.summary)
})

const showContent = computed(() => {
  if (props.activeTab === 'summary') {
    return !!props.task.summary
  }
  return !!props.task.transcript || (props.task.transcript_segments?.length ?? 0) > 0
})

// 多P 转录 tab：当前分P页是否有可展示的转录内容（partDetails 优先的 multipartPagePart
// 的 transcript/segments；未加载（null/空）时显示"正在加载该分P转录..."占位）
const hasMultipartPartContent = computed(() => {
  const part = props.multipartPagePart
  if (!part) return false
  return (part.transcript != null && part.transcript !== '')
    || (part.transcript_segments != null && part.transcript_segments.length > 0)
})

// 分页器：仅多P 任务且 parts 已加载（multipartPageCount = parts 数）时显示
const showMultipartPager = computed(() => props.task.has_parts && props.multipartPageCount > 0)

// 分页器输入框（1-based 分P号/页码，Enter 跳转；非数字/空忽略；clamp 到 0..N-1）
const jumpInput = ref('')
const handleJumpInput = () => {
  const raw = jumpInput.value.trim()
  jumpInput.value = ''
  if (!raw) return
  const parsed = Number.parseInt(raw, 10)
  if (Number.isNaN(parsed)) return
  const clamped = Math.min(Math.max(parsed, 1), Math.max(1, props.multipartPageCount))
  emit('change-multipart-page', clamped - 1)
}

// 当前分P页是否有内容：summary 来自 partDetails（parts 列表行含 summary 字段但真实
// 契约下被剥离）；无 summary → compiledMarkdown 只有总览段，显示占位
const hasMultipartPageContent = computed(() => !!props.multipartPagePart?.summary)

// 当前分P页占位：分P 处理中 → "正在处理中"；已完成/失败但无内容 → "暂无可展示内容"
const multipartPagePlaceholder = computed(() => {
  const part = props.multipartPagePart
  if (part && PART_PROCESSING_STATUSES.has(part.status)) {
    return '该分P总结正在处理中...'
  }
  return '暂无可展示内容'
})

// 分P列表点击跳转后：滚动内容区进入视野（App handlePartJump 调用）
const scrollContentIntoView = () => {
  contentScrollRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

defineExpose({ scrollContentIntoView })
</script>

<template>
  <div ref="contentScrollRef" class="min-h-0 flex-1 overflow-y-auto overflow-x-auto p-4 md:p-8 pt-16 md:pt-20 custom-scrollbar">
    <div class="max-w-4xl mx-auto">
      <!-- 错误状态 -->
      <div v-if="isFailed" class="bg-red-50 border border-red-100 p-6 rounded-2xl mb-6">
        <div class="flex items-center gap-3 text-red-700 font-bold mb-2">
          <PhXCircle :size="24" />
          处理失败
        </div>
        <p class="text-red-600 text-sm">{{ task.error_message || '未知错误' }}</p>
      </div>

      <!-- 加载状态 -->
      <div v-else-if="isLoading && !showContent" class="flex flex-col items-center justify-center h-full pt-20">
        <div class="w-14 h-14 border-4 border-blue-100 border-t-blue-500 rounded-full animate-spin mb-6"></div>
        <h3 class="text-lg font-medium text-slate-700">正在处理中...</h3>
        <p class="text-slate-400 mt-2">这通常需要几分钟，请稍候</p>
      </div>

      <!-- 成功内容区 -->
      <div v-else class="bg-white rounded-2xl shadow-sm border border-slate-200 min-h-[500px] relative">
        <!-- AI 总结 Tab -->
        <div v-show="activeTab === 'summary'">
          <!-- 顶部元信息卡片 -->
          <div class="px-8 pt-8 pb-6 border-b border-slate-200">
            <TaskMetaCard
              :task="task"
              :topic="topic"
              :summary-word-count="summaryWordCount"
              :is-editing-topic="isEditingTopic"
              :editing-topic-value="editingTopicValue"
              @start-edit-topic="emit('start-edit-topic')"
              @save-topic="emit('save-topic')"
              @cancel-edit-topic="emit('cancel-edit-topic')"
              @update:editing-topic-value="(val) => emit('update:editing-topic-value', val)"
            />
          </div>

          <!-- 内容区：总览段 + 当前分P页 summary 拼接的 compiledMarkdown 由唯一
               MarkdownContent 统一渲染（时间芯片等样式命中 markdown-theme-container
               作用域；章节导航/高亮/mermaid 锚点覆盖总览 + 当前页） -->
          <MarkdownContent
            :task="task"
            :compiled-markdown="compiledMarkdown"
            :active-tab="activeTab"
            :summary-highlight-request="summaryHighlightRequest ?? null"
            :heading-jump-request="headingJumpRequest ?? null"
            :scroll-container="getScrollContainer"
            @open-mermaid-viewer="(target) => emit('open-mermaid-viewer', target)"
            @update-markdown-headings="(headings) => emit('update-markdown-headings', headings)"
            @update-active-heading-id="(headingId) => emit('update-active-heading-id', headingId)"
          />

          <!-- 当前分P页占位：该P无 summary（处理中/无内容）时在分页器上方显示 -->
          <p
            v-if="showMultipartPager && !hasMultipartPageContent"
            data-testid="multipart-page-placeholder"
            class="px-8 pt-4 text-sm italic text-slate-400"
          >
            {{ multipartPagePlaceholder }}
          </p>
        </div>

        <!-- 转录文本 Tab（字幕化渲染：segments 优先，HHMMSS 行回退） -->
        <div v-show="activeTab === 'transcript'" class="px-8 py-8">
          <div class="flex justify-between items-center mb-6">
            <h3 class="text-lg font-bold text-slate-800">全文转录</h3>
          </div>
          <!-- 单P：主行 transcript/segments；多P：当前分P页 multipartPagePart（partDetails
               优先）的 transcript/segments，未加载时占位"正在加载该分P转录..." -->
          <TranscriptViewer
            v-if="!task.has_parts"
            :transcript="task.transcript"
            :segments="task.transcript_segments"
            :video-url="task.video_url"
          />
          <template v-else>
            <TranscriptViewer
              v-if="hasMultipartPartContent"
              :transcript="multipartPagePart?.transcript"
              :segments="multipartPagePart?.transcript_segments"
              :video-url="task.video_url"
              :part-index="multipartPage"
            />
            <p v-else class="text-gray-400 italic">正在加载该分P转录...</p>
          </template>
        </div>

        <!-- 分页器（仅多P 任务一P一页，summary 与 transcript 两 tab 共享：
             ◀ 第 x / N 页 ▶ + 输入框跳转） -->
        <div
          v-if="showMultipartPager"
          data-testid="multipart-pager"
          class="flex flex-wrap items-center gap-3 border-t border-slate-100 px-8 py-3 text-sm"
        >
          <button
            type="button"
            data-testid="multipart-pager-prev"
            class="rounded-lg border border-slate-200 px-3 py-1.5 text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
            :disabled="multipartPage <= 0"
            @click="emit('change-multipart-page', multipartPage - 1)"
          >
            ◀
          </button>
          <span class="text-xs text-slate-500">第 {{ multipartPage + 1 }} / {{ multipartPageCount }} 页</span>
          <button
            type="button"
            data-testid="multipart-pager-next"
            class="rounded-lg border border-slate-200 px-3 py-1.5 text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
            :disabled="multipartPage >= multipartPageCount - 1"
            @click="emit('change-multipart-page', multipartPage + 1)"
          >
            ▶
          </button>
          <input
            v-model="jumpInput"
            data-testid="multipart-pager-input"
            type="text"
            inputmode="numeric"
            placeholder="分P号，回车跳转"
            class="w-32 rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-600"
            @keydown.enter="handleJumpInput"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style>
/* ========== 自定义滚动条样式 ========== */
.custom-scrollbar::-webkit-scrollbar {
  width: 8px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #d1d5db;
  border-radius: 4px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #9ca3af;
}

/* ========== Tailwind Typography (prose) 样式微调 ========== */
/* 注意：大部分样式已移至 styles/markdown-themes/base.css */
/* 这里只保留不在主题系统中的样式 */
</style>
