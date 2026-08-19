<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { PhListBullets, PhCaretDown } from '@phosphor-icons/vue'
import type { MarkdownHeadingItem } from '../types'

const props = defineProps<{
  headings: MarkdownHeadingItem[]
  activeHeadingId?: string
}>()

const emit = defineEmits<{
  jump: [headingId: string]
}>()

const hasHeadings = computed(() => props.headings.length > 0)
const isWideScreen = ref(false)
const isNarrowPanelOpen = ref(false)
const isWidePanelCollapsed = ref(false)
const WIDE_SCREEN_QUERY = '(min-width: 1024px)'

let mediaQueryList: MediaQueryList | null = null
let detachMediaListener: (() => void) | null = null

const updateScreenMode = () => {
  if (!mediaQueryList) return
  const nextWide = mediaQueryList.matches
  if (nextWide !== isWideScreen.value) {
    isWideScreen.value = nextWide
    if (nextWide) {
      isWidePanelCollapsed.value = false
      isNarrowPanelOpen.value = false
    } else {
      isNarrowPanelOpen.value = false
      isWidePanelCollapsed.value = false
    }
  }
}

const isPanelVisible = computed(() => {
  if (!hasHeadings.value) return false
  if (isWideScreen.value) return !isWidePanelCollapsed.value
  return isNarrowPanelOpen.value
})

const togglePanel = () => {
  if (!hasHeadings.value) return
  if (isWideScreen.value) {
    isWidePanelCollapsed.value = !isWidePanelCollapsed.value
    return
  }
  isNarrowPanelOpen.value = !isNarrowPanelOpen.value
}

const handleJump = (headingId: string) => {
  emit('jump', headingId)
  if (!isWideScreen.value) {
    isNarrowPanelOpen.value = false
  }
}

onMounted(() => {
  if (typeof window === 'undefined') return
  mediaQueryList = window.matchMedia(WIDE_SCREEN_QUERY)
  isWideScreen.value = mediaQueryList.matches
  if (isWideScreen.value) {
    isWidePanelCollapsed.value = false
  }

  const onChange = () => updateScreenMode()
  if (typeof mediaQueryList.addEventListener === 'function') {
    mediaQueryList.addEventListener('change', onChange)
    detachMediaListener = () => {
      mediaQueryList?.removeEventListener('change', onChange)
    }
  } else {
    mediaQueryList.addListener(onChange)
    detachMediaListener = () => {
      mediaQueryList?.removeListener(onChange)
    }
  }
})

onBeforeUnmount(() => {
  if (detachMediaListener) {
    detachMediaListener()
    detachMediaListener = null
  }
})

// 层级缩进：更明显的视觉层次
const resolveIndent = (level: number) => {
  const clampedLevel = Math.min(Math.max(level, 1), 6)
  const indents = [12, 24, 40, 60, 80, 100]
  return `${indents[clampedLevel - 1]}px`
}

// 字体样式：根据层级变化
const getHeadingStyle = (level: number) => {
  const clampedLevel = Math.min(Math.max(level, 1), 6)
  const styles = [
    { size: 'text-[0.9375rem]', weight: 'font-semibold', color: 'text-slate-900' },
    { size: 'text-[0.875rem]', weight: 'font-semibold', color: 'text-slate-800' },
    { size: 'text-[0.875rem]', weight: 'font-medium', color: 'text-slate-700' },
    { size: 'text-[0.8125rem]', weight: 'font-normal', color: 'text-slate-600' },
    { size: 'text-[0.8125rem]', weight: 'font-normal', color: 'text-slate-500' },
    { size: 'text-xs', weight: 'font-normal', color: 'text-slate-400' }
  ]
  const style = styles[clampedLevel - 1]!
  return `${style.size} ${style.weight} ${style.color}`
}
</script>

<template>
  <div class="relative">
    <!-- 触发按钮 -->
    <button
      type="button"
      class="px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-200 flex items-center gap-1.5 whitespace-nowrap"
      :class="hasHeadings
        ? (isPanelVisible ? 'text-slate-800 bg-slate-100 shadow-sm' : 'text-slate-600 hover:text-slate-800 hover:bg-slate-100')
        : 'text-slate-300 cursor-not-allowed'"
      :disabled="!hasHeadings"
      title="章节导航"
      @click="togglePanel"
    >
      <PhListBullets :size="14" />
      <span>章节</span>
      <PhCaretDown :size="11" class="transition-transform duration-200" :class="isPanelVisible ? 'rotate-180' : ''" />
    </button>

    <!-- 下拉面板 -->
    <div
      v-if="hasHeadings"
      class="absolute right-0 top-full mt-3.5 md:left-full md:top-0 md:ml-2 w-80 max-w-[calc(100vw-2rem)] overflow-y-auto bg-white border border-slate-200/60 rounded-2xl transition-all duration-200 z-10 py-3"
      :class="isPanelVisible ? 'opacity-100 visible translate-y-0' : 'opacity-0 invisible -translate-y-1 pointer-events-none'"
      :style="{ maxHeight: 'calc(100vh - 120px)', boxShadow: isPanelVisible ? '0 4px 12px rgba(0, 0, 0, 0.08)' : 'none' }"
    >
      <!-- 章节列表 -->
      <button
        v-for="heading in headings"
        :key="heading.id"
        type="button"
        class="block w-full text-left py-2.5 pr-4 transition-all duration-150 border-l-2 rounded-r-xl"
        :class="[
          getHeadingStyle(heading.level),
          heading.id === activeHeadingId
            ? 'border-blue-500 bg-blue-50/70 !text-blue-700 !font-semibold'
            : 'border-transparent hover:bg-slate-50/80 hover:border-slate-300'
        ]"
        :style="{ paddingLeft: resolveIndent(heading.level) }"
        :title="heading.text"
        @click="handleJump(heading.id)"
      >
        <span class="block whitespace-normal break-words leading-relaxed">{{ heading.text }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
/* 自定义滚动条样式 */
.absolute.right-0::-webkit-scrollbar {
  width: 4px;
}

.absolute.right-0::-webkit-scrollbar-track {
  background: transparent;
}

.absolute.right-0::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 2px;
}

.absolute.right-0::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}
</style>
