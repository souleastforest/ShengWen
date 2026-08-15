<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  PhMagnifyingGlass,
  PhArrowClockwise,
  PhInfo,
  PhTrash,
} from '@phosphor-icons/vue'
import { TaskStatus, type Task } from '../../../types'
import { getStatusLabel, getStatusClass, getStatusIcon } from '../../../shared/utils/taskStatus'
import {
  resolveTaskTopic,
  buildMatchPreview,
  buildModifiedInfo,
  formatTaskDate,
  getTaskStatusLabel,
  type MatchPreview,
  type SearchMatchSource,
} from '../taskDisplay'

type ManagedTaskResult = {
  task: Task
  topicPreview: MatchPreview
  summaryPreview: MatchPreview
  modifiedLabel: string
  modifiedTitle: string
}

const props = defineProps<{
  tasks: Task[]
  selectedTask: Task | null
}>()

const emit = defineEmits<{
  selectTask: [task: Task]
  deleteTask: [taskId: string]
  retryTask: [task: Task]
  showInfo: [task: Task]
  focusSearchMatch: [payload: {
    taskId: string
    keyword: string
    source: SearchMatchSource
    requestId: number
  }]
}>()

const manageKeyword = ref('')
const manageStatus = ref<'all' | TaskStatus>('all')
const manageSort = ref<'newest' | 'oldest' | 'latest_modified'>('newest')
const searchRequestId = ref(0)

const statusOptions: Array<{ value: 'all' | TaskStatus, label: string }> = [
  { value: 'all', label: '全部状态' },
  ...Object.values(TaskStatus).map((value) => ({ value, label: getStatusLabel(value) })),
]

const managedResults = computed<ManagedTaskResult[]>(() => {
  const keyword = manageKeyword.value.trim()
  const status = manageStatus.value
  let list = props.tasks.map((task) => {
    const topicPreview = buildMatchPreview(resolveTaskTopic(task), keyword, 14)
    const summaryPreview = buildMatchPreview(task.summary || '', keyword, 22)
    const modifiedInfo = buildModifiedInfo(task)
    return {
      task,
      topicPreview,
      summaryPreview,
      modifiedLabel: modifiedInfo.label,
      modifiedTitle: modifiedInfo.title,
    }
  })

  if (keyword) {
    list = list.filter((item) => item.topicPreview.hasHit || item.summaryPreview.hasHit)
  }

  if (status !== 'all') {
    list = list.filter((item) => item.task.status === status)
  }

  list.sort((a, b) => {
    const modifiedA = new Date(a.task.latest_modified_at || a.task.created_at).getTime()
    const modifiedB = new Date(b.task.latest_modified_at || b.task.created_at).getTime()
    const ta = new Date(a.task.created_at).getTime()
    const tb = new Date(b.task.created_at).getTime()
    if (manageSort.value === 'latest_modified') return modifiedB - modifiedA
    if (manageSort.value === 'oldest') return ta - tb
    return tb - ta
  })

  return list
})

const handleManagedResultClick = (result: ManagedTaskResult) => {
  emit('selectTask', result.task)

  const keyword = manageKeyword.value.trim()
  if (!keyword) return

  let source: SearchMatchSource | null = null
  if (result.summaryPreview.hasHit) {
    source = 'summary'
  } else if (result.topicPreview.hasHit) {
    source = 'topic'
  }

  if (!source) return
  searchRequestId.value += 1
  emit('focusSearchMatch', {
    taskId: result.task.id,
    keyword,
    source,
    requestId: searchRequestId.value,
  })
}
</script>

