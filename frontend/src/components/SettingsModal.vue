<script setup lang="ts">
import { ref } from 'vue'
import {
  PhX,
  PhBrain,
  PhMicrophone,
  PhGitBranch,
} from '@phosphor-icons/vue'
import type {
  LLMProvider,
  LLMSettings,
  TranscriptionSettings,
  SummarizationSettings,
  ModelPathValidationResult,
  VibeVoiceServiceStatus,
} from '../types'
import SettingsFormLlm from '../features/settings/components/SettingsFormLlm.vue'
import SettingsFormTranscription from '../features/settings/components/SettingsFormTranscription.vue'
import SettingsFormSummarization from '../features/settings/components/SettingsFormSummarization.vue'

const props = defineProps<{
  isOpen: boolean
  llmProviders: LLMProvider[]
  llmSettings: LLMSettings | null
  isUpdatingLlmSettings: boolean
  isTestingLlm: boolean
  transcriptionSettings: TranscriptionSettings | null
  isUpdatingTranscriptionSettings: boolean
  summarizationSettings: SummarizationSettings | null
  isUpdatingSummarizationSettings: boolean
  isReadingBilibiliCookieFromBrowser: boolean
  modelPathValidationResult: ModelPathValidationResult | null
  isValidatingModelPath: boolean
  vibevoiceServiceStatus: VibeVoiceServiceStatus | null
  isScanningVibeVoice: boolean
  isStartingVibeVoice: boolean
  isStoppingVibeVoice: boolean
  clearModelPathValidation: () => void
}>()

