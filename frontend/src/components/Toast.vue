<script setup lang="ts">
import { computed, ref } from 'vue'
import { PhCheckCircle, PhXCircle, PhWarning, PhInfo } from '@phosphor-icons/vue'
import { useToast } from '../composables/useToast'

export interface ToastProps {
  id: string
  message: string
  type: 'success' | 'error' | 'warning' | 'info'
  duration?: number
}

const props = defineProps<ToastProps>()

const emit = defineEmits<{
  close: [id: string]
}>()

const { pauseToast, resumeToast } = useToast()

// 图标映射
const icons = {
  success: PhCheckCircle,
  error: PhXCircle,
  warning: PhWarning,
  info: PhInfo
}

// 图标和文字颜色样式
const typeStyles = {
  success: 'text-emerald-600',
  error: 'text-red-600',
  warning: 'text-amber-600',
  info: 'text-blue-600'
}

// 图标背景样式
const iconBgStyles = {
  success: 'bg-emerald-100',
  error: 'bg-red-100',
  warning: 'bg-amber-100',
  info: 'bg-blue-100'
}

const isHovered = ref(false)

function handleClick() {
  emit('close', props.id)
}

function handleMouseEnter() {
  isHovered.value = true
  pauseToast(props.id)
}

function handleMouseLeave() {
  isHovered.value = false
  resumeToast(props.id)
}

const IconComponent = computed(() => icons[props.type])
const textColorClass = computed(() => typeStyles[props.type])
const iconBgClass = computed(() => iconBgStyles[props.type])
</script>

<template>
  <div
    @click="handleClick"
    @mouseenter="handleMouseEnter"
    @mouseleave="handleMouseLeave"
    class="cursor-pointer rounded-2xl shadow-lg border border-gray-100 bg-white min-w-[280px] max-w-md overflow-hidden will-change-transform will-change-opacity transition-all duration-200"
    :class="isHovered ? 'scale-105 shadow-2xl -translate-y-1' : 'scale-100'"
  >
    <div class="flex items-center gap-3 px-4 py-3">
      <div class="shrink-0 w-8 h-8 rounded-full flex items-center justify-center" :class="iconBgClass">
        <component :is="IconComponent" :size="18" weight="fill" :class="textColorClass" />
      </div>
      <p class="flex-1 text-sm font-medium leading-snug text-slate-800">{{ message }}</p>
    </div>
  </div>
</template>
