<script setup lang="ts">
import { ref } from 'vue'
import {
  PhLink,
  PhUpload,
  PhQuestion,
  PhFile,
  PhX,
  PhWarning,
  PhFileText,
  PhLightning,
  PhBrain,
  PhPlayCircle,
  PhSpinner,
} from '@phosphor-icons/vue'
import type { SummaryMode } from '../../../types'
import { formatFileSize } from '../../../utils/formatters'

const videoUrl = defineModel<string>('videoUrl', { required: true })
const selectedFile = defineModel<File | null>('selectedFile', { default: null })
const localFilePath = defineModel<string>('localFilePath', { default: '' })
const summaryMode = defineModel<Exclude<SummaryMode, 'auto'>>('summaryMode', { default: 'none' })
// 仅转录模式的"总结标题"开关：默认开启，与后端 generate_topic 默认一致
const generateTopic = defineModel<boolean>('generateTopic', { default: true })

const props = defineProps<{
  isLocalClient: boolean
  isSubmitting: boolean
  uploadProgress: number
  /** 上传大小上限（字节，后端 /upload/config 下发，与 storage.max_upload_mb 同源） */
  maxUploadBytes: number
}>()

const emit = defineEmits<{
  submit: []
  cancelSubmit: []
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const showLocalPathHelp = ref(false)

// 文件大小预检错误（超 maxUploadBytes 上限）
const fileSizeError = ref<string | null>(null)

// 模式 Tab 三态（仅转录 none / 标准 standard / Agent agent）的滑动 thumb 定位。
// 显式 Record 映射（含后端/历史兼容值 auto），杜绝三元表达式枚举落空错位。
const modeThumbClass: Record<SummaryMode, string> = {
  none: 'left-1 bg-white shadow-sm',
  standard: 'left-[calc(33.333%)] bg-white shadow-sm',
  agent: 'left-[calc(66.667%)] agent-gradient shadow-[0_8px_24px_rgba(59,130,246,0.35)]',
  auto: 'left-1 bg-white shadow-sm',
}

const triggerFileUpload = () => {
  fileInput.value?.click()
}

const handleSubmitAction = () => {
  if (props.isSubmitting) {
    emit('cancelSubmit')
    return
  }
  emit('submit')
}

const handleVideoUrlEnter = () => {
  if (props.isSubmitting) return
  if (!videoUrl.value.trim()) return
  handleSubmitAction()
}

const handleLocalPathEnter = () => {
  if (props.isSubmitting) return
  if (!localFilePath.value.trim()) return
  handleSubmitAction()
}

const handleFileChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) {
    // 大小预检：上限来自后端 /upload/config（与 storage.max_upload_mb 同源），超限拒绝并提示
    if (file.size > props.maxUploadBytes) {
      selectedFile.value = null
      target.value = ''
      fileSizeError.value = `文件过大（${formatFileSize(file.size)}），最大支持 ${formatFileSize(props.maxUploadBytes)}`
      return
    }
    fileSizeError.value = null
    // 清空 URL 输入框（互斥模式）
    videoUrl.value = ''
    localFilePath.value = ''
    selectedFile.value = file
  }
}

const handleLocalPathInput = (event: Event) => {
  selectedFile.value = null
  videoUrl.value = ''

  // 自动清理路径格式
  const input = event.target as HTMLInputElement
  let value = input.value

  // 去除首尾空格和引号
  value = value.trim()
  if ((value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))) {
    value = value.slice(1, -1)
  }

  // 更新清理后的值
  if (value !== input.value) {
    localFilePath.value = value
  }
}

const handleClearSelectedFile = () => {
  if (props.isSubmitting) {
    emit('cancelSubmit')
  }
  selectedFile.value = null
  fileSizeError.value = null
}
</script>