const emit = defineEmits<{
  close: []
  updateLlmSettings: [payload: {
    provider: string
    base_url?: string
    api_key?: string
    model_id?: string
    temperature?: number
    extra_headers?: Record<string, string>
  }]
  testLlm: []
  updateLlmSettingsAndTest: [payload: {
    provider: string
    base_url?: string
    api_key?: string
    model_id?: string
    temperature?: number
    extra_headers?: Record<string, string>
  }]
  updateTranscriptionSettings: [payload: {
    device?: 'cpu' | 'cuda'
    model_source?: 'auto_download' | 'manual_path'
    model_size?: 'tiny' | 'base' | 'small' | 'medium' | 'large'
    model_path?: string
    enable_bilibili_subtitle_fetch?: boolean
    bilibili_sessdata?: string
    clear_bilibili_sessdata?: boolean
    transcriber_type?: 'fast_whisper' | 'vibe_voice_asr'
    vibevoice_language_model?: string
    vibevoice_max_new_tokens?: number
    vibevoice_dtype?: 'bfloat16' | 'float16'
    vibevoice_inference_mode?: 'local' | 'api'
    vibevoice_api_url?: string
  }]
  readBilibiliCookieFromBrowser: []
  validateModelPath: [request: { path: string; transcriber_type: 'fast_whisper' | 'vibe_voice_asr' }]
  scanVibeVoiceServices: []
  startVibeVoiceService: [payload: { model_path: string; port: number; dtype: string }]
  stopVibeVoiceService: []
  fetchVibeVoiceServiceStatus: []
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

const settingsTab = ref<'llm' | 'transcription' | 'summarization'>('llm')
</script>

<template>
  <!-- 遮罩层 -->
  <Transition name="modal-fade">
    <div
      v-if="isOpen"
      class="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex flex-col items-center justify-center p-4 gap-4"
      @click.self="emit('close')"
    >
      <!-- 弹窗主体 -->
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-4xl h-[85vh] flex flex-col md:flex-row overflow-hidden">
        <!-- 左侧导航栏 (桌面端) / 顶部导航 (移动端) -->
        <div class="md:w-48 shrink-0 bg-slate-50 border-b md:border-b-0 md:border-r border-slate-200 flex md:flex-col py-3 md:py-6 overflow-x-auto md:overflow-x-visible">
          <nav class="flex md:flex-col flex-1 px-3 gap-1 md:space-y-1 min-w-max md:min-w-0">
            <button
              @click="settingsTab = 'llm'"
              :class="[
                'flex items-center gap-2 md:gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap',
                settingsTab === 'llm'
                  ? 'bg-white text-blue-600 shadow-sm'
                  : 'text-slate-600 hover:bg-white/50 hover:text-slate-800'
              ]"
            >
              <PhBrain :size="18" :weight="settingsTab === 'llm' ? 'fill' : 'regular'" />
              <span>LLM 配置</span>
            </button>

            <button
              @click="settingsTab = 'transcription'"
              :class="[
                'flex items-center gap-2 md:gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap',
                settingsTab === 'transcription'
                  ? 'bg-white text-blue-600 shadow-sm'
                  : 'text-slate-600 hover:bg-white/50 hover:text-slate-800'
              ]"
            >
              <PhMicrophone :size="18" :weight="settingsTab === 'transcription' ? 'fill' : 'regular'" />
              <span>转录设置</span>
            </button>

            <button
              @click="settingsTab = 'summarization'"
              :class="[
                'flex items-center gap-2 md:gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap',
                settingsTab === 'summarization'
                  ? 'bg-white text-blue-600 shadow-sm'
                  : 'text-slate-600 hover:bg-white/50 hover:text-slate-800'
              ]"
            >
              <PhGitBranch :size="18" :weight="settingsTab === 'summarization' ? 'fill' : 'regular'" />
              <span>Agent 设置</span>
            </button>
          </nav>
        </div>

        <!-- 右侧内容区 -->
        <div class="flex-1 flex flex-col min-w-0 min-h-0">
          <!-- 头部栏 (仅桌面端) -->
          <div class="hidden md:flex items-center justify-between px-6 py-4 border-b border-slate-200 bg-white">
            <h2 class="text-lg font-semibold text-slate-800">
              {{ settingsTab === 'llm' ? 'LLM 配置' : settingsTab === 'transcription' ? '转录设置' : 'Agent 设置' }}
            </h2>
            <button
              @click="emit('close')"
              class="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
            >
              <PhX :size="20" />
            </button>
          </div>
          <!-- 内容区：与 Sidebar 面板双入口复用的公共设置表单（Q8：语义以弹窗为准）。
               v-show 常挂载（对抗评审 P1-2/P2-3 修订）：切 tab 只切 DOM 可见性，
               表单实例与本地 ref 常驻——未保存输入不丢失；SettingsFormTranscription
               的 [isOpen, vibevoiceInferenceMode] watch 与拆片前弹窗根层 watch 同时机
               （任意 tab 打开弹窗即按需拉取 VibeVoice 服务状态）。 -->
          <div class="flex-1 overflow-y-auto px-4 md:px-6 py-4 md:py-6 custom-scrollbar min-h-0">
            <div v-show="settingsTab === 'llm'">
              <SettingsFormLlm
                :llm-providers="llmProviders"
                :llm-settings="llmSettings"
                :is-updating-llm-settings="isUpdatingLlmSettings"
                :is-testing-llm="isTestingLlm"
                @update-llm-settings="(payload) => emit('updateLlmSettings', payload)"
                @update-llm-settings-and-test="(payload) => emit('updateLlmSettingsAndTest', payload)"
              />
            </div>
            <div v-show="settingsTab === 'transcription'">
              <SettingsFormTranscription
                :transcription-settings="transcriptionSettings"
                :is-updating-transcription-settings="isUpdatingTranscriptionSettings"
                :is-reading-bilibili-cookie-from-browser="isReadingBilibiliCookieFromBrowser"
                :model-path-validation-result="modelPathValidationResult"
                :is-validating-model-path="isValidatingModelPath"
                :vibevoice-service-status="vibevoiceServiceStatus"
                :is-scanning-vibe-voice="isScanningVibeVoice"
                :is-starting-vibe-voice="isStartingVibeVoice"
                :is-stopping-vibe-voice="isStoppingVibeVoice"
                :clear-model-path-validation="clearModelPathValidation"
                :is-open="isOpen"
                @update-transcription-settings="(payload) => emit('updateTranscriptionSettings', payload)"
                @read-bilibili-cookie-from-browser="emit('readBilibiliCookieFromBrowser')"
                @validate-model-path="(request) => emit('validateModelPath', request)"
                @scan-vibe-voice-services="emit('scanVibeVoiceServices')"
                @fetch-vibe-voice-service-status="emit('fetchVibeVoiceServiceStatus')"
              />
            </div>
            <div v-show="settingsTab === 'summarization'">
              <SettingsFormSummarization
                :summarization-settings="summarizationSettings"
                :is-updating-summarization-settings="isUpdatingSummarizationSettings"
                @update-summarization-settings="(payload) => emit('updateSummarizationSettings', payload)"
              />
            </div>
          </div>
        </div>
      </div>

      <!-- 移动端底部退出按钮 -->
      <button
        @click="emit('close')"
        class="md:hidden flex items-center justify-center gap-2 px-6 py-3 text-white text-sm font-medium transition-opacity hover:opacity-80"
      >
        <PhX :size="20" />
        <span>关闭</span>
      </button>
    </div>
  </Transition>
</template>

<style scoped>
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 200ms ease;
}

.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}

.modal-fade-enter-active > div,
.modal-fade-leave-active > div {
  transition: transform 200ms ease, opacity 200ms ease;
}

.modal-fade-enter-from > div,
.modal-fade-leave-to > div {
  transform: scale(0.95);
  opacity: 0;
}

.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}

.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}

.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}

.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}
</style>
