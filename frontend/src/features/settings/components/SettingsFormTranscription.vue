<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhCpu, PhKey, PhSpinner, PhMicrophone, PhGitBranch } from '@phosphor-icons/vue'
import type {
  TranscriptionSettings,
  ModelPathValidationResult,
  VibeVoiceServiceStatus,
} from '../../../types'

const props = defineProps<{
  transcriptionSettings: TranscriptionSettings | null
  isUpdatingTranscriptionSettings: boolean
  isReadingBilibiliCookieFromBrowser: boolean
  modelPathValidationResult: ModelPathValidationResult | null
  isValidatingModelPath: boolean
  vibevoiceServiceStatus: VibeVoiceServiceStatus | null
  isScanningVibeVoice: boolean
  isStartingVibeVoice: boolean
  isStoppingVibeVoice: boolean
  clearModelPathValidation: () => void
  isOpen?: boolean
}>()

const emit = defineEmits<{
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
  fetchVibeVoiceServiceStatus: []
}>()

// 转录设置
const transcriptionDevice = ref<'cpu' | 'cuda'>('cpu')
const transcriptionModelSource = ref<'auto_download' | 'manual_path'>('auto_download')
const transcriptionModelSize = ref<'tiny' | 'base' | 'small' | 'medium' | 'large'>('tiny')
const transcriptionModelPathInput = ref('')
const enableBilibiliSubtitleFetch = ref(true)
const globalBilibiliSessdataInput = ref('')
const transcriberType = ref<'fast_whisper' | 'vibe_voice_asr'>('fast_whisper')
const vibevoiceLanguageModel = ref('Qwen/Qwen2.5-7B')
const vibevoiceMaxNewTokens = ref(16384)
const vibevoiceDtype = ref<'bfloat16' | 'float16'>('bfloat16')
const vibevoiceInferenceMode = ref<'local' | 'api'>('local')
const vibevoiceApiUrl = ref('')

const bilibiliCookieSourceLabel = computed(() => {
  const source = props.transcriptionSettings?.bilibili_cookie_source || 'none'
  if (source === 'global') return '全局配置'
  if (source === 'env') return '环境变量'
  return '未设置'
})

const requiredModelFilesLabel = computed(() => {
  const files = props.transcriptionSettings?.required_model_files || []
  if (!files.length) return 'config.json, model.bin, tokenizer.json, vocabulary.txt'
  return files.join(', ')
})

watch(() => props.transcriptionSettings, (settings) => {
  if (settings) {
    transcriptionDevice.value = settings.device || 'cpu'
    transcriptionModelSource.value = settings.model_source || 'auto_download'
    transcriptionModelSize.value = settings.model_size || 'tiny'
    transcriptionModelPathInput.value = settings.model_path || ''
    enableBilibiliSubtitleFetch.value = settings.enable_bilibili_subtitle_fetch ?? true
    transcriberType.value = settings.transcriber_type || 'fast_whisper'
    vibevoiceLanguageModel.value = settings.vibevoice_language_model || 'Qwen/Qwen2.5-7B'
    vibevoiceMaxNewTokens.value = settings.vibevoice_max_new_tokens ?? 16384
    vibevoiceDtype.value = settings.vibevoice_dtype || 'bfloat16'
    vibevoiceInferenceMode.value = settings.vibevoice_inference_mode || 'local'
    vibevoiceApiUrl.value = settings.vibevoice_api_url || ''
  }
}, { immediate: true })

watch(
  [() => props.isOpen, vibevoiceInferenceMode],
  ([isOpen, inferenceMode]) => {
    if (isOpen && inferenceMode === 'api') {
      emit('fetchVibeVoiceServiceStatus')
    }
  },
  { immediate: true }
)

watch(transcriberType, () => {
  props.clearModelPathValidation()
})

const isVibeVoiceAvailable = computed(() => {
  const cuda = props.transcriptionSettings?.cuda_available ?? false
  const apiMode = vibevoiceInferenceMode.value === 'api'
  return cuda || apiMode
})

watch(transcriberType, (type) => {
  if (type === 'vibe_voice_asr' && isVibeVoiceAvailable.value) {
    transcriptionDevice.value = 'cuda'
  }
})

watch(transcriptionModelPathInput, (newVal, oldVal) => {
  if (props.modelPathValidationResult && newVal.trim() !== oldVal?.trim()) {
    props.clearModelPathValidation()
  }
})

