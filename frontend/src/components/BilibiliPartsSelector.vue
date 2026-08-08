<script setup lang="ts">
import { ref, computed } from 'vue'
import { PhX, PhVideo, PhSpinner } from '@phosphor-icons/vue'
import type { BilibiliVideoInfo } from '../types'

const props = defineProps<{
  isOpen: boolean
  videoInfo: BilibiliVideoInfo | null
  isLoading: boolean
}>()

const emit = defineEmits<{
  close: []
  confirm: [config: { mode: 'merge' | 'separate'; indices: number[] }]
}>()

const selectedIndices = ref<Set<number>>(new Set())
const processingMode = ref<'merge' | 'separate'>('merge')

// 计算属性
const formattedDuration = (seconds: number): string => {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }
  return `${minutes}:${String(secs).padStart(2, '0')}`
}

const totalSelectedDuration = computed(() => {
  if (!props.videoInfo?.parts) return 0
  return props.videoInfo.parts
    .filter(p => selectedIndices.value.has(p.index))
    .reduce((sum, p) => sum + p.duration, 0)
})

const formattedTotalDuration = computed(() => formattedDuration(totalSelectedDuration.value))

const canConfirm = computed(() => selectedIndices.value.size > 0)

// 方法
const togglePart = (index: number) => {
  if (selectedIndices.value.has(index)) {
    selectedIndices.value.delete(index)
  } else {
    selectedIndices.value.add(index)
  }
}

const selectAll = () => {
  if (!props.videoInfo?.parts) return
  props.videoInfo.parts.forEach(p => selectedIndices.value.add(p.index))
}

const deselectAll = () => {
  selectedIndices.value.clear()
}

const handleConfirm = () => {
  if (!canConfirm.value) return
  emit('confirm', {
    mode: processingMode.value,
    indices: Array.from(selectedIndices.value).sort((a, b) => a - b),
  })
}

const handleClose = () => {
  emit('close')
}

// 重置状态当打开时
const resetState = () => {
  selectedIndices.value.clear()
  processingMode.value = 'merge'
  // 默认全选
  if (props.videoInfo?.parts) {
    props.videoInfo.parts.forEach(p => selectedIndices.value.add(p.index))
  }
}

// 监听打开状态重置
import { watch } from 'vue'
watch(() => props.isOpen, (newVal) => {
  if (newVal) {
    resetState()
  }
})
</script>