<template>
  <div class="flex-1 min-h-0 flex flex-col">
    <!-- 搜索条件 -->
    <div class="p-4 pb-3 border-b border-gray-100 space-y-2.5">
      <h2 class="text-xs font-semibold text-slate-400 uppercase tracking-wider">任务搜索</h2>
      <div class="relative">
        <PhMagnifyingGlass :size="16" class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          v-model="manageKeyword"
          type="text"
          placeholder="搜索 topic / AI 总结正文"
          class="w-full pl-9 pr-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
        >
      </div>
      <div class="grid grid-cols-2 gap-2">
        <select
          v-model="manageStatus"
          class="w-full px-2.5 py-2 bg-gray-50 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
        >
          <option v-for="opt in statusOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
        <select
          v-model="manageSort"
          class="w-full px-2.5 py-2 bg-gray-50 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
        >
          <option value="newest">最新优先</option>
          <option value="latest_modified">最新修改优先</option>
          <option value="oldest">最早优先</option>
        </select>
      </div>
    </div>

    <!-- 搜索结果 -->
    <div class="flex-1 overflow-y-auto p-4 custom-scrollbar">
    <div class="text-[11px] text-slate-400 mb-2 px-1">共 {{ managedResults.length }} 条</div>
    <div class="space-y-2">
      <div
        v-for="result in managedResults"
        :key="result.task.id"
        @click="handleManagedResultClick(result)"
        :class="['p-2.5 rounded-xl border cursor-pointer transition-all hover:shadow-sm active:scale-[0.985] relative',
                 selectedTask?.id === result.task.id ? 'border-blue-200 bg-blue-50/60 ring-1 ring-primary/20 shadow-sm' : 'border-gray-100 hover:bg-white hover:border-gray-200']"
      >
        <div class="flex justify-between items-start gap-2 mb-1">
          <span :class="['text-[11px] font-medium px-1.5 py-0.5 rounded-full flex items-center gap-1 shrink-0', getStatusClass(result.task.status)]">
            <component :is="getStatusIcon(result.task.status)" :size="12" :class="result.task.status !== TaskStatus.COMPLETED && result.task.status !== TaskStatus.FAILED && result.task.status !== TaskStatus.PENDING ? 'animate-spin' : ''" />
            {{ getTaskStatusLabel(result.task) }}
          </span>
          <div class="flex items-center gap-1.5 shrink-0">
            <span class="text-[10px] text-slate-400">{{ formatTaskDate(result.task.created_at) }}</span>
            <span
              v-if="result.modifiedLabel"
              :title="result.modifiedTitle"
              class="text-[10px] text-slate-500 bg-slate-100 px-1.5 py-[1px] rounded-md border border-slate-200/80"
            >
              {{ result.modifiedLabel }}
            </span>
          </div>
        </div>
        <div class="text-[13px] leading-5 font-medium text-slate-700 line-clamp-2" :title="resolveTaskTopic(result.task)">
          {{ resolveTaskTopic(result.task) }}
        </div>

        <div v-if="manageKeyword.trim()" class="mt-1.5 space-y-0.5">
          <div v-if="result.topicPreview.hasHit" class="flex items-start gap-1 text-[10px] text-slate-500">
            <span class="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 shrink-0">主题</span>
            <p class="leading-[1.15rem] break-all">
              <span v-if="result.topicPreview.leftEllipsis">...</span>{{ result.topicPreview.before }}<mark class="bg-amber-200/80 px-0.5 rounded">{{ result.topicPreview.hit }}</mark>{{ result.topicPreview.after }}<span v-if="result.topicPreview.rightEllipsis">...</span>
            </p>
          </div>
          <div v-if="result.summaryPreview.hasHit" class="flex items-start gap-1 text-[10px] text-slate-500">
            <span class="px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 shrink-0">正文</span>
            <p class="leading-[1.15rem] break-all">
              <span v-if="result.summaryPreview.leftEllipsis">...</span>{{ result.summaryPreview.before }}<mark class="bg-amber-200/80 px-0.5 rounded">{{ result.summaryPreview.hit }}</mark>{{ result.summaryPreview.after }}<span v-if="result.summaryPreview.rightEllipsis">...</span>
            </p>
          </div>
        </div>

        <div class="mt-1.5 flex items-center justify-end gap-1">
          <button
            v-if="result.task.status === TaskStatus.FAILED"
            @click.stop="emit('retryTask', result.task)"
            class="w-5 h-5 rounded-full bg-red-50 text-red-500 border border-red-100 hover:bg-red-100 hover:text-red-600 transition-colors flex items-center justify-center"
            title="快速重跑"
          >
            <PhArrowClockwise :size="11" />
          </button>
          <button
            @click.stop="emit('showInfo', result.task)"
            class="text-slate-400 hover:text-blue-500 p-0.5"
            title="查看信息"
          >
            <PhInfo :size="13" />
          </button>
          <button
            @click.stop="emit('deleteTask', result.task.id)"
            class="text-slate-400 hover:text-red-500 p-0.5"
            title="删除任务"
          >
            <PhTrash :size="13" />
          </button>
        </div>
      </div>
      <p v-if="managedResults.length === 0" class="text-center text-gray-400 py-8 text-sm">没有匹配的任务</p>
    </div>
    </div>
  </div>
</template>
