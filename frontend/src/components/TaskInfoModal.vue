<script setup lang="ts">
import { computed, ref } from 'vue'
import { PhInfo, PhX, PhArrowSquareOut, PhDownloadSimple, PhArrowClockwise, PhCopy, PhCheck } from '@phosphor-icons/vue'
import { TaskStatus, type Task } from '../types'
import { getAudioStatusInfo, canReDownloadAudio, isLocalFileUrl } from '../utils/audioStatus'
import { copyText } from '../utils/clipboard'

const show = defineModel<boolean>('show', { required: true })

const props = defineProps<{
  selectedTask: Task | null
  isRedownloading?: boolean
}>()

const emit = defineEmits<{
  reDownload: []
  reTranscribe: []
}>()

// 复制原网址后的短暂内联反馈（图标/文案 1.5s 后还原）
const copied = ref(false)
let copyTimer: ReturnType<typeof setTimeout> | undefined

const copyVideoUrl = async () => {
  const url = props.selectedTask?.video_url
  if (!url) return
  const ok = await copyText(url)
  if (!ok) return
  copied.value = true
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => {
    copied.value = false
  }, 1500)
}

const audioStatus = computed(() =>
  getAudioStatusInfo(
    props.selectedTask?.audio_downloaded,
    props.selectedTask?.audio_missing_reason,
  ),
)

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
              <button
                v-if="selectedTask.status === TaskStatus.FAILED"
                @click="emit('reTranscribe')"
                class="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-red-200 bg-red-50 text-red-600 hover:bg-red-100 hover:text-red-700 transition-colors text-xs font-medium"
                title="快速重跑"
              >
                <PhArrowClockwise :size="14" />
                快速重跑
              </button>
            </div>

            <div class="text-slate-500">音频状态</div>
            <div class="flex items-center gap-2 flex-wrap">
              <span
                :class="['px-2 py-0.5 rounded-full text-xs font-medium', audioStatus.badgeClass]"
                :title="audioStatus.hint ?? undefined"
              >
                {{ audioStatus.label }}
              </span>
              <button
                v-if="canReDownloadAudio(selectedTask)"
                :disabled="isRedownloading"
                @click="emit('reDownload')"
                class="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-blue-200 bg-blue-50 text-blue-600 hover:bg-blue-100 hover:text-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-xs font-medium"
              >
                <PhDownloadSimple :size="14" />
                {{ isRedownloading ? '重新下载中...' : '重新下载' }}
              </button>
            </div>

            <div class="text-slate-500">视频 URL</div>
            <div class="flex items-center gap-1.5 min-w-0">
              <template v-if="isLocalFileUrl(selectedTask.video_url)">
                <span class="text-slate-800 font-mono text-xs truncate" :title="selectedTask.video_url">{{ selectedTask.video_url }}</span>
              </template>
              <a
                v-else
                :href="selectedTask.video_url"
                target="_blank"
                class="text-primary hover:underline flex items-center gap-1 min-w-0"
                :title="selectedTask.video_url"
              >
                <span class="truncate">{{ selectedTask.video_url }}</span>
                <PhArrowSquareOut :size="12" class="shrink-0" />
              </a>
              <button
                @click="copyVideoUrl"
                class="shrink-0 p-1 rounded-lg text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition-colors"
                :title="copied ? '已复制' : '复制原网址'"
              >
                <PhCheck v-if="copied" :size="14" class="text-emerald-500" />
                <PhCopy v-else :size="14" />
              </button>
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
