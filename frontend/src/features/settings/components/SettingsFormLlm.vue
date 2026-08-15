<script setup lang="ts">
import { ref, watch } from 'vue'
import { PhCpu, PhKey, PhFlask, PhSpinner, PhBrain } from '@phosphor-icons/vue'
import type { LLMProvider, LLMSettings } from '../../../types'

const props = defineProps<{
  llmProviders: LLMProvider[]
  llmSettings: LLMSettings | null
  isUpdatingLlmSettings: boolean
  isTestingLlm: boolean
}>()

const emit = defineEmits<{
  updateLlmSettings: [payload: {
    provider: string
    base_url?: string
    api_key?: string
    model_id?: string
    temperature?: number
    extra_headers?: Record<string, string>
  }]
  updateLlmSettingsAndTest: [payload: {
    provider: string
    base_url?: string
    api_key?: string
    model_id?: string
    temperature?: number
    extra_headers?: Record<string, string>
  }]
}>()

// LLM 设置
const llmProvider = ref('')
const llmBaseUrl = ref('')
const llmModelId = ref('')
const llmTemperature = ref(0.7)
const llmApiKey = ref('')
const llmExtraHeaders = ref('')

watch(() => props.llmSettings, (settings) => {
  if (settings) {
    llmProvider.value = settings.provider || ''
    llmBaseUrl.value = settings.base_url || ''
    llmModelId.value = settings.model_id || ''
    llmTemperature.value = settings.temperature ?? 0.7
    llmApiKey.value = ''
    const eh = settings.extra_headers
    llmExtraHeaders.value = eh && Object.keys(eh).length ? JSON.stringify(eh, null, 2) : ''
  }
}, { immediate: true })

const handleProviderPresetChange = () => {
  const provider = props.llmProviders.find(p => p.id === llmProvider.value)
  if (provider) {
    llmBaseUrl.value = provider.default_base_url || ''
    llmModelId.value = provider.default_model_id || ''
  }
}

const buildLlmPayload = () => {
  const payload: {
    provider: string
    base_url?: string
    api_key?: string
    model_id?: string
    temperature?: number
    extra_headers?: Record<string, string>
  } = {
    provider: llmProvider.value,
    base_url: llmBaseUrl.value.trim(),
    model_id: llmModelId.value.trim(),
    temperature: llmTemperature.value,
  }

  if (llmApiKey.value.trim()) {
    payload.api_key = llmApiKey.value.trim()
  }

  try {
    const parsed = JSON.parse(llmExtraHeaders.value.trim())
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      payload.extra_headers = parsed
    }
  } catch { /* empty or invalid JSON — omit extra_headers */ }

  return payload
}

const handleSaveLlmSettings = () => {
  emit('updateLlmSettings', buildLlmPayload())
  llmApiKey.value = ''
}

const handleTestLlm = () => {
  emit('updateLlmSettingsAndTest', buildLlmPayload())
  llmApiKey.value = ''
}
</script>

<template>
  <div class="space-y-4">
    <!-- 基础配置 -->
    <div class="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div class="flex items-center gap-2 pb-2 border-b border-slate-100">
        <PhBrain :size="18" class="text-blue-500" />
        <h3 class="text-sm font-semibold text-slate-800">基础配置</h3>
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-700 mb-2">LLM 供应商</label>
        <div class="relative">
          <PhCpu :size="16" class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <select
            v-model="llmProvider"
            :disabled="isTestingLlm || isUpdatingLlmSettings"
            class="w-full pl-9 pr-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            @change="handleProviderPresetChange"
          >
            <option value="" disabled>选择 LLM 供应商</option>
            <option v-for="provider in llmProviders" :key="provider.id" :value="provider.id">
              {{ provider.label }}
            </option>
          </select>
        </div>
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-700 mb-2">Base URL</label>
        <input
          v-model="llmBaseUrl"
          type="text"
          placeholder="https://api.example.com/v1"
          :disabled="isTestingLlm || isUpdatingLlmSettings"
          class="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-700 mb-2">模型 ID</label>
        <input
          v-model="llmModelId"
          type="text"
          placeholder="example-model-id"
          :disabled="isTestingLlm || isUpdatingLlmSettings"
          class="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-700 mb-2">Temperature ({{ llmTemperature }})</label>
        <input
          v-model.number="llmTemperature"
          type="range"
          min="0"
          max="2"
          step="0.1"
          :disabled="isTestingLlm || isUpdatingLlmSettings"
          class="w-full h-2 bg-slate-200 rounded-lg appearance-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        >
        <p class="text-xs text-slate-500 mt-1">控制输出的随机性，0 = 确定性，2 = 最随机</p>
      </div>
    </div>

    <!-- API 密钥 -->
    <div class="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div class="flex items-center gap-2 pb-2 border-b border-slate-100">
        <PhKey :size="18" class="text-blue-500" />
        <h3 class="text-sm font-semibold text-slate-800">API 密钥</h3>
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-700 mb-2">API Key</label>
        <div class="relative">
          <PhKey :size="16" class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            v-model="llmApiKey"
            type="password"
            placeholder="sk-..."
            :disabled="isTestingLlm || isUpdatingLlmSettings"
            class="w-full pl-9 pr-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
        </div>
        <p v-if="llmSettings?.has_api_key" class="text-xs text-emerald-600 mt-2">
          ✓ 已配置 API Key ({{ llmSettings.api_key_hint }})
        </p>
      </div>
    </div>

    <!-- Extra Headers (仅 Anthropic 等需要) -->
    <div v-if="llmProvider === 'anthropic'" class="rounded-xl border border-slate-200 bg-white p-4 space-y-4">
      <div class="flex items-center gap-2 pb-2 border-b border-slate-100">
        <PhKey :size="18" class="text-blue-500" />
        <h3 class="text-sm font-semibold text-slate-800">Extra Headers</h3>
      </div>

      <div>
        <label class="block text-xs font-medium text-slate-700 mb-2">自定义请求头 (JSON)</label>
        <textarea
          v-model="llmExtraHeaders"
          placeholder='{"anthropic-version": "2023-06-01"}'
          rows="3"
          :disabled="isTestingLlm || isUpdatingLlmSettings"
          class="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors resize-y"
        />
        <p class="text-xs text-slate-500 mt-1">留空表示不设置额外请求头，格式为 JSON 对象</p>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="flex gap-3">
      <button
        @click="handleSaveLlmSettings"
        :disabled="isTestingLlm || isUpdatingLlmSettings"
        class="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-500 hover:bg-blue-600 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <PhSpinner v-if="isUpdatingLlmSettings" :size="16" class="animate-spin" />
        <span>{{ isUpdatingLlmSettings ? '保存中...' : '保存配置' }}</span>
      </button>
      <button
        @click="handleTestLlm"
        :disabled="isTestingLlm || isUpdatingLlmSettings"
        class="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-emerald-500 hover:bg-emerald-600 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <PhFlask :size="16" />
        <span>{{ isTestingLlm ? '测试中...' : '测试连接' }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
input[type="range"]::-webkit-slider-thumb {
  appearance: none;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #3b82f6;
  cursor: pointer;
}

input[type="range"]::-moz-range-thumb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #3b82f6;
  cursor: pointer;
  border: none;
}
</style>