const handleSaveTranscriptionSettings = () => {
  const payload: {
    device?: 'cpu' | 'cuda'
    model_source?: 'auto_download' | 'manual_path'
    model_size?: 'tiny' | 'base' | 'small' | 'medium' | 'large'
    model_path?: string
    enable_bilibili_subtitle_fetch?: boolean
    bilibili_sessdata?: string
    transcriber_type?: 'fast_whisper' | 'vibe_voice_asr'
    vibevoice_language_model?: string
    vibevoice_max_new_tokens?: number
    vibevoice_dtype?: 'bfloat16' | 'float16'
    vibevoice_inference_mode?: 'local' | 'api'
    vibevoice_api_url?: string
  } = {
    device: transcriptionDevice.value,
    transcriber_type: transcriberType.value,
    model_path: transcriptionModelPathInput.value.trim(),
    enable_bilibili_subtitle_fetch: enableBilibiliSubtitleFetch.value
  }

  if (transcriberType.value === 'fast_whisper') {
    payload.model_source = transcriptionModelSource.value
    payload.model_size = transcriptionModelSize.value
  }

  if (transcriberType.value === 'vibe_voice_asr') {
    payload.model_source = 'manual_path'
    payload.vibevoice_language_model = vibevoiceLanguageModel.value.trim()
    payload.vibevoice_max_new_tokens = Math.max(1, Number(vibevoiceMaxNewTokens.value) || 16384)
    payload.vibevoice_dtype = vibevoiceDtype.value
    payload.vibevoice_inference_mode = vibevoiceInferenceMode.value
    payload.vibevoice_api_url = vibevoiceApiUrl.value.trim()
  }

  const cookie = globalBilibiliSessdataInput.value.trim()
  if (cookie) {
    payload.bilibili_sessdata = cookie
  }

  emit('updateTranscriptionSettings', payload)
  globalBilibiliSessdataInput.value = ''
}

const clearGlobalBilibiliSessdata = () => {
  emit('updateTranscriptionSettings', { clear_bilibili_sessdata: true })
}

const handleValidateModelPath = () => {
  if (!transcriptionModelPathInput.value.trim()) return
  emit('validateModelPath', {
    path: transcriptionModelPathInput.value.trim(),
    transcriber_type: transcriberType.value,
  })
}

const handleReadBilibiliCookieFromBrowser = () => {
  emit('readBilibiliCookieFromBrowser')
}
</script>

