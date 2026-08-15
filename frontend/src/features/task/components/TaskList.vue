<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  PhClock,
  PhArrowClockwise,
  PhInfo,
  PhTrash,
} from '@phosphor-icons/vue'
import { TaskStatus, type Task, type QueueSnapshot } from '../../../types'
import { getQueueInfo as resolveQueueInfo } from '../../../utils/queueStatus'
import { getStatusLabel, getStatusClass, getStatusIcon } from '../../../shared/utils/taskStatus'
import { resolveTaskTopic, formatTaskDate } from '../taskDisplay'

const props = defineProps<{
  tasks: Task[]
  selectedTask: Task | null
  queues?: QueueSnapshot[]
}>()

const emit = defineEmits<{
  selectTask: [task: Task]
  deleteTask: [taskId: string]
  retryTask: [task: Task]
  showInfo: [task: Task]
}>()

const getTaskStatusLabel = (task: Task) => {
  const base = getStatusLabel(task.status)
  const total = Number(task.part_count || 0)
  const done = Number(task.part_completed || 0)
  const failed = Number(task.part_failed || 0)
  if (total > 0 && (task.status === TaskStatus.PARTIAL || task.status === TaskStatus.COMPLETED || task.status === TaskStatus.DOWNLOADING || task.status === TaskStatus.TRANSCRIBING || task.status === TaskStatus.SUMMARIZING)) {
    return failed > 0 ? base + ' (' + done + '/' + total + '，失败 ' + failed + ')' : base + ' (' + done + '/' + total + ')'
  }
  // ASR 分片计数（仅转录阶段；与总结分块 summary_chunk_* 语义独立）：
  // 长音频 10 个 6min 分片 → "转录中 (3/10)"。done||0 兜底、total>0 才显示。
  if (task.status === TaskStatus.TRANSCRIBING) {
    const asrTotal = Number(task.asr_chunk_total || 0)
    if (asrTotal > 0) {
      const asrDone = Number(task.asr_chunk_done || 0)
      return '转录中 (' + Math.min(asrDone, asrTotal) + '/' + asrTotal + ')'
    }
  }
  if (task.status !== TaskStatus.SUMMARIZING) return base
  const summaryTotal = Number(task.summary_chunk_total || 0)
  const summaryDone = Number(task.summary_chunk_done || 0)
  return summaryTotal > 0 ? '总结中 (' + Math.min(summaryDone, summaryTotal) + '/' + summaryTotal + ')' : base
}

const getTaskProgress = (task: Task) => {
  if (task.status === TaskStatus.SUMMARIZING) {
    const total = Number(task.summary_chunk_total || 0)
    const done = Number(task.summary_chunk_done || 0)
    if (total > 0) {
      return Math.max(0, Math.min(100, (done / total) * 100))
    }
  }
  return Math.max(0, Math.min(100, Number(task.progress || 0)))
}

// 在队列快照中查找任务排队信息（waiting_task_ids 中则排队，active 不算排队）
const getQueueInfo = (task: Task) => resolveQueueInfo(task.id, props.queues ?? [])

const getQueueBadgeText = (task: Task): string | null => {
  const info = getQueueInfo(task)
  if (!info || !info.queued) return null
  return `排队中 (${info.queueName} #${info.position})`
}

const isTaskQueued = (task: Task): boolean => Boolean(getQueueInfo(task)?.queued)

// 进度条动画控制逻辑
const prevProgressMap = ref<Record<string, number>>({})
const shouldAnimateMap = ref<Record<string, boolean>>({})

watch(() => props.tasks, (newTasks) => {
  if (!newTasks) return
  newTasks.forEach(task => {
    const prevProgress = prevProgressMap.value[task.id] ?? 0
    shouldAnimateMap.value[task.id] = task.progress >= prevProgress
    prevProgressMap.value[task.id] = task.progress
  })
}, { deep: true, immediate: true })
</script>

<template>
  <div class="space-y-2">
    <div
      v-for="task in tasks"
      :key="task.id"
      @click="emit('selectTask', task)"
      :class="['p-3 rounded-2xl border cursor-pointer transition-all hover:shadow-sm active:scale-[0.98] group relative',
               selectedTask?.id === task.id ? 'border-blue-200 bg-blue-50/60 ring-1 ring-primary/20 shadow-sm' : 'border-transparent hover:bg-white hover:border-gray-100']"
    >
      <div class="flex justify-between items-start mb-1">
        <div class="flex items-center gap-1.5">
          <span v-if="isTaskQueued(task)" class="text-xs font-medium px-2 py-0.5 rounded-full flex items-center gap-1 text-amber-600 bg-amber-50">
            <PhClock :size="12" />
            {{ getQueueBadgeText(task) }}
          </span>
          <span v-else :class="['text-xs font-medium px-2 py-0.5 rounded-full flex items-center gap-1', getStatusClass(task.status)]">
            <component :is="getStatusIcon(task.status)" :size="12" :class="task.status !== TaskStatus.COMPLETED && task.status !== TaskStatus.FAILED && task.status !== TaskStatus.PENDING ? 'animate-spin' : ''" />
            {{ getTaskStatusLabel(task) }}
          </span>
          <button
            v-if="task.status === TaskStatus.FAILED"
            @click.stop="emit('retryTask', task)"
            class="w-6 h-6 rounded-full bg-red-50 text-red-500 border border-red-100 hover:bg-red-100 hover:text-red-600 transition-colors flex items-center justify-center shrink-0"
            title="快速重跑"
          >
            <PhArrowClockwise :size="12" />
          </button>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-[10px] text-slate-400">{{ formatTaskDate(task.created_at) }}</span>
          <div :class="['flex items-center gap-1', 'md:opacity-0 md:group-hover:opacity-100', 'md:transition-opacity']">
            <button
              @click.stop="emit('showInfo', task)"
              class="text-slate-400 hover:text-blue-500 p-1"
              title="查看信息"
            >
              <PhInfo :size="14" />
            </button>
            <button
              @click.stop="emit('deleteTask', task.id)"
              class="text-slate-400 hover:text-red-500 p-1"
              title="删除任务"
            >
              <PhTrash :size="14" />
            </button>
          </div>
        </div>
      </div>
      <div class="text-sm font-medium text-slate-700 truncate" :title="resolveTaskTopic(task)">
        {{ resolveTaskTopic(task) }}
      </div>
      <div v-if="!isTaskQueued(task) && (task.status === TaskStatus.DOWNLOADING || task.status === TaskStatus.UPLOADING || task.status === TaskStatus.TRANSCRIBING || task.status === TaskStatus.SUMMARIZING)" class="w-full bg-blue-100 h-1 rounded-full mt-2 overflow-hidden">
        <div
          class="bg-blue-500 h-full rounded-full"
          :class="{ 'transition-all duration-500': shouldAnimateMap[task.id] }"
          :style="{ width: getTaskProgress(task) + '%' }"
        ></div>
      </div>
    </div>
    <p v-if="tasks.length === 0" class="text-center text-gray-400 py-8 text-sm">暂无任务记录</p>
  </div>
</template>
