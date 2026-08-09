<script setup lang="ts">
import { PhPencilSimple, PhCheck, PhX, PhArrowSquareOut } from '@phosphor-icons/vue'
import type { Task } from '../types'
import { formatDuration, formatTranscriptionDuration, formatConversionRatio, formatDateTime } from '../utils/formatters'
import { ref, nextTick, computed } from 'vue'
import { getAudioStatusInfo } from '../utils/audioStatus'

const props = defineProps<{
  task: Task
  topic: string
  summaryWordCount?: number
  isEditingTopic?: boolean
  editingTopicValue?: string
}>()

const emit = defineEmits<{
  'start-edit-topic': []
  'save-topic': []
  'cancel-edit-topic': []
  'update:editing-topic-value': [value: string]
}>()

const inputRef = ref<HTMLInputElement | null>(null)

const startEditingTopic = () => {
  emit('start-edit-topic')
  nextTick(() => {
    inputRef.value?.focus()
  })
}

// 仅展示：与 TaskInfoModal 共用同一文案映射，避免两处漂移
const audioStatus = computed(() =>
  getAudioStatusInfo(props.task.audio_downloaded, props.task.audio_missing_reason),
)
</script>

<template>
  <div class="flex flex-col gap-4">
    <!-- 主题标题 -->
    <div v-if="!isEditingTopic" class="flex items-center gap-2">
      <h1 class="text-2xl md:text-3xl font-bold text-slate-800">
        {{ topic || task.title || 'AI 总结' }}
      </h1>
      <button
        @click="startEditingTopic"
        class="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-gray-200 bg-white text-slate-400 hover:text-primary hover:border-blue-200 hover:bg-blue-50 transition-colors"
        title="编辑主题"
      >
        <PhPencilSimple :size="18" />
        <span class="text-xs font-medium">编辑</span>
      </button>
    </div>
    <div v-else class="flex items-center gap-2">
      <input
        ref="inputRef"
        :value="editingTopicValue"
        type="text"
        class="flex-1 px-3 py-2 text-xl font-bold border border-blue-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-200 bg-white"
        @input="$emit('update:editing-topic-value', ($event.target as HTMLInputElement).value)"
        @keyup.enter="$emit('save-topic')"
        @keyup.esc="$emit('cancel-edit-topic')"
        placeholder="输入主题..."
      />
      <button @click="$emit('save-topic')" class="text-emerald-600 hover:bg-emerald-50 p-2 rounded-lg" title="保存">
        <PhCheck :size="20" />
      </button>
      <button @click="$emit('cancel-edit-topic')" class="text-slate-400 hover:bg-slate-100 p-2 rounded-lg" title="取消">
        <PhX :size="20" />
      </button>
    </div>

    <!-- 视频链接 -->
    <div class="flex items-center gap-2 text-xs text-slate-500">
      <span class="text-slate-400">视频链接:</span>
      <a :href="task.video_url" target="_blank" class="text-slate-600 hover:text-primary hover:underline truncate flex items-center gap-1 max-w-md">
        {{ task.video_url }}
        <PhArrowSquareOut :size="12" />
      </a>
      <template v-if="task.author_name">
        <span class="text-slate-400">By</span>
        <a
          v-if="task.author_url"
          :href="task.author_url"
          target="_blank"
          class="text-slate-600 hover:text-primary hover:underline truncate max-w-[220px]"
        >
          {{ task.author_name }}
        </a>
        <span v-else class="text-slate-600 truncate max-w-[220px]">
          {{ task.author_name }}
        </span>
      </template>
    </div>

    <!-- 元信息卡片 -->
    <div class="flex flex-col gap-3 text-sm text-slate-600">
      <!-- 第一行：视频时长、转录耗时、转换比 -->
      <div class="flex items-center gap-6 flex-wrap">
        <span class="flex items-center">
          视频时长 <strong class="ml-1 text-slate-800 font-semibold">{{ formatDuration(task.audio_duration) }}</strong>
        </span>
        <span class="flex items-center">
          转录耗时 <strong class="ml-1 text-slate-800 font-semibold">{{ formatTranscriptionDuration(task.transcription_time) }}</strong>
        </span>
        <span class="flex items-center">
          转换比 <strong class="ml-1 text-slate-800 font-semibold">{{ formatConversionRatio(task.audio_duration, task.transcription_time) }}x</strong>
        </span>
      </div>

      <!-- 第二行：总字数、生成时间 -->
      <div class="flex items-center gap-6 flex-wrap">
        <span v-if="summaryWordCount !== undefined" class="flex items-center">
          总字数 <strong class="ml-1 text-slate-800 font-semibold">{{ summaryWordCount }}</strong>
        </span>
        <span v-if="task.summary_mode" class="flex items-center">
          总结模式
          <strong class="ml-1 text-slate-800 font-semibold">
            {{
              task.summary_mode === 'agent'
                ? 'Agent 增强模式'
                : task.summary_mode === 'standard'
                  ? '标准模式'
                  : task.summary_mode === 'none'
                    ? '仅转录'
                    : '自动模式'
            }}
          </strong>
        </span>
        <span v-if="task.summary_chunk_total && task.summary_chunk_total > 0" class="flex items-center">
          分块进度
          <strong class="ml-1 text-slate-800 font-semibold">
            {{ task.summary_chunk_done || 0 }}/{{ task.summary_chunk_total }}
          </strong>
        </span>
        <span class="flex items-center">
          生成时间 <strong class="ml-1 text-slate-800 font-semibold">{{ formatDateTime(task.created_at) }}</strong>
        </span>
        <span class="flex items-center">
          音频状态
          <span
            :class="['ml-1 px-2 py-0.5 rounded-full text-xs font-medium', audioStatus.badgeClass]"
            :title="audioStatus.hint ?? undefined"
          >
            {{ audioStatus.label }}
          </span>
        </span>
      </div>
    </div>
  </div>
</template>
