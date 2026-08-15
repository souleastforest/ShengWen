<script setup lang="ts">
/**
 * Markdown 渲染容器（从 TaskContentArea.vue 拆出，行为逐行等价）
 *
 * 职责：渲染 compiledMarkdown（v-html）并承载：
 * - 标题收集/滚动观察（slug 去重、IntersectionObserver 驱动 activeHeadingId）
 * - 搜索关键词高亮（summaryHighlightRequest → mark.summary-search-highlight）
 * - Mermaid 命令式渲染（MermaidBlock 宿主）
 * - XSS 渲染层 DEV 兜底断言
 *
 * 契约面：props task / compiledMarkdown / activeTab / summaryHighlightRequest /
 * headingJumpRequest / scrollContainer；emits open-mermaid-viewer /
 * update-markdown-headings / update-active-heading-id。
 */
import { ref, watch, nextTick, onBeforeUnmount } from 'vue'
import { configureMarkdownRenderer } from '../useMarkdownCompile'
import type { Task, MarkdownHeadingItem } from '../../../types'
import { useMarkdownTheme } from '../../../composables/useMarkdownTheme'
import MermaidBlock from './MermaidBlock.vue'

interface SummaryHighlightRequest {
  taskId: string
  keyword: string
  source: 'topic' | 'summary'
  requestId: number
}

const props = defineProps<{
  task: Task
  compiledMarkdown: string
  activeTab: 'summary' | 'transcript'
  summaryHighlightRequest?: SummaryHighlightRequest | null
  headingJumpRequest?: { id: string; requestId: number } | null
  /** 滚动容器 getter（TaskContentArea 的 contentScrollRef 惰性取值），标题观察与跳转的根 */
  scrollContainer: (() => HTMLElement | null) | null
}>()

const emit = defineEmits<{
  'open-mermaid-viewer': [target: HTMLElement]
  'update-markdown-headings': [headings: MarkdownHeadingItem[]]
  'update-active-heading-id': [headingId: string]
}>()

// 与 App.vue 共用同一 marked renderer 配置（幂等，模块级单次）
configureMarkdownRenderer()

// 使用主题管理 Hook（仅用于状态追踪，样式通过 CSS 自动应用）
const { currentThemeId } = useMarkdownTheme()

const summaryArticleRef = ref<HTMLElement | null>(null)
const SUMMARY_HIGHLIGHT_CLASS = 'summary-search-highlight'
const markdownHeadings = ref<MarkdownHeadingItem[]>([])
const activeHeadingId = ref('')
let headingObserver: IntersectionObserver | null = null
const headingElementMap = new Map<string, HTMLElement>()

const cleanupHeadingObserver = () => {
  if (headingObserver) {
    headingObserver.disconnect()
    headingObserver = null
  }
}

const clearMarkdownHeadings = () => {
  cleanupHeadingObserver()
  markdownHeadings.value = []
  activeHeadingId.value = ''
  headingElementMap.clear()
  emit('update-markdown-headings', [])
  emit('update-active-heading-id', '')
}

const slugifyHeading = (text: string) => {
  return text
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '-')
    .replace(/[^\w\u4e00-\u9fa5-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
}

const setupHeadingObserver = () => {
  cleanupHeadingObserver()
  const container = props.scrollContainer?.() ?? null
  if (!container || headingElementMap.size === 0) return

  const observedHeadings = Array.from(headingElementMap.values())
  headingObserver = new IntersectionObserver(
    (entries) => {
      const visibleEntries = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)

      const firstVisible = visibleEntries[0]
      if (firstVisible) {
        activeHeadingId.value = (firstVisible.target as HTMLElement).id
        return
      }

      const containerTop = container.getBoundingClientRect().top + 96
      let nearestId = ''
      let nearestOffset = Number.POSITIVE_INFINITY
      observedHeadings.forEach((element) => {
        const offset = Math.abs(element.getBoundingClientRect().top - containerTop)
        if (offset < nearestOffset) {
          nearestId = element.id
          nearestOffset = offset
        }
      })
      if (nearestId) {
        activeHeadingId.value = nearestId
      }
    },
    {
      root: container,
      rootMargin: '-96px 0px -60% 0px',
      threshold: [0, 1],
    },
  )

  observedHeadings.forEach((element) => headingObserver?.observe(element))
}

