/**
 * Markdown 编译管线（从 App.vue 下沉，行为逐行等价）
 *
 * 职责：
 * 1. marked 自定义 renderer 配置（mermaid 类名输出），单次全局配置
 * 2. 编译管线出口：stripDoubleBracePlaceholders → marked.parse →
 *    DOMPurify 单点净化（FORBID_ATTR: ['style'] + USE_PROFILES: { html: true }）
 *    → postProcessCompiledMarkdown（时间芯片/代码类名）
 * 3. 多P 总结一P一页分页状态机：总览段（常驻）+ 当前分P页（随 multipartPage
 *    变化）+ 任务切换重置 + generation 守卫（防串台）
 *
 * 供 App.vue（编排）与 MarkdownContent.vue（渲染容器）共用。
 */
import { computed, ref, watch, type Ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { Task, TaskPart } from '../../types'
import { stripDoubleBracePlaceholders } from '../../utils/formatters'
import { postProcessCompiledMarkdown } from '../../utils/markdownPostProcessor'

let rendererConfigured = false

/** 配置 marked renderer 以支持 mermaid 类名（模块级单次，幂等） */
export function configureMarkdownRenderer() {
  if (rendererConfigured) return
  rendererConfigured = true
  const renderer = new marked.Renderer()
  renderer.code = ({ text, lang }) => {
    if (lang === 'mermaid') {
      return `<pre class="mermaid">${text}</pre>`
    }
    return `<pre><code class="language-${lang}">${text}</code></pre>`
  }
  marked.setOptions({ renderer })
}

/**
 * 编译管线出口（纯函数）：marked 不做净化，LLM 内容（含原始 HTML）经
 * marked 编译后立即 DOMPurify 白名单净化；postProcess 只追加可信 DOM
 * （类名/时间芯片），不会重新引入未净化内容。渲染层（v-html）只允许消费
 * 本函数/本 composable 的产物，禁止绕过该出口直接赋值。
 * 配置：FORBID_ATTR: ['style']（默认配置不过滤 style，LLM 输出可携带
 * 追踪/遮罩 CSS）；USE_PROFILES: { html: true }（剔除 svg/mathML 面，
 * 本管线不需要；Mermaid SVG 走 DOM API 不经此出口）。
 */
export function compileMarkdownText(summary: string, options?: { videoUrl?: string }): string {
  configureMarkdownRenderer()
  const cleanedSummary = stripDoubleBracePlaceholders(summary)
  const html = DOMPurify.sanitize(marked.parse(cleanedSummary) as string, {
    FORBID_ATTR: ['style'],
    USE_PROFILES: { html: true },
  })
  return postProcessCompiledMarkdown(html, { videoUrl: options?.videoUrl || '' })
}

/**
 * 分P 处理中状态集合（与 features/task/state.ts 的 PROCESSING_STATUSES 对齐）：
 * 占位文案区分"正在处理中"与"暂无可展示内容"。
 */
export const PART_PROCESSING_STATUSES = new Set([
  'PENDING',
  'DOWNLOADING',
  'UPLOADING',
  'TRANSCRIBING',
  'SUMMARIZING',
])

export function useMarkdownCompile(options: {
  selectedTask: Readonly<Ref<Task | null>>
  /** 分P 列表：task.has_parts 时页数 = parts 数（一P一页） */
  parts: Readonly<Ref<TaskPart[]>>
  /** 分P 详情缓存（App 由 fetchTaskPart 填充）；当前页 summary 优先取 partDetails，回退 parts */
  partDetails: Readonly<Ref<Record<number, TaskPart>>>
}) {
  const { selectedTask, parts, partDetails } = options
  configureMarkdownRenderer()

  // 总览段与当前分P页分开编译：翻页只重编当前页，
  // 总览（章节导航/高亮/mermaid 锚点来源）常驻不受影响。
  const overviewCompiledMarkdown = ref('')
  const pageCompiledMarkdown = ref('')
  const multipartPage = ref(0)
  let markdownCompileTimer: ReturnType<typeof setTimeout> | null = null
  let markdownCompileGeneration = 0

  /**
   * 总览段三段式提取（真实数据契约，主行 summary 存在两种分P格式）：
   * ① 有一级 "# 分P总结" 标记 → 标记前部分；
   * ② 无一级标记、但分P段落以 "## Pn：" 二级标题直接跟在总览后
   *    （任务 0f13aa14 实测格式：总览正文后紧跟 "## P1：..." 段落）→
   *    首个 "## Pn：" 之前；
   * ③ 都没有 → 整个 summary。
   * 禁止任何 slice 截断（slice(0, 12000) 是旧缺陷，多P 总览可能整段丢失）。
   */
  const getMultipartOverview = (summary: string) => {
    const marker = summary.search(/^#\s*分P总结.*$/m)
    if (marker >= 0) return summary.slice(0, marker).trim()
    const partMarker = summary.search(/^##\s+P\d+[:：]/m)
    if (partMarker >= 0) return summary.slice(0, partMarker).trim()
    return summary.trim()
  }

  const multipartPageCount = computed(() => {
    if (!selectedTask.value?.has_parts) return 0
    return parts.value.length
  })

  /** 当前分P页解析出的分P（partDetails 优先，回退 parts 列表行），供占位状态判断 */
  const multipartPagePart = computed(() => {
    const page = multipartPage.value
    return partDetails.value[page] || parts.value[page] || null
  })

  const scheduleMarkdownCompile = () => {
    markdownCompileGeneration += 1
    const generation = markdownCompileGeneration
    if (markdownCompileTimer) {
      clearTimeout(markdownCompileTimer)
      markdownCompileTimer = null
    }

    overviewCompiledMarkdown.value = ''
    pageCompiledMarkdown.value = ''
    const task = selectedTask.value
    if (!task?.summary) return

    markdownCompileTimer = setTimeout(() => {
      markdownCompileTimer = null
      if (generation !== markdownCompileGeneration || selectedTask.value?.id !== task.id) return

      const summary = task.summary
      if (!summary) return

      overviewCompiledMarkdown.value = compileMarkdownText(
        task.has_parts ? getMultipartOverview(summary) : summary,
        { videoUrl: task.video_url || '' },
      )

      if (task.has_parts && parts.value.length > 0) {
        const page = Math.min(multipartPage.value, parts.value.length - 1)
        const part = partDetails.value[page] || parts.value[page]
        const partSummary = part?.summary
        if (partSummary) {
          pageCompiledMarkdown.value = compileMarkdownText(partSummary, {
            videoUrl: task.video_url || '',
          })
        }
      }
    }, 120)
  }

  watch(
    [
      () => selectedTask.value?.id,
      () => selectedTask.value?.summary,
      () => parts.value,
      () => partDetails.value,
      multipartPage,
    ],
    scheduleMarkdownCompile,
    { immediate: true },
  )

  watch(
    () => selectedTask.value?.id,
    () => {
      multipartPage.value = 0
    },
  )

  const changeMultipartPage = (page: number) => {
    multipartPage.value = Math.max(0, Math.min(page, Math.max(0, multipartPageCount.value - 1)))
  }

  return {
    overviewCompiledMarkdown,
    pageCompiledMarkdown,
    multipartPage,
    multipartPageCount,
    multipartPagePart,
    changeMultipartPage,
  }
}