<template>
  <Teleport to="body">
    <transition name="modal">
      <div
        v-if="isOpen"
        class="fixed inset-0 z-[70] bg-black/50 backdrop-blur-sm flex items-center justify-center p-4"
        @click.self="handleClose"
      >
        <div
          class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden"
          @click.stop
        >
          <!-- 头部 -->
          <div class="flex items-center justify-between px-6 py-4 border-b border-slate-200">
            <div class="flex items-center gap-3">
              <PhVideo :size="24" class="text-blue-500" />
              <h2 class="text-lg font-semibold text-slate-800">选择要处理的分P</h2>
            </div>
            <button
              @click="handleClose"
              class="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
            >
              <PhX :size="20" />
            </button>
          </div>

          <!-- 加载状态 -->
          <div v-if="isLoading" class="flex-1 flex items-center justify-center py-12">
            <div class="flex flex-col items-center gap-3">
              <PhSpinner :size="32" class="text-blue-500 animate-spin" />
              <p class="text-sm text-slate-500">正在获取视频信息...</p>
            </div>
          </div>

          <!-- 内容 -->
          <div v-else-if="videoInfo" class="flex-1 overflow-y-auto px-6 py-4 custom-scrollbar">
            <!-- 视频标题 -->
            <div class="mb-4">
              <h3 class="text-base font-medium text-slate-800 line-clamp-2">{{ videoInfo.title }}</h3>
              <p class="text-sm text-slate-500 mt-1">BV{{ videoInfo.bvid }} · {{ videoInfo.parts?.length || 1 }}个分P</p>
            </div>

            <!-- 快捷操作 -->
            <div class="flex items-center gap-2 mb-3">
              <button
                @click="selectAll"
                class="px-3 py-1.5 text-xs font-medium text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
              >
                全选
              </button>
              <button
                @click="deselectAll"
                class="px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
              >
                取消全选
              </button>
              <div class="flex-1"></div>
              <span class="text-sm text-slate-500">
                已选 {{ selectedIndices.size }} 个 · 共 {{ formattedTotalDuration }}
              </span>
            </div>

            <!-- 分P列表 -->
            <div class="space-y-2 max-h-[40vh] overflow-y-auto custom-scrollbar pr-2">
              <div
                v-for="part in videoInfo.parts"
                :key="part.index"
                @click="togglePart(part.index)"
                :class="[
                  'flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-all',
                  selectedIndices.has(part.index)
                    ? 'border-blue-500 bg-blue-50/50'
                    : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
                ]"
              >
                <div
                  :class="[
                    'flex-shrink-0 w-5 h-5 rounded border-2 flex items-center justify-center mt-0.5 transition-colors',
                    selectedIndices.has(part.index)
                      ? 'border-blue-500 bg-blue-500'
                      : 'border-slate-300 bg-white'
                  ]"
                >
                  <svg
                    v-if="selectedIndices.has(part.index)"
                    class="w-3 h-3 text-white"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2">
                    <span class="text-sm font-medium text-slate-800">P{{ part.index + 1 }}</span>
                    <span class="text-xs text-slate-400">{{ formattedDuration(part.duration) }}</span>
                  </div>
                  <p class="text-sm text-slate-600 mt-0.5 line-clamp-2">{{ part.title }}</p>
                </div>
              </div>
            </div>

            <!-- 处理方式 -->
            <div class="mt-6 pt-4 border-t border-slate-200">
              <h4 class="text-sm font-medium text-slate-700 mb-3">处理方式</h4>
              <div class="space-y-2">
                <label
                  :class="[
                    'flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-all',
                    processingMode === 'merge'
                      ? 'border-blue-500 bg-blue-50/50'
                      : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
                  ]"
                >
                  <input
                    type="radio"
                    v-model="processingMode"
                    value="merge"
                    class="sr-only"
                  />
                  <div
                    :class="[
                      'flex-shrink-0 w-4 h-4 rounded-full border-2 flex items-center justify-center mt-0.5 transition-colors',
                      processingMode === 'merge'
                        ? 'border-blue-500 bg-blue-500'
                        : 'border-slate-300 bg-white'
                    ]"
                  >
                    <div
                      v-if="processingMode === 'merge'"
                      class="w-1.5 h-1.5 rounded-full bg-white"
                    ></div>
                  </div>
                  <div>
                    <div class="text-sm font-medium text-slate-800">合并为一个任务</div>
                    <div class="text-xs text-slate-500 mt-0.5">一个任务，按分P顺序处理并生成分P总结和总体概览</div>
                  </div>
                </label>
                <label
                  :class="[
                    'flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-all',
                    processingMode === 'separate'
                      ? 'border-blue-500 bg-blue-50/50'
                      : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
                  ]"
                >
                  <input
                    type="radio"
                    v-model="processingMode"
                    value="separate"
                    class="sr-only"
                  />
                  <div
                    :class="[
                      'flex-shrink-0 w-4 h-4 rounded-full border-2 flex items-center justify-center mt-0.5 transition-colors',
                      processingMode === 'separate'
                        ? 'border-blue-500 bg-blue-500'
                        : 'border-slate-300 bg-white'
                    ]"
                  >
                    <div
                      v-if="processingMode === 'separate'"
                      class="w-1.5 h-1.5 rounded-full bg-white"
                    ></div>
                  </div>
                  <div>
                    <div class="text-sm font-medium text-slate-800">拆分为多个任务</div>
                    <div class="text-xs text-slate-500 mt-0.5">每个分P独立处理，生成各自的转录和总结</div>
                  </div>
                </label>
              </div>
            </div>
          </div>

          <!-- 底部按钮 -->
          <div class="flex items-center justify-end gap-3 px-6 py-4 border-t border-slate-200 bg-slate-50/50">
            <button
              @click="handleClose"
              class="px-4 py-2 text-sm font-medium text-slate-600 hover:bg-white hover:text-slate-800 rounded-lg transition-colors"
            >
              取消
            </button>
            <button
              @click="handleConfirm"
              :disabled="!canConfirm || isLoading"
              class="px-4 py-2 text-sm font-medium text-white bg-blue-500 hover:bg-blue-600 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              开始处理
            </button>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
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

.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-active .bg-white,
.modal-leave-active .bg-white {
  transition: transform 0.2s ease;
}

.modal-enter-from .bg-white,
.modal-leave-to .bg-white {
  transform: scale(0.95);
}
</style>
