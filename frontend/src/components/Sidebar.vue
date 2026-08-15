<script setup lang="ts">
import { ref } from 'vue'
import {
  PhWaveSine,
  PhX,
  PhGearSix,
  PhFolder,
  PhMagnifyingGlass,
} from '@phosphor-icons/vue'
import {
  type Task,
  type SummaryMode,
  type LLMProvider,
  type LLMSettings,
  type TranscriptionSettings,
  type SummarizationSettings,
  type QueueSnapshot,
  type ModelPathValidationResult,
  type VibeVoiceServiceStatus,
} from '../types'
import ThemeSelector from './ThemeSelector.vue'
import UploadForm from '../features/upload/components/UploadForm.vue'
import TaskList from '../features/task/components/TaskList.vue'
import TaskSearch from '../features/task/components/TaskSearch.vue'
import SettingsFormLlm from '../features/settings/components/SettingsFormLlm.vue'
import SettingsFormTranscription from '../features/settings/components/SettingsFormTranscription.vue'
import SettingsFormSummarization from '../features/settings/components/SettingsFormSummarization.vue'
import type { SearchMatchSource } from '../features/task/taskDisplay'

const videoUrl = defineModel<string>('videoUrl', { required: true })
const selectedFile = defineModel<File | null>('selectedFile', { default: null })
const localFilePath = defineModel<string>('localFilePath', { default: '' })
const summaryMode = defineModel<Exclude<SummaryMode, 'auto'>>('summaryMode', { default: 'none' })
// 仅转录模式的"总结标题"开关：默认开启，与后端 generate_topic 默认一致
const generateTopic = defineModel<boolean>('generateTopic', { default: true })
const isSidebarOpen = defineModel<boolean>('isSidebarOpen', { required: true })

const props = withDefaults(defineProps<{
  isLocalClient: boolean
  tasks: Task[]
  queues?: QueueSnapshot[]
  selectedTask: Task | null
  isSubmitting: boolean
  uploadProgress: number
  /** 上传大小上限（字节，后端 /upload/config 下发，与 storage.max_upload_mb 同源） */
  maxUploadBytes: number
  llmProviders: LLMProvider[]
  llmSettings: LLMSettings | null
  isUpdatingLlmSettings: boolean
  isTestingLlm: boolean
  transcriptionSettings: TranscriptionSettings | null
  isUpdatingTranscriptionSettings: boolean
  summarizationSettings: SummarizationSettings | null
  isUpdatingSummarizationSettings: boolean
  // 设置面板（SettingsFormTranscription，与 SettingsModal 双入口复用）所需 props
  isReadingBilibiliCookieFromBrowser?: boolean
  modelPathValidationResult?: ModelPathValidationResult | null
  isValidatingModelPath?: boolean
  vibevoiceServiceStatus?: VibeVoiceServiceStatus | null
  isScanningVibeVoice?: boolean
  isStartingVibeVoice?: boolean
  isStoppingVibeVoice?: boolean
  clearModelPathValidation?: () => void
}>(), {
  queues: () => [],
  isReadingBilibiliCookieFromBrowser: false,
  modelPathValidationResult: null,
  isValidatingModelPath: false,
  vibevoiceServiceStatus: null,
  isScanningVibeVoice: false,
  isStartingVibeVoice: false,
  isStoppingVibeVoice: false,
  clearModelPathValidation: () => {},
})

type LlmSettingsPayload = {
  provider: string
  base_url?: string
  api_key?: string
  model_id?: string
  temperature?: number
  extra_headers?: Record<string, string>
}

type TranscriptionSettingsPayload = {
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
}

type SummarizationSettingsPayload = {
  chunk_target_duration_sec?: number
  chunk_min_duration_sec?: number
  chunk_max_duration_sec?: number
  boundary_jump_sec?: number
  auto_chunk_min_audio_duration_sec?: number
  auto_chunk_min_transcript_lines?: number
  max_agent_value_chars?: number
  fallback_to_standard_on_agent_error?: boolean
}

const emit = defineEmits<{
  submit: []
  cancelSubmit: []
  selectTask: [task: Task]
  deleteTask: [taskId: string]
  retryTask: [task: Task]
  showInfo: [task: Task]
  openSettings: []
  focusSearchMatch: [payload: {
    taskId: string
    keyword: string
    source: SearchMatchSource
    requestId: number
  }]
  updateLlmSettings: [payload: LlmSettingsPayload]
  updateTranscriptionSettings: [payload: TranscriptionSettingsPayload]
  updateSummarizationSettings: [payload: SummarizationSettingsPayload]
  startTestLlm: []
  // 设置面板转录表单转发（与 SettingsModal 同款事件，Q8 双入口复用）
  readBilibiliCookieFromBrowser: []
  validateModelPath: [request: { path: string; transcriber_type: 'fast_whisper' | 'vibe_voice_asr' }]
  scanVibeVoiceServices: []
  fetchVibeVoiceServiceStatus: []
}>()