<template>
  <div class="space-y-2.5">
    <div class="relative">
      <PhLink :size="18" class="absolute left-3 top-3 text-slate-400" />
      <input
        v-model="videoUrl"
        type="text"
        placeholder="粘贴视频 URL (如 Bilibili)"
        class="w-full pl-10 pr-12 py-2.5 bg-gray-50 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all text-sm"
        @input="selectedFile = null; localFilePath = ''"
        @keydown.enter.prevent="handleVideoUrlEnter"
      >
      <button
        @click="triggerFileUpload"
        class="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 text-slate-400 hover:text-primary hover:bg-blue-50 rounded-lg transition-colors"
        title="上传文件"
      >
        <PhUpload :size="18" />
      </button>
      <input
        ref="fileInput"
        type="file"
        accept="video/*,audio/*"
        class="hidden"
        @change="handleFileChange"
      >
    </div>

    <div
      v-if="props.isLocalClient"
      class="flex items-center gap-1.5 px-1 mt-3 mb-1"
    >
      <span class="text-xs font-medium text-slate-600">根据本地文件路径创建</span>
      <button
        @click="showLocalPathHelp = !showLocalPathHelp"
        class="p-0.5 text-slate-400 hover:text-primary hover:bg-blue-50 rounded transition-colors"
        title="如何复制本地文件路径"
      >
        <PhQuestion :size="14" weight="bold" />
      </button>
    </div>

    <!-- 帮助提示 -->
    <div
      v-if="props.isLocalClient && showLocalPathHelp"
      class="mx-1 mb-2 p-2.5 bg-blue-50 border border-blue-100 rounded-lg text-xs text-slate-600 space-y-1"
    >
      <p class="font-medium text-slate-700">快速复制文件路径：</p>
      <p>• Windows: 按住 <kbd class="px-1 py-0.5 bg-white border border-slate-300 rounded text-[10px] font-mono">Shift</kbd> + 右键文件 → "复制为路径"</p>
      <p>• 或直接从文件管理器地址栏复制完整路径</p>
    </div>

    <div
      v-if="props.isLocalClient"
      class="relative"
    >
      <PhFile :size="18" class="absolute left-3 top-3 text-slate-400" />
      <input
        v-model="localFilePath"
        type="text"
        placeholder="粘贴本机文件路径"
        class="w-full pl-10 pr-10 py-2.5 bg-gray-50 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all text-sm"
        @input="handleLocalPathInput"
        @keydown.enter.prevent="handleLocalPathEnter"
      >
      <button
        v-if="localFilePath"
        @click="localFilePath = ''"
        class="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
        title="清空路径"
      >
        <PhX :size="16" />
      </button>
    </div>

    <div v-if="selectedFile" class="flex items-center gap-2 px-3 py-2 bg-blue-50 border border-blue-100 rounded-lg text-sm">
      <div class="flex-1 min-w-0">
        <p class="font-medium text-slate-700 truncate">{{ selectedFile.name }}</p>
        <p class="text-xs text-slate-500">{{ formatFileSize(selectedFile.size) }}</p>
      </div>
      <button
        @click="handleClearSelectedFile"
        class="p-1 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded transition-colors"
        title="清除文件"
      >
        <PhX :size="16" />
      </button>
    </div>

    <div
      v-if="fileSizeError"
      class="flex items-center gap-1.5 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600"
    >
      <PhWarning :size="14" />
      {{ fileSizeError }}
    </div>

    <div class="relative">
      <div class="relative flex bg-gray-100 p-1 rounded-2xl transition-all duration-200 overflow-visible">
        <div
          class="absolute top-1 bottom-1 w-[calc(33.333%-6px)] rounded-xl transition-all duration-300 ease-out"
          :class="modeThumbClass[summaryMode]"
        ></div>

        <div
          class="pointer-events-none absolute -right-3 -bottom-3 h-10 w-28 rounded-full agent-glow blur-xl transition-opacity duration-300"
          :class="summaryMode === 'agent' ? 'opacity-100' : 'opacity-0'"
        ></div>

        <button
          type="button"
          @click="summaryMode = 'none'"
          class="relative z-10 flex-1 px-3 py-2 rounded-xl text-xs font-medium transition-colors inline-flex items-center justify-center gap-1.5"
          :class="summaryMode === 'none' ? 'text-amber-700' : 'text-slate-500 hover:text-slate-700'"
        >
          <PhFileText :size="13" :weight="summaryMode === 'none' ? 'fill' : 'regular'" />
          <span>仅转录</span>
        </button>
        <button
          type="button"
          @click="summaryMode = 'standard'"
          class="relative z-10 flex-1 px-3 py-2 rounded-xl text-xs font-medium transition-colors inline-flex items-center justify-center gap-1.5"
          :class="summaryMode === 'standard' ? 'text-primary' : 'text-slate-500 hover:text-slate-700'"
        >
          <PhLightning :size="13" :weight="summaryMode === 'standard' ? 'fill' : 'regular'" />
          <span>标准模式</span>
        </button>
        <button
          type="button"
          @click="summaryMode = 'agent'"
          class="relative z-10 flex-1 px-3 py-2 rounded-xl text-xs font-medium transition-colors inline-flex items-center justify-center gap-1.5"
          :class="summaryMode === 'agent' ? 'text-white' : 'text-slate-500 hover:text-slate-700'"
        >
          <PhBrain :size="13" :weight="summaryMode === 'agent' ? 'fill' : 'regular'" />
          <span>Agent 模式</span>
        </button>
      </div>
    </div>
    <p
      v-if="summaryMode === 'none'"
      class="text-[11px] text-amber-600/90 px-1 -mt-0.5 leading-relaxed"
    >提交后不生成 AI 总结，之后可随时在任务上补总结。</p>

    <!-- 仅转录模式的"总结标题"开关：默认开启，转录完成后对全文生成标题 -->
    <div
      v-if="summaryMode === 'none'"
      class="flex items-center justify-between gap-3 px-3 py-2 rounded-xl border border-amber-200 bg-amber-50/60"
    >
      <div class="flex-1 min-w-0">
        <p class="text-xs font-medium text-slate-700">总结标题</p>
        <p class="text-[11px] text-slate-500 leading-relaxed mt-0.5">
          仅转录完成后自动生成标题
        </p>
      </div>
      <button
        type="button"
        @click="generateTopic = !generateTopic"
        :class="[
          'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-amber-500 focus:ring-offset-2',
          generateTopic ? 'bg-amber-500' : 'bg-gray-300'
        ]"
        role="switch"
        :aria-checked="generateTopic"
      >
        <span
          :class="[
            'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out',
            generateTopic ? 'translate-x-5' : 'translate-x-0'
          ]"
        ></span>
      </button>
    </div>

    <button
      @click="handleSubmitAction"
      :disabled="!isSubmitting && !videoUrl && !selectedFile && !localFilePath"
      class="w-full bg-primary hover:bg-secondary text-white py-2.5 rounded-xl font-semibold transition-all shadow-sm shadow-blue-100 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed active:scale-95"
    >
      <PhSpinner v-if="isSubmitting" :size="18" class="animate-spin" />
      <PhPlayCircle v-else :size="18" />
      {{ isSubmitting && uploadProgress > 0 ? `上传中 ${uploadProgress}%` : isSubmitting ? '取消提交' : '开始处理' }}
    </button>

    <!-- 文件上传真实进度条 -->
    <div v-if="isSubmitting && uploadProgress > 0" class="h-1.5 bg-gray-100 rounded-full overflow-hidden">
      <div
        class="h-full bg-primary rounded-full transition-all duration-300"
        :style="{ width: uploadProgress + '%' }"
      ></div>
    </div>
  </div>
