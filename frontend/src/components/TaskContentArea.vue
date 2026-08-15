<script setup lang="ts">
import { PhXCircle } from '@phosphor-icons/vue'
import { computed, ref } from 'vue'
import type { Task, MarkdownHeadingItem } from '../types'
import { TaskStatus } from '../types'
import TaskMetaCard from './TaskMetaCard.vue'
import { countWords } from '../utils/formatters'
import MarkdownContent from '../features/transcription/components/MarkdownContent.vue'

interface SummaryHighlightRequest {
  taskId: string
  keyword: string
  source: 'topic' | 'summary'
  requestId: number
}

interface Props {
  task: Task
  activeTab: 'summary' | 'transcript'
  compiledMarkdown: string
  showFullMultipartSummary: boolean
  multipartPage: number
  multipartPageCount: number
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
  'expand-multipart-summary': []
  'collapse-multipart-summary': []
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
  return !!props.task.transcript
})
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

          <!-- 总结内容（markdown 渲染容器：标题收集/高亮/mermaid） -->
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
          <div v-if="task.has_parts && task.summary && !showFullMultipartSummary" class="border-t border-slate-100 px-8 py-4">
            <button
              type="button"
              class="text-sm font-medium text-blue-600 hover:text-blue-700"
              @click="emit('expand-multipart-summary')"
            >
              展开完整分P总结
            </button>
            <span class="ml-2 text-xs text-slate-400">按页加载分P总结，每页 10 个 P</span>
          </div>
          <div v-else-if="task.has_parts && task.summary && showFullMultipartSummary" class="flex items-center justify-between gap-3 border-t border-slate-100 px-8 py-3 text-sm">
            <button
              type="button"
              class="rounded-lg border border-slate-200 px-3 py-1.5 text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
              :disabled="multipartPage <= 0"
              @click="emit('change-multipart-page', multipartPage - 1)"
            >
              上一页
            </button>
            <span class="text-xs text-slate-500">第 {{ multipartPage + 1 }} / {{ multipartPageCount }} 页</span>
            <button
              type="button"
              class="rounded-lg border border-slate-200 px-3 py-1.5 text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
              :disabled="multipartPage >= multipartPageCount - 1"
              @click="emit('change-multipart-page', multipartPage + 1)"
            >
              下一页
            </button>
            <button
              type="button"
              class="text-xs text-slate-500 hover:text-slate-700"
              @click="emit('collapse-multipart-summary')"
            >
              收起
            </button>
          </div>
        </div>

        <!-- 转录文本 Tab -->
        <div v-show="activeTab === 'transcript'" class="px-8 py-8">
          <div class="flex justify-between items-center mb-6">
            <h3 class="text-lg font-bold text-slate-800">全文转录</h3>
          </div>
          <div class="space-y-4 text-slate-600 leading-relaxed font-normal">
            <p v-if="task.transcript" class="whitespace-pre-wrap text-sm leading-relaxed">{{ task.transcript }}</p>
            <p v-else class="text-gray-400 italic">暂无转录内容</p>
          </div>
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