<template>
  <div class="space-y-4">
    <!-- 硬件配置 -->
    <div class="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div class="flex items-center gap-2 pb-2 border-b border-slate-100">
        <PhCpu :size="18" class="text-blue-500" />
        <h3 class="text-sm font-semibold text-slate-800">硬件配置</h3>
      </div>

      <div v-if="transcriptionSettings" class="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
        <div class="flex items-center justify-between gap-2 mb-2">
          <p class="text-sm font-medium text-slate-700">CUDA 状态</p>
          <span
            :class="[
              'text-xs px-2 py-0.5 rounded-full border',
              transcriptionSettings.cuda_available
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                : 'border-amber-200 bg-amber-50 text-amber-700'
            ]"
          >
            {{ transcriptionSettings.cuda_available ? '可用' : '不可用' }}
          </span>
        </div>
        <p class="text-xs text-slate-600">
          {{ transcriptionSettings.cuda_message }}
        </p>
      </div>

      <div>
        <label class="block text-sm font-medium text-slate-700 mb-2">计算设备</label>
        <div class="grid grid-cols-2 gap-3">
          <button
            @click="transcriptionDevice = 'cpu'"
            :class="[
              'px-4 py-3 rounded-xl border-2 text-left transition-all',
              transcriptionDevice === 'cpu'
                ? 'border-blue-500 bg-blue-50 text-blue-700'
                : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
            ]"
          >
            <div class="font-medium">CPU</div>
            <div class="text-xs text-slate-500 mt-0.5">通用处理器</div>
          </button>
          <button
            @click="transcriptionDevice = 'cuda'"
            :class="[
              'px-4 py-3 rounded-xl border-2 text-left transition-all',
              transcriptionDevice === 'cuda'
                ? 'border-blue-500 bg-blue-50 text-blue-700'
                : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
            ]"
            :disabled="!transcriptionSettings?.cuda_available"
          >
            <div class="font-medium">CUDA</div>
            <div class="text-xs text-slate-500 mt-0.5">NVIDIA GPU 加速</div>
          </button>
        </div>
      </div>
    </div>

    <!-- 模型配置 -->
    <div class="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div class="flex items-center gap-2 pb-2 border-b border-slate-100">
        <PhMicrophone :size="18" class="text-blue-500" />
        <h3 class="text-sm font-semibold text-slate-800">模型配置</h3>
      </div>

      <!-- 转录器类型选择 -->
      <div>
        <label class="block text-sm font-medium text-slate-700 mb-2">转录器类型</label>
        <div class="grid grid-cols-2 gap-3">
          <button
            @click="transcriberType = 'fast_whisper'"
            :class="[
              'px-4 py-3 rounded-xl border-2 text-left transition-all',
              transcriberType === 'fast_whisper'
                ? 'border-blue-500 bg-blue-50 text-blue-700'
                : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
            ]"
          >
            <div class="font-medium">FastWhisper</div>
            <div class="text-xs text-slate-500 mt-0.5">CTranslate2 本地推理</div>
          </button>
          <button
            @click="transcriberType = 'vibe_voice_asr'"
            :disabled="!isVibeVoiceAvailable"
            :class="[
              'px-4 py-3 rounded-xl border-2 text-left transition-all',
              transcriberType === 'vibe_voice_asr'
                ? 'border-blue-500 bg-blue-50 text-blue-700'
                : isVibeVoiceAvailable
                  ? 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
                  : 'border-slate-200 bg-slate-100 text-slate-400 cursor-not-allowed opacity-60'
            ]"
          >
            <div class="font-medium">VibeVoice ASR</div>
            <div class="text-xs text-slate-500 mt-0.5">{{ isVibeVoiceAvailable ? 'VibeVoice 本地模型' : '需要 CUDA 环境' }}</div>
          </button>
        </div>
      </div>

    <div v-if="transcriberType === 'fast_whisper'" class="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 space-y-3">
      <div class="flex items-center justify-between gap-2">
        <p class="text-sm font-medium text-slate-700">转录模型来源</p>
        <span class="text-xs px-2 py-0.5 rounded-full border border-slate-300 bg-white text-slate-600">
          {{ transcriptionModelSource === 'auto_download' ? '自动下载' : '手动目录' }}
        </span>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <button
          @click="transcriptionModelSource = 'auto_download'"
          :class="[
            'px-4 py-3 rounded-xl border-2 text-left transition-all',
            transcriptionModelSource === 'auto_download'
              ? 'border-blue-500 bg-blue-50 text-blue-700'
              : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
          ]"
        >
          <div class="font-medium">自动下载</div>
          <div class="text-xs text-slate-500 mt-0.5">按模型大小自动加载</div>
        </button>
        <button
          @click="transcriptionModelSource = 'manual_path'"
          :class="[
            'px-4 py-3 rounded-xl border-2 text-left transition-all',
            transcriptionModelSource === 'manual_path'
              ? 'border-blue-500 bg-blue-50 text-blue-700'
              : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
          ]"
        >
          <div class="font-medium">手动指定目录</div>
          <div class="text-xs text-slate-500 mt-0.5">使用本地已下载模型</div>
        </button>
      </div>

      <div>
        <label class="block text-xs text-slate-600 mb-1.5">模型大小（自动下载模式）</label>
        <select
          v-model="transcriptionModelSize"
          class="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 disabled:opacity-60"
          :disabled="transcriptionModelSource !== 'auto_download'"
        >
          <option value="tiny">tiny</option>
          <option value="base">base</option>
          <option value="small">small</option>
          <option value="medium">medium</option>
          <option value="large">large</option>
        </select>
      </div>

      <div>
        <label class="block text-xs text-slate-600 mb-1.5">模型目录（手动指定模式）</label>
        <input
          v-model="transcriptionModelPathInput"
          type="text"
          placeholder="例如: E:/models/faster-whisper/tiny"
          class="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 disabled:opacity-60"
          :disabled="transcriptionModelSource !== 'manual_path'"
        >
        <p class="mt-1 text-xs text-slate-500">
          目录内需包含: {{ requiredModelFilesLabel }}
        </p>
      </div>

      <p
        v-if="transcriptionModelSource === 'manual_path'"
        class="text-xs whitespace-pre-line"
        :class="transcriptionSettings?.model_path_valid ? 'text-emerald-700' : 'text-amber-700'"
      >
        {{ transcriptionSettings?.model_path_message || '手动模式将校验目录完整性。' }}
      </p>
    </div>

      <!-- VibeVoice 配置 -->
      <div v-if="transcriberType === 'vibe_voice_asr'" class="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 space-y-3">
        <div>
          <label class="block text-xs text-slate-600 mb-1.5">VibeVoice 模型目录</label>
          <input
            v-model="transcriptionModelPathInput"
            type="text"
            placeholder="例如: /models/vibevoice-asr"
            class="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
        </div>

        <!-- 验证路径按钮 -->
        <div class="flex items-center gap-3">
          <button
            @click="handleValidateModelPath"
            :disabled="isValidatingModelPath || !transcriptionModelPathInput.trim()"
            class="flex items-center gap-2 px-4 py-2 bg-white hover:bg-slate-50 text-slate-600 rounded-xl text-xs font-medium border border-slate-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <PhSpinner v-if="isValidatingModelPath" :size="14" class="animate-spin" />
            <span>{{ isValidatingModelPath ? '验证中...' : '验证路径' }}</span>
          </button>
          <span
            v-if="modelPathValidationResult"
            :class="[
              'text-xs px-2 py-0.5 rounded-full border',
              modelPathValidationResult.valid
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                : 'border-red-200 bg-red-50 text-red-700'
            ]"
          >
            {{ modelPathValidationResult.valid ? '路径有效' : '路径无效' }}
          </span>
        </div>
        <p
          v-if="modelPathValidationResult"
          class="text-xs whitespace-pre-line"
          :class="modelPathValidationResult.valid ? 'text-emerald-700' : 'text-red-600'"
        >
          {{ modelPathValidationResult.message }}
        </p>

        <!-- 推理模式选择 -->
        <div>
          <label class="block text-xs text-slate-600 mb-1.5">推理模式</label>
          <div class="grid grid-cols-2 gap-3">
            <button
              @click="vibevoiceInferenceMode = 'local'"
              :class="[
                'px-4 py-3 rounded-xl border-2 text-left transition-all',
                vibevoiceInferenceMode === 'local'
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
              ]"
            >
              <div class="font-medium">本地加载</div>
              <div class="text-xs text-slate-500 mt-0.5">需要 CUDA GPU</div>
            </button>
            <button
              @click="vibevoiceInferenceMode = 'api'"
              :class="[
                'px-4 py-3 rounded-xl border-2 text-left transition-all',
                vibevoiceInferenceMode === 'api'
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
              ]"
            >
              <div class="font-medium">推理服务</div>
              <div class="text-xs text-slate-500 mt-0.5">连接 vLLM API</div>
            </button>
          </div>
        </div>

        <!-- API 模式配置 -->
        <div v-if="vibevoiceInferenceMode === 'api'" class="space-y-3">
          <div>
            <label class="block text-xs text-slate-600 mb-1.5">推理服务地址</label>
            <input
              v-model="vibevoiceApiUrl"
              type="text"
              placeholder="例如: http://localhost:8000"
              class="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
            >
          </div>

          <div class="flex items-center gap-2">
            <button
              @click="emit('scanVibeVoiceServices')"
              :disabled="isScanningVibeVoice"
              class="flex items-center gap-2 px-4 py-2 bg-white hover:bg-slate-50 text-slate-600 rounded-xl text-xs font-medium border border-slate-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <PhSpinner v-if="isScanningVibeVoice" :size="14" class="animate-spin" />
              <span>{{ isScanningVibeVoice ? '扫描中...' : '扫描本地服务' }}</span>
            </button>
          </div>

          <div v-if="vibevoiceServiceStatus" class="flex items-center gap-2 text-xs">
            <span :class="[
              'px-2 py-0.5 rounded-full border',
              vibevoiceServiceStatus.api_healthy
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                : 'border-red-200 bg-red-50 text-red-700'
            ]">
              {{ vibevoiceServiceStatus.api_healthy ? '服务正常' : '服务不可达' }}
            </span>
            <span v-if="vibevoiceServiceStatus.running" class="text-slate-500">
              PID: {{ vibevoiceServiceStatus.pid }}
            </span>
          </div>

          <div class="text-xs text-slate-500">
            输入 vLLM 服务地址，或点击"扫描"自动发现本地服务。
          </div>
        </div>

        <!-- 语言模型 -->
        <div v-if="vibevoiceInferenceMode === 'local'">
          <label class="block text-xs text-slate-600 mb-1.5">语言模型</label>
          <input
            v-model="vibevoiceLanguageModel"
            type="text"
            placeholder="例如: Qwen/Qwen2.5-7B"
            class="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
        </div>

        <!-- max_new_tokens -->
        <div v-if="vibevoiceInferenceMode === 'local'">
          <label class="block text-xs text-slate-600 mb-1.5">最大生成 Token 数</label>
          <input
            v-model.number="vibevoiceMaxNewTokens"
            type="number"
            min="1"
            class="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
        </div>

        <!-- dtype -->
        <div v-if="vibevoiceInferenceMode === 'local'">
          <label class="block text-xs text-slate-600 mb-1.5">数据类型</label>
          <select
            v-model="vibevoiceDtype"
            class="w-full px-3 py-2.5 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
          >
            <option value="bfloat16">bfloat16</option>
            <option value="float16">float16</option>
          </select>
        </div>

        <!-- CUDA 提示 -->
        <div v-if="vibevoiceInferenceMode === 'local'" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
          <p class="text-xs text-amber-700">
            VibeVoice ASR 需要 CUDA 环境。请确保 GPU 可用且显存充足（7B 模型约需 14GB VRAM）。
          </p>
        </div>
      </div>
    </div>

    <!-- B站字幕配置 -->
    <div class="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div class="flex items-center gap-2 pb-2 border-b border-slate-100">
        <PhGitBranch :size="18" class="text-blue-500" />
        <h3 class="text-sm font-semibold text-slate-800">B站字幕配置</h3>
      </div>

      <!-- B站字幕提取 -->
      <div class="flex items-center justify-between gap-3 px-4 py-3 rounded-xl border border-slate-200 bg-slate-50">
      <div class="flex-1 min-w-0">
        <p class="text-sm font-medium text-slate-700">优先使用 B 站字幕</p>
        <p class="text-xs text-slate-500 mt-0.5">
          仅对 B 站链接生效；未获取到字幕时自动回退到下载+ASR
        </p>
      </div>
      <button
        @click="enableBilibiliSubtitleFetch = !enableBilibiliSubtitleFetch"
        :class="[
          'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2',
          enableBilibiliSubtitleFetch ? 'bg-blue-500' : 'bg-slate-300'
        ]"
        role="switch"
        :aria-checked="enableBilibiliSubtitleFetch"
      >
        <span
          :class="[
            'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out',
            enableBilibiliSubtitleFetch ? 'translate-x-5' : 'translate-x-0'
          ]"
        ></span>
      </button>
    </div>

    <!-- B站 SESSDATA -->
    <div class="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 space-y-3">
      <div class="flex items-center justify-between gap-2">
        <p class="text-sm font-medium text-slate-700">全局 B 站 SESSDATA</p>
        <span class="text-xs px-2 py-0.5 rounded-full border border-slate-300 bg-white text-slate-600">
          来源: {{ bilibiliCookieSourceLabel }}
        </span>
      </div>
      <p class="text-xs text-slate-500">
        当前: {{ transcriptionSettings?.bilibili_sessdata_masked || '未设置' }}
      </p>
      <div class="relative">
        <PhKey :size="16" class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          v-model="globalBilibiliSessdataInput"
          type="password"
          placeholder="输入后保存到本机配置"
          class="w-full pl-10 pr-3 py-2.5 bg-white border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
        >
      </div>
      <p class="text-xs text-amber-600 mt-1">
        提示：如果填写 SESSDATA 后依然获取字幕不成功，请尝试在对应浏览器重新登录或手动复制 SESSDATA。
      </p>
      <div class="grid grid-cols-2 gap-2">
        <button
          @click="handleReadBilibiliCookieFromBrowser"
          :disabled="isReadingBilibiliCookieFromBrowser || isUpdatingTranscriptionSettings"
          class="flex items-center justify-center gap-2 bg-white hover:bg-slate-50 text-slate-600 py-2 rounded-xl text-xs font-medium border border-slate-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <PhSpinner v-if="isReadingBilibiliCookieFromBrowser" :size="14" class="animate-spin" />
          <span>{{ isReadingBilibiliCookieFromBrowser ? '读取中...' : '从浏览器读取' }}</span>
        </button>
        <button
          @click="clearGlobalBilibiliSessdata"
          :disabled="isUpdatingTranscriptionSettings || !transcriptionSettings?.has_bilibili_sessdata"
          class="bg-white hover:bg-slate-50 text-slate-600 py-2 rounded-xl text-xs font-medium border border-slate-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
        >
          清空已保存 Cookie
        </button>
      </div>
    </div>
    </div>

    <button
      @click="handleSaveTranscriptionSettings"
      :disabled="isUpdatingTranscriptionSettings"
      class="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-500 hover:bg-blue-600 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
    >
      <PhSpinner v-if="isUpdatingTranscriptionSettings" :size="16" class="animate-spin" />
      <span>{{ isUpdatingTranscriptionSettings ? '保存中...' : '保存设置' }}</span>
    </button>
  </div>
</template>
