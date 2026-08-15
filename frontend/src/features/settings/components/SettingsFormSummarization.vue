<script setup lang="ts">
import { ref, watch } from 'vue'
import { PhKey, PhFlask, PhSpinner, PhGitBranch, PhCpu } from '@phosphor-icons/vue'
import type { SummarizationSettings } from '../../../types'

const props = defineProps<{
  summarizationSettings: SummarizationSettings | null
  isUpdatingSummarizationSettings: boolean
}>()

const emit = defineEmits<{
  updateSummarizationSettings: [payload: {
    chunk_target_duration_sec?: number
    chunk_min_duration_sec?: number
    chunk_max_duration_sec?: number
    boundary_jump_sec?: number
    auto_chunk_min_audio_duration_sec?: number
    auto_chunk_min_transcript_lines?: number
    max_agent_value_chars?: number
    fallback_to_standard_on_agent_error?: boolean
  }]
}>()

// Agent 设置
const chunkTargetDurationSec = ref(20)
const chunkMinDurationSec = ref(10)
const chunkMaxDurationSec = ref(30)
const boundaryJumpSec = ref(10)
const autoChunkMinAudioDurationSec = ref(40)
const autoChunkMinTranscriptLines = ref(1800)
const maxAgentValueChars = ref(500)
const fallbackToStandardOnAgentError = ref(true)

const secondsToMinutes = (seconds: number) => {
  return Math.round((Number(seconds || 0) / 60) * 10) / 10
}

const minutesToSeconds = (minutes: number) => {
  return Math.round(Number(minutes || 0) * 60)
}

watch(() => props.summarizationSettings, (settings) => {
  if (settings) {
    chunkTargetDurationSec.value = secondsToMinutes(settings.chunk_target_duration_sec)
    chunkMinDurationSec.value = secondsToMinutes(settings.chunk_min_duration_sec)
    chunkMaxDurationSec.value = secondsToMinutes(settings.chunk_max_duration_sec)
    boundaryJumpSec.value = settings.boundary_jump_sec
    autoChunkMinAudioDurationSec.value = secondsToMinutes(settings.auto_chunk_min_audio_duration_sec)
    autoChunkMinTranscriptLines.value = settings.auto_chunk_min_transcript_lines
    maxAgentValueChars.value = settings.max_agent_value_chars
    fallbackToStandardOnAgentError.value = settings.fallback_to_standard_on_agent_error
  }
}, { immediate: true })

const handleSaveSummarizationSettings = () => {
  emit('updateSummarizationSettings', {
    chunk_target_duration_sec: minutesToSeconds(chunkTargetDurationSec.value),
    chunk_min_duration_sec: minutesToSeconds(chunkMinDurationSec.value),
    chunk_max_duration_sec: minutesToSeconds(chunkMaxDurationSec.value),
    boundary_jump_sec: boundaryJumpSec.value,
    auto_chunk_min_audio_duration_sec: minutesToSeconds(autoChunkMinAudioDurationSec.value),
    auto_chunk_min_transcript_lines: autoChunkMinTranscriptLines.value,
    max_agent_value_chars: Math.max(100, Number(maxAgentValueChars.value || 0)),
    fallback_to_standard_on_agent_error: fallbackToStandardOnAgentError.value,
  })
}
</script>