const collectMarkdownHeadings = async () => {
  await nextTick()
  const contentRoot = summaryArticleRef.value?.querySelector('[data-summary-content]') as HTMLElement | null
  if (!contentRoot) {
    clearMarkdownHeadings()
    return
  }

  const headingElements = Array.from(contentRoot.querySelectorAll('h1, h2, h3, h4')) as HTMLElement[]
  if (!headingElements.length) {
    clearMarkdownHeadings()
    return
  }

  const slugCounter = new Map<string, number>()
  const collected: MarkdownHeadingItem[] = []
  headingElementMap.clear()

  headingElements.forEach((element, index) => {
    const text = (element.textContent || '').trim()
    if (!text) return

    const level = Number(element.tagName.replace('H', '')) || 2
    const baseSlug = slugifyHeading(text) || `section-${index + 1}`
    const currentCount = slugCounter.get(baseSlug) || 0
    slugCounter.set(baseSlug, currentCount + 1)
    const uniqueSlug = currentCount === 0 ? baseSlug : `${baseSlug}-${currentCount + 1}`

    element.id = uniqueSlug
    collected.push({
      id: uniqueSlug,
      text,
      level,
    })
    headingElementMap.set(uniqueSlug, element)
  })

  markdownHeadings.value = collected
  activeHeadingId.value = collected[0]?.id || ''
  emit('update-markdown-headings', collected)
  emit('update-active-heading-id', activeHeadingId.value)
  setupHeadingObserver()
}

const jumpToHeading = (headingId: string) => {
  const container = props.scrollContainer?.() ?? null
  const target = headingElementMap.get(headingId)
  if (!container || !target) return

  const containerRect = container.getBoundingClientRect()
  const targetRect = target.getBoundingClientRect()
  const offsetTop = targetRect.top - containerRect.top + container.scrollTop - 88

  container.scrollTo({
    top: Math.max(0, offsetTop),
    behavior: 'smooth',
  })
  // 不立即更新 activeHeadingId，让 IntersectionObserver 自然更新
}

const clearSummaryHighlight = () => {
  if (!summaryArticleRef.value) return
  const marks = summaryArticleRef.value.querySelectorAll(`mark.${SUMMARY_HIGHLIGHT_CLASS}`)
  marks.forEach((mark) => {
    const parent = mark.parentNode
    if (!parent) return
    parent.replaceChild(document.createTextNode(mark.textContent || ''), mark)
    parent.normalize()
  })
}

const findAndHighlightInSummary = (root: HTMLElement, keyword: string): HTMLElement | null => {
  const trimmedKeyword = keyword.trim()
  if (!trimmedKeyword) return null
  const lowerKeyword = trimmedKeyword.toLowerCase()

  const walker = document.createTreeWalker(
    root,
    NodeFilter.SHOW_TEXT,
    {
      acceptNode: (node: Node) => {
        const text = node.textContent || ''
        if (!text.trim()) return NodeFilter.FILTER_REJECT
        const parent = (node as Text).parentElement
        if (!parent) return NodeFilter.FILTER_REJECT
        if (parent.closest(`mark.${SUMMARY_HIGHLIGHT_CLASS}`)) return NodeFilter.FILTER_REJECT
        if (parent.closest('pre, code, .mermaid, svg, script, style')) return NodeFilter.FILTER_REJECT
        return NodeFilter.FILTER_ACCEPT
      },
    },
  )

  let currentNode = walker.nextNode() as Text | null
  while (currentNode) {
    const lowerText = currentNode.data.toLowerCase()
    const matchIndex = lowerText.indexOf(lowerKeyword)
    if (matchIndex >= 0) {
      const matchedTextNode = currentNode.splitText(matchIndex)
      matchedTextNode.splitText(trimmedKeyword.length)

      const mark = document.createElement('mark')
      mark.className = SUMMARY_HIGHLIGHT_CLASS
      mark.textContent = matchedTextNode.data
      matchedTextNode.parentNode?.replaceChild(mark, matchedTextNode)
      return mark
    }
    currentNode = walker.nextNode() as Text | null
  }

  return null
}

