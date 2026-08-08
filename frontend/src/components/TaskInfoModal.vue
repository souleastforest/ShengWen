<script setup lang="ts">
import { PhInfo, PhX, PhArrowSquareOut } from '@phosphor-icons/vue'
import { TaskStatus, type Task } from '../types'

const show = defineModel<boolean>('show', { required: true })

defineProps<{
  selectedTask: Task | null
}>()

const getStatusLabel = (status: TaskStatus) => {
  switch (status) {
    case TaskStatus.COMPLETED: return '完成'
    case TaskStatus.FAILED: return '失败'
    case TaskStatus.PARTIAL: return '部分完成'
    case TaskStatus.PENDING: return '等待中'
    case TaskStatus.DOWNLOADING: return '下载中'
    case TaskStatus.UPLOADING: return '上传中'
    case TaskStatus.TRANSCRIBING: return '转录中'
    case TaskStatus.SUMMARIZING: return '总结中'
    default: return status
  }
}

const getStatusClass = (status: TaskStatus) => {
  switch (status) {
    case TaskStatus.COMPLETED: return 'text-emerald-600 bg-emerald-50'
    case TaskStatus.FAILED: return 'text-red-600 bg-red-50'
    case TaskStatus.PARTIAL: return 'text-amber-600 bg-amber-50'
    case TaskStatus.PENDING: return 'text-slate-400 bg-slate-50'
    default: return 'text-blue-600 bg-blue-50'
  }
}
</script>

<template>
  <transition name="fade">
    <div v-if="show && selectedTask" class="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-slate-900/50 backdrop-blur-sm" @click="show = false"></div>
      <div class="bg-white rounded-2xl shadow-xl w-full max-w-md relative z-10 overflow-hidden animate-in fade-in zoom-in duration-200">
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-gray-50">
          <h3 class="font-bold text-slate-800 flex items-center gap-2">
            <PhInfo :size="20" class="text-primary" /> 任务属性
          </h3>
          <button @click="show = false" class="text-slate-400 hover:text-slate-600 transition-colors">
            <PhX :size="20" />
          </button>
        </div>
        <div class="p-6 space-y-4">
          <div class="grid grid-cols-[100px_1fr] gap-2 text-sm">
            <div class="text-slate-500">任务 ID</div>
            <div class="text-slate-800 font-mono text-xs break-all">{{ selectedTask.id }}</div>
            
            <div class="text-slate-500">创建时间</div>
            <div class="text-slate-800">{{ new Date(selectedTask.created_at).toLocaleString() }}</div>
            
            <div class="text-slate-500">当前状态</div>
            <div class="flex items-center gap-2">
              <span :class="['px-2 py-0.5 rounded-full text-xs font-medium', getStatusClass(selectedTask.status)]">
                {{ getStatusLabel(selectedTask.status) }}
              </span>
            </div>

            <div class="text-slate-500">视频 URL</div>
            <div class="text-slate-800 truncate" :title="selectedTask.video_url">
              <a :href="selectedTask.video_url" target="_blank" class="text-primary hover:underline flex items-center gap-1">
                链接 <PhArrowSquareOut :size="12" />
              </a>
            </div>

            <template v-if="selectedTask.audio_duration">
              <div class="text-slate-500">音频时长</div>
              <div class="text-slate-800">{{ Math.round(selectedTask.audio_duration) }} 秒</div>
            </template>

            <template v-if="selectedTask.transcription_time">
              <div class="text-slate-500">转录耗时</div>
              <div class="text-slate-800">{{ selectedTask.transcription_time.toFixed(2) }} 秒</div>
            </template>
            
            <div class="text-slate-500">错误信息</div>
            <div class="text-red-600 break-words">{{ selectedTask.error_message || '无' }}</div>
          </div>
        </div>
        <div class="p-4 bg-gray-50 flex justify-end">
          <button @click="show = false" class="px-4 py-2 bg-white border border-gray-200 text-slate-600 rounded-lg hover:bg-gray-50 font-medium text-sm transition-colors">
            关闭
          </button>
        </div>
      </div>
    </div>
  </transition>
</template>