<template>
  <div class="space-y-4">
    <!-- 使用说明 -->
    <div class="rounded-xl border border-slate-200 bg-white p-4 space-y-2">
      <div class="flex items-center gap-2 pb-2 border-b border-slate-100">
        <PhFlask :size="18" class="text-blue-500" />
        <h3 class="text-sm font-semibold text-slate-800">使用说明</h3>
      </div>
      <p class="text-xs text-slate-600 leading-relaxed">
        长视频会分块总结后拼接。三个参数控制分块大小：<br>
        • 目标时长：期望每块的长度（如30分钟）<br>
        • 最短时长：最后一块的最小长度，太短会合并到前一块（建议为目标的一半）<br>
        • 最长时长：单块的硬性上限（建议为目标的1.3-1.5倍）
      </p>
    </div>

    <!-- 核心参数 -->
    <div class="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div class="flex items-center gap-2 pb-2 border-b border-slate-100">
        <PhGitBranch :size="18" class="text-blue-500" />
        <h3 class="text-sm font-semibold text-slate-800">核心参数</h3>
      </div>
      <div class="grid grid-cols-3 gap-3">
        <div>
          <label class="block text-xs text-slate-600 mb-1.5">每块目标时长(分钟)</label>
          <input
            v-model.number="chunkTargetDurationSec"
            type="number"
            min="0.5"
            step="0.5"
            class="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
        </div>
        <div>
          <label class="block text-xs text-slate-600 mb-1.5">每块最短(分钟)</label>
          <input
            v-model.number="chunkMinDurationSec"
            type="number"
            min="0.5"
            step="0.5"
            class="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
        </div>
        <div>
          <label class="block text-xs text-slate-600 mb-1.5">每块最长(分钟)</label>
          <input
            v-model.number="chunkMaxDurationSec"
            type="number"
            min="0.5"
            step="0.5"
            class="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
        </div>
      </div>
    </div>

    <!-- 自动触发 -->
    <div class="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div class="flex items-center gap-2 pb-2 border-b border-slate-100">
        <PhCpu :size="18" class="text-blue-500" />
        <h3 class="text-sm font-semibold text-slate-800">自动触发</h3>
      </div>
      <p class="text-xs text-slate-500">满足任一条件，就会自动启用 Agent 分块。</p>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-xs text-slate-600 mb-1.5">音频时长达到(分钟)</label>
          <input
            v-model.number="autoChunkMinAudioDurationSec"
            type="number"
            min="5"
            step="0.5"
            class="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
        </div>
        <div>
          <label class="block text-xs text-slate-600 mb-1.5">转录行数达到(行)</label>
          <input
            v-model.number="autoChunkMinTranscriptLines"
            type="number"
            min="100"
            step="50"
            class="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
        </div>
      </div>
    </div>

    <!-- 高级参数 -->
    <div class="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div class="flex items-center gap-2 pb-2 border-b border-slate-100">
        <PhKey :size="18" class="text-blue-500" />
        <h3 class="text-sm font-semibold text-slate-800">高级参数</h3>
      </div>
      <p class="text-xs text-slate-500">一般不用改</p>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-xs text-slate-600 mb-1.5">分块边界容错(秒)</label>
          <input
            v-model.number="boundaryJumpSec"
            type="number"
            min="1"
            step="1"
            class="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
        </div>
        <div>
          <label class="block text-xs text-slate-600 mb-1.5">前文摘要引用上限(字)</label>
          <input
            v-model.number="maxAgentValueChars"
            type="number"
            min="100"
            step="50"
            class="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
        </div>
      </div>

      <!-- 错误回退开关 -->
      <div class="flex items-center justify-between gap-3 pt-2">
        <div class="flex-1 min-w-0">
          <p class="text-xs font-medium text-slate-700">Agent 失败自动回退标准模式</p>
          <p class="text-xs text-slate-500 mt-0.5">
            开启后遇到分块异常会自动降级，保证任务尽量产出结果。
          </p>
        </div>
        <button
          @click="fallbackToStandardOnAgentError = !fallbackToStandardOnAgentError"
          :class="[
            'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2',
            fallbackToStandardOnAgentError ? 'bg-blue-500' : 'bg-slate-300'
          ]"
          role="switch"
          :aria-checked="fallbackToStandardOnAgentError"
        >
          <span
            :class="[
              'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out',
              fallbackToStandardOnAgentError ? 'translate-x-5' : 'translate-x-0'
            ]"
          ></span>
        </button>
      </div>
    </div>

    <button
      @click="handleSaveSummarizationSettings"
      :disabled="isUpdatingSummarizationSettings"
      class="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-500 hover:bg-blue-600 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
    >
      <PhSpinner v-if="isUpdatingSummarizationSettings" :size="16" class="animate-spin" />
      <span>{{ isUpdatingSummarizationSettings ? '保存中...' : '保存设置' }}</span>
    </button>
  </div>
</template>