</template>

<style scoped>
/* Agent 模式极光流动动画 */
@keyframes aurora-flow {
  0% {
    background-position: 0% 50%;
    background-size: 180% 180%;
  }
  15% {
    background-position: 30% 20%;
    background-size: 220% 200%;
  }
  33% {
    background-position: 80% 30%;
    background-size: 200% 240%;
  }
  50% {
    background-position: 100% 70%;
    background-size: 190% 190%;
  }
  67% {
    background-position: 60% 100%;
    background-size: 230% 210%;
  }
  85% {
    background-position: 20% 80%;
    background-size: 200% 220%;
  }
  100% {
    background-position: 0% 50%;
    background-size: 180% 180%;
  }
}

.agent-gradient {
  background: linear-gradient(
    125deg,
    #06b6d4,
    #3b82f6,
    #5b8ff9,
    #3b82f6,
    #6366f1,
    #3b82f6,
    #06b6d4
  );
  background-size: 180% 180%;
  animation: aurora-flow 12s ease-in-out infinite;
}

.agent-glow {
  background: linear-gradient(
    125deg,
    rgba(6, 182, 212, 0.25),
    rgba(59, 130, 246, 0.3),
    rgba(91, 143, 249, 0.28),
    rgba(59, 130, 246, 0.3),
    rgba(99, 102, 241, 0.22),
    rgba(59, 130, 246, 0.3),
    rgba(6, 182, 212, 0.25)
  );
  background-size: 180% 180%;
  animation: aurora-flow 12s ease-in-out infinite;
}
</style>
