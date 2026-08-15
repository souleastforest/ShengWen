/**
 * Markdown 编译管线（从 App.vue 下沉，行为逐行等价）
 *
 * 职责：
 * 1. marked 自定义 renderer 配置（mermaid 类名输出），单次全局配置
 * 2. 编译管线出口：stripDoubleBracePlaceholders → marked.parse →
 *    DOMPurify 单点净化（FORBID_ATTR: ['style'] + USE_PROFILES: { html: true }）
 *    → postProcessCompiledMarkdown（时间芯片/代码类名）
 * 3. 多P 总结分页状态机（useMultipartSummary 本质）：overview / 按页加载 /
 *    任务切换重置 / 身份守卫展开
 *
 * 供 App.vue（编排）与 MarkdownContent.vue（渲染容器）共用。
 */
import { computed, ref, watch, type Ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { Task } from '../../types'
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

/** 多P 总结每页分 P 数（与拆片前 App.vue 常量一致） */
const MULTIPART_PAGE_SIZE = 10

export function useMarkdownCompile(options: {
  selectedTask: Readonly<Ref<Task | null>>
  fetchTaskFullContent: (taskId: string) => Promise<unknown>
}) {
  const { selectedTask, fetchTaskFullContent } = options
  configureMarkdownRenderer()

  // Defer large summary compilation so the multipart preview stays interactive.
  const compiledMarkdown = ref('')
  const showFullMultipartSummary = ref(false)
  const multipartPage = ref(0)
  let markdownCompileTimer: ReturnType<typeof setTimeout> | null = null
  let markdownCompileGeneration = 0

  const getMultipartOverview = (summary: string) => {
    const marker = summary.search(/^#\s*分P总结.*$/m)
    if (marker > 0) return summary.slice(0, marker).trim()
    return summary.slice(0, 12000).trim()
  }

  const getMultipartPages = (summary: string) => {
    const marker = summary.search(/^#\s*分P总结.*$/m)
    if (marker < 0) return [summary]
    const body = summary.slice(marker)
    const matches = Array.from(body.matchAll(/^##\s+P\d+[:：].*$/gm))
    if (!matches.length) return [body.trim()]
    const sections = matches.map((match, index) => {
      const start = match.index ?? 0
      const nextMatch = matches[index + 1]
      const end = nextMatch?.index ?? body.length
      return body.slice(start, end).trim()
    })
    const pages: string[] = []
    for (let index = 0; index < sections.length; index += MULTIPART_PAGE_SIZE) {
      pages.push(`# 分P总结\\n\\n${sections.slice(index, index + MULTIPART_PAGE_SIZE).join('\\n\\n')}`)
    }
    return pages
  }

  const multipartPageCount = computed(() => {
    const summary = selectedTask.value?.summary
    if (!summary || !selectedTask.value?.has_parts) return 0
    return getMultipartPages(summary).length
  })

  const scheduleMarkdownCompile = () => {
    markdownCompileGeneration += 1
    const generation = markdownCompileGeneration
    if (markdownCompileTimer) {
      clearTimeout(markdownCompileTimer)
      markdownCompileTimer = null
    }

    compiledMarkdown.value = ''
    const task = selectedTask.value
    if (!task?.summary) return

    markdownCompileTimer = setTimeout(() => {
      markdownCompileTimer = null
      if (generation !== markdownCompileGeneration || selectedTask.value?.id !== task.id) return

      const summary = task.summary
      if (!summary) return
      let previewSummary = summary
      if (task.has_parts) {
        if (!showFullMultipartSummary.value) {
          previewSummary = getMultipartOverview(summary)
        } else {
          const pages = getMultipartPages(summary)
          previewSummary = pages[multipartPage.value] || pages[0] || ''
        }
      }
      compiledMarkdown.value = compileMarkdownText(previewSummary, {
        videoUrl: task.video_url || '',
      })
    }, 120)
  }

  watch(
    [() => selectedTask.value?.id, () => selectedTask.value?.summary, showFullMultipartSummary, multipartPage],
    scheduleMarkdownCompile,
    { immediate: true },
  )

  watch(
    () => selectedTask.value?.id,
    () => {
      showFullMultipartSummary.value = false
      multipartPage.value = 0
    },
  )

  const expandMultipartSummary = async () => {
    const task = selectedTask.value
    if (!task) return
    multipartPage.value = 0
    try {
      await fetchTaskFullContent(task.id)
    } catch (error) {
      console.error('Failed to load full multipart summary:', error)
      return
    }
    // 等待期间用户可能已切换任务：任务身份重校验（与 copyContent/downloadContent
    // 的 selectedTask.id 守卫模式一致），不得展开新任务的完整分P总结
    if (!selectedTask.value || selectedTask.value.id !== task.id) return
    showFullMultipartSummary.value = true
  }

  const collapseMultipartSummary = () => {
    showFullMultipartSummary.value = false
    multipartPage.value = 0
  }

  const changeMultipartPage = (page: number) => {
    multipartPage.value = Math.max(0, Math.min(page, Math.max(0, multipartPageCount.value - 1)))
  }

  return {
    compiledMarkdown,
    showFullMultipartSummary,
    multipartPage,
    multipartPageCount,
    expandMultipartSummary,
    collapseMultipartSummary,
    changeMultipartPage,
  }
}