const isSettingsPanelOpen = ref(false)
const settingsTab = ref<'llm' | 'transcription' | 'summarization'>('llm')
const sidebarTab = ref<'quick' | 'manage' | 'theme'>('quick')
const appVersion = __APP_VERSION__

const handleSelectTaskFromList = (task: Task) => {
  emit('selectTask', task)
  isSidebarOpen.value = false
}

// 设置表单事件转发（保持 Sidebar public seam 不变）
const handleFormLlmSave = (payload: LlmSettingsPayload) => {
  emit('updateLlmSettings', payload)
}

const handleFormLlmSaveAndTest = (payload: LlmSettingsPayload) => {
  // 与原 Sidebar handleTestLlm 行为等价：先保存（同步 emit）再触发测试
  emit('updateLlmSettings', payload)
  emit('startTestLlm')
}

const handleFormTranscriptionSave = (payload: TranscriptionSettingsPayload) => {
  emit('updateTranscriptionSettings', payload)
}

const handleFormSummarizationSave = (payload: SummarizationSettingsPayload) => {
  emit('updateSummarizationSettings', payload)
}
</script>

<template>
  <aside
    :class="[
      'fixed md:static inset-y-0 left-0 z-40 w-[85%] max-w-[340px] md:w-[400px] bg-white border-r border-gray-100 h-full shadow-2xl md:shadow-none transition-transform duration-300 transform',
      isSidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'
    ]"
  >
    <div class="flex h-full">
      <!-- 左侧 tab rail -->
      <div class="w-14 shrink-0 border-r border-gray-100 bg-gray-50 flex flex-col items-center py-4 gap-2">
        <button
          @click="sidebarTab = 'quick'"
          :class="[
            'w-10 h-10 rounded-xl flex items-center justify-center transition-colors',
            sidebarTab === 'quick'
              ? 'bg-blue-50 text-primary'
              : 'text-slate-400 hover:bg-gray-100 hover:text-slate-600'
          ]"
          title="任务管理"
        >
          <PhFolder :size="20" />
        </button>

        <button
          @click="sidebarTab = 'manage'"
          :class="[
            'w-10 h-10 rounded-xl flex items-center justify-center transition-colors relative',
            sidebarTab === 'manage'
              ? 'bg-blue-50 text-primary'
              : 'text-slate-400 hover:bg-gray-100 hover:text-slate-600'
          ]"
          title="任务搜索"
        >
          <PhMagnifyingGlass :size="20" />
          <span class="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-gray-200 text-slate-500 text-[10px] leading-4 text-center">
            {{ tasks.length }}
          </span>
        </button>
      </div>

      <div class="flex-1 min-w-0 flex flex-col">
        <div class="relative">
          <!-- Logo -->
          <div class="p-4 border-b border-gray-100 flex items-center justify-between">
            <div class="flex items-center gap-2.5 min-w-0">
              <div class="w-9 h-9 bg-gradient-to-br from-blue-500 to-blue-600 rounded-2xl flex items-center justify-center text-white shadow-sm shadow-blue-200 shrink-0">
                <PhWaveSine :size="20" weight="bold" />
              </div>
              <div class="min-w-0">
                <h1 class="text-lg font-bold text-slate-900 tracking-tight truncate">声文智汇</h1>
                <p class="text-[11px] text-slate-500">
                  {{ sidebarTab === 'quick' ? '快速提交与任务浏览' : sidebarTab === 'manage' ? '全部任务搜索视图' : 'Markdown 样式主题' }} · v{{ appVersion }}
                </p>
              </div>
            </div>

            <div class="flex items-center gap-1">
              <button
                @click="emit('openSettings')"
                class="w-8 h-8 rounded-lg border border-slate-200 bg-white text-slate-400 hover:text-blue-600 hover:border-blue-200 hover:bg-blue-50 transition-colors flex items-center justify-center"
                title="设置"
              >
                <PhGearSix :size="16" />
              </button>
              <button @click="isSidebarOpen = false" class="md:hidden text-slate-400 hover:text-slate-600 p-1">
                <PhX :size="20" />
              </button>
            </div>
          </div>

          <Transition name="settings-pop">
            <div
              v-if="isSettingsPanelOpen"
              class="mx-4 mt-3 rounded-2xl border border-gray-100 bg-white shadow-lg p-3 max-h-[52dvh] overflow-y-auto relative z-20 md:absolute md:top-[72px] md:left-4 md:right-4 md:mx-0 md:mt-0 md:max-h-[70dvh] md:z-30"
            >
              <div class="grid grid-cols-3 gap-1 p-1 bg-slate-100 rounded-xl mb-3">
                <button
                  @click="settingsTab = 'llm'"
                  :class="[
                    'py-1.5 text-xs font-medium rounded-lg transition-colors',
                    settingsTab === 'llm' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                  ]"
                >
                  LLM
                </button>
                <button
                  @click="settingsTab = 'transcription'"
                  :class="[
                    'py-1.5 text-xs font-medium rounded-lg transition-colors',
                    settingsTab === 'transcription' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                  ]"
                >
                  转录
                </button>
                <button
                  @click="settingsTab = 'summarization'"
                  :class="[
                    'py-1.5 text-xs font-medium rounded-lg transition-colors',
                    settingsTab === 'summarization' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                  ]"
                >
                  Agent
                </button>
              </div>

              <!-- 与 SettingsModal 双入口复用的公共设置表单（Q8：语义以弹窗为准） -->
              <SettingsFormLlm
                v-if="settingsTab === 'llm'"
                :llm-providers="llmProviders"
                :llm-settings="llmSettings"
                :is-updating-llm-settings="isUpdatingLlmSettings"
                :is-testing-llm="isTestingLlm"
                @update-llm-settings="handleFormLlmSave"
                @update-llm-settings-and-test="handleFormLlmSaveAndTest"
              />
              <SettingsFormTranscription
                v-else-if="settingsTab === 'transcription'"
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
                :is-open="isSettingsPanelOpen && settingsTab === 'transcription'"
                @update-transcription-settings="handleFormTranscriptionSave"
                @read-bilibili-cookie-from-browser="emit('readBilibiliCookieFromBrowser')"
                @validate-model-path="(request) => emit('validateModelPath', request)"
                @scan-vibe-voice-services="emit('scanVibeVoiceServices')"
                @fetch-vibe-voice-service-status="emit('fetchVibeVoiceServiceStatus')"
              />
              <SettingsFormSummarization
                v-else
                :summarization-settings="summarizationSettings"
                :is-updating-summarization-settings="isUpdatingSummarizationSettings"
                @update-summarization-settings="handleFormSummarizationSave"
              />
            </div>
          </Transition>
        </div>

        <template v-if="sidebarTab === 'quick'">
          <!-- 提交新任务 -->
          <div class="p-3 pb-2">
            <UploadForm
              v-model:video-url="videoUrl"
              v-model:selected-file="selectedFile"
              v-model:local-file-path="localFilePath"
              v-model:summary-mode="summaryMode"
              v-model:generate-topic="generateTopic"
              :is-local-client="isLocalClient"
              :is-submitting="isSubmitting"
              :upload-progress="uploadProgress"
              :max-upload-bytes="maxUploadBytes"
              @submit="emit('submit')"
              @cancel-submit="emit('cancelSubmit')"
            />
          </div>

          <div class="flex-1 overflow-y-auto p-4 custom-scrollbar">
            <h2 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3 px-2">任务管理</h2>
            <TaskList
              :tasks="tasks"
              :selected-task="selectedTask"
              :queues="queues"
              @select-task="handleSelectTaskFromList"
              @delete-task="(taskId) => emit('deleteTask', taskId)"
              @retry-task="(task) => emit('retryTask', task)"
              @show-info="(task) => emit('showInfo', task)"
            />
          </div>
        </template>

        <template v-else-if="sidebarTab === 'manage'">
          <!-- 管理任务：搜索 + 结果列表 -->
          <TaskSearch
            :tasks="tasks"
            :selected-task="selectedTask"
            @select-task="handleSelectTaskFromList"
            @delete-task="(taskId) => emit('deleteTask', taskId)"
            @retry-task="(task) => emit('retryTask', task)"
            @show-info="(task) => emit('showInfo', task)"
            @focus-search-match="(payload) => emit('focusSearchMatch', payload)"
          />
        </template>

        <!-- 主题选择器 -->
        <template v-else>
          <ThemeSelector />
        </template>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.settings-pop-enter-active,
.settings-pop-leave-active {
  transition: opacity 180ms ease, transform 180ms ease;
}

.settings-pop-enter-from,
.settings-pop-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