const applySummaryHighlight = async () => {
  const request = props.summaryHighlightRequest
  if (!request || request.taskId !== props.task.id || props.activeTab !== 'summary') {
    clearSummaryHighlight()
    return
  }

  const keyword = request.keyword.trim()
  if (!keyword) {
    clearSummaryHighlight()
    return
  }

  await nextTick()
  const contentRoot = summaryArticleRef.value?.querySelector('[data-summary-content]') as HTMLElement | null
  if (!contentRoot) return

  clearSummaryHighlight()
  const highlighted = findAndHighlightInSummary(contentRoot, keyword)
  if (highlighted) {
    highlighted.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

// XSS 防线说明（渲染层兜底断言）：compiledMarkdown 已在 useMarkdownCompile
// 管线出口（marked 编译后、postProcess 前）经 DOMPurify.sanitize() 单点净化
// ——marked 本身不做净化。此处 v-html 只渲染管线产物，任何代码不得绕过该出口
// 直接给 compiledMarkdown 赋值；DEV 下若检测到未净化痕迹则告警
// （生产构建 import.meta.env.DEV 为 false，会被摇树）。
// 监听内容变化并渲染 Mermaid + 收集标题
watch([() => props.compiledMarkdown, () => props.activeTab], async () => {
  if (
    import.meta.env.DEV
    && props.compiledMarkdown
    && /<script|onerror=|onload=|javascript:/i.test(props.compiledMarkdown)
  ) {
    console.error('[XSS 防线] compiledMarkdown 疑似未净化（管线出口被绕过？）', props.compiledMarkdown)
  }
  if (props.activeTab === 'summary' && props.compiledMarkdown) {
    await nextTick()
    await collectMarkdownHeadings()
    return
  }

  clearMarkdownHeadings()
}, { immediate: true })

watch(
  [
    () => props.summaryHighlightRequest?.requestId,
    () => props.summaryHighlightRequest?.taskId,
    () => props.summaryHighlightRequest?.keyword,
    () => props.task.id,
    () => props.compiledMarkdown,
    () => props.activeTab,
  ],
  async () => {
    await applySummaryHighlight()
  },
  { immediate: true },
)

watch(
  () => activeHeadingId.value,
  (headingId) => {
    emit('update-active-heading-id', headingId)
  },
)

watch(
  () => props.headingJumpRequest?.requestId,
  () => {
    if (props.activeTab !== 'summary') return
    const headingId = props.headingJumpRequest?.id
    if (!headingId) return
    jumpToHeading(headingId)
  },
)

// 卸载时清理观察器与标题状态
onBeforeUnmount(() => {
  clearMarkdownHeadings()
})
</script>

<template>
  <article
    ref="summaryArticleRef"
    class="prose prose-sm md:prose-base prose-slate prose-headings:font-bold prose-a:text-blue-600 hover:prose-a:underline prose-img:rounded-xl max-w-none px-8 py-8 ss-shared-prose markdown-theme-container"
    :data-theme="currentThemeId"
  >
    <MermaidBlock
      :task-id="task.id"
      :render-key="compiledMarkdown"
      :enabled="activeTab === 'summary'"
      @open-viewer="emit('open-mermaid-viewer', $event)"
    >
      <div v-if="task.summary && compiledMarkdown" data-summary-content v-html="compiledMarkdown"></div>
      <p v-else-if="task.summary" class="text-slate-400 italic">正在加载总结预览...</p>
      <p v-else class="text-slate-400 italic">暂无总结内容</p>
    </MermaidBlock>
  </article>
</template>

<style>
.summary-search-highlight {
  background: #fde68a;
  color: inherit;
  border-radius: 4px;
  padding: 0 0.1em;
}
</style>
