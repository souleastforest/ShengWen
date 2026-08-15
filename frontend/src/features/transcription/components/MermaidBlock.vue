<script setup lang="ts">
/**
 * Mermaid 命令式 DOM 渲染封装（从 TaskContentArea.vue 拆出，行为逐行等价）。
 *
 * 职责：扫描宿主容器内的 `.mermaid` 节点（marked 自定义 renderer 产物），
 * 替换为带工具栏的渲染块（下载 SVG / 复制代码 / 查看代码 / 预览）。
 * 生命周期：
 * - 初始化/更新：renderKey（compiledMarkdown）或 enabled 变化 → nextTick 后
 *   重新扫描渲染；renderVersion 防竞态（渲染在途时内容变更 → 陈旧渲染丢弃）
 * - 清理：内容由 v-html 整体替换，本组件无全局注册资源需要释放
 *
 * 契约面：props taskId / renderKey / enabled；emit open-viewer。
 * 宿主通过 slot 传递 markdown 内容；宿主 div 使用 display:contents 不产生
 * 布局影响（保持拆片前后 DOM 视觉结构一致）。
 */
import { ref, watch } from 'vue'
import { nextTick } from 'vue'
import { normalizeAccidentalInlineCodeBlocks } from '../../../utils/markdownNormalizer'
import { normalizeMermaidSvgLayout } from '../../../utils/mermaidLayout'
import { getMermaid } from '../../../utils/mermaidLoader'

const props = defineProps<{
  taskId: string
  /** 内容变更信号（compiledMarkdown），变化即触发重新渲染 */
  renderKey: string
  /** 是否启用（activeTab === 'summary' 时才渲染） */
  enabled: boolean
}>()

const emit = defineEmits<{
  'open-viewer': [target: HTMLElement]
}>()

const rootRef = ref<HTMLElement | null>(null)
const mermaidRenderVersion = ref(0)

const MERMAID_ICON_DOWNLOAD = 'M224,144v64a8,8,0,0,1-8,8H40a8,8,0,0,1-8-8V144a8,8,0,0,1,16,0v56H208V144a8,8,0,0,1,16,0Zm-101.66,5.66a8,8,0,0,0,11.32,0l40-40a8,8,0,0,0-11.32-11.32L136,124.69V32a8,8,0,0,0-16,0v92.69L93.66,98.34a8,8,0,0,0-11.32,11.32Z'
const MERMAID_ICON_COPY = 'M216,32H88a8,8,0,0,0-8,8V80H40a8,8,0,0,0-8,8V216a8,8,0,0,0,8,8H168a8,8,0,0,0,8-8V176h40a8,8,0,0,0,8-8V40A8,8,0,0,0,216,32ZM160,208H48V96H160Zm48-48H176V88a8,8,0,0,0-8-8H96V48H208Z'
const MERMAID_ICON_CODE = 'M69.12,94.15,28.5,128l40.62,33.85a8,8,0,1,1-10.24,12.29l-48-40a8,8,0,0,1,0-12.29l48-40a8,8,0,0,1,10.24,12.3Zm176,27.7-48-40a8,8,0,1,0-10.24,12.3L227.5,128l-40.62,33.85a8,8,0,1,0,10.24,12.29l48-40a8,8,0,0,0,0-12.29ZM162.73,32.48a8,8,0,0,0-10.25,4.79l-64,176a8,8,0,0,0,4.79,10.26A8.14,8.14,0,0,0,96,224a8,8,0,0,0,7.52-5.27l64-176A8,8,0,0,0,162.73,32.48Z'
const MERMAID_ICON_PREVIEW = 'M216,48V96a8,8,0,0,1-16,0V67.31l-42.34,42.35a8,8,0,0,1-11.32-11.32L188.69,56H160a8,8,0,0,1,0-16h48A8,8,0,0,1,216,48ZM98.34,146.34,56,188.69V160a8,8,0,0,0-16,0v48a8,8,0,0,0,8,8H96a8,8,0,0,0,0-16H67.31l42.35-42.34a8,8,0,0,0-11.32-11.32ZM208,152a8,8,0,0,0-8,8v28.69l-42.34-42.35a8,8,0,0,0-11.32,11.32L188.69,200H160a8,8,0,0,0,0,16h48a8,8,0,0,0,8-8V160A8,8,0,0,0,208,152ZM67.31,56H96a8,8,0,0,0,0-16H48a8,8,0,0,0-8,8V96a8,8,0,0,0,16,0V67.31l42.34,42.35a8,8,0,0,0,11.32-11.32Z'

const createMermaidToolButton = (title: string, iconPath: string) => {
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'ss-mermaid-tool-btn'
  button.title = title
  button.setAttribute('aria-label', title)

  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  icon.setAttribute('viewBox', '0 0 256 256')
  icon.setAttribute('aria-hidden', 'true')
  icon.classList.add('ss-mermaid-tool-icon')
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  path.setAttribute('d', iconPath)
  icon.appendChild(path)
  button.appendChild(icon)

  return button
}

const copyTextToClipboard = async (text: string): Promise<boolean> => {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Fallback to execCommand below.
  }

  try {
    const textArea = document.createElement('textarea')
    textArea.value = text
    textArea.setAttribute('readonly', '')
    textArea.style.position = 'fixed'
    textArea.style.opacity = '0'
    textArea.style.pointerEvents = 'none'
    document.body.appendChild(textArea)
    textArea.select()
    const copied = document.execCommand('copy')
    textArea.remove()
    return copied
  } catch {
    return false
  }
}

const renderMermaidBlocks = async () => {
  const renderVersion = ++mermaidRenderVersion.value
  const contentRoot = rootRef.value?.querySelector('[data-summary-content]') as HTMLElement | null
  if (!contentRoot) return

  normalizeAccidentalInlineCodeBlocks(contentRoot)

  const mermaidNodes = Array.from(contentRoot.querySelectorAll('.mermaid')) as HTMLElement[]
  if (!mermaidNodes.length) return

  for (const [index, sourceNode] of mermaidNodes.entries()) {
    if (renderVersion !== mermaidRenderVersion.value) return

    const code = (sourceNode.textContent || '').trim()
    const block = document.createElement('div')
    block.className = 'ss-mermaid-block'

    const diagramWrap = document.createElement('div')
    diagramWrap.className = 'ss-mermaid-diagram-wrap'

    const toolbar = document.createElement('div')
    toolbar.className = 'ss-mermaid-toolbar'

    const downloadSvgButton = createMermaidToolButton('下载 SVG', MERMAID_ICON_DOWNLOAD)
    downloadSvgButton.disabled = true

    const copyCodeButton = createMermaidToolButton('复制代码', MERMAID_ICON_COPY)
    copyCodeButton.disabled = !code

    const toggleCodeButton = createMermaidToolButton('查看代码', MERMAID_ICON_CODE)

    const previewButton = createMermaidToolButton('预览', MERMAID_ICON_PREVIEW)
    previewButton.disabled = true

    const renderHost = document.createElement('div')
    renderHost.className = 'ss-mermaid-render'

    const codePanel = document.createElement('pre')
    codePanel.className = 'ss-mermaid-code-panel'
    codePanel.hidden = true
    const codeElement = document.createElement('code')
    codeElement.className = 'language-mermaid'
    codeElement.textContent = code
    codePanel.appendChild(codeElement)

    toolbar.appendChild(downloadSvgButton)
    toolbar.appendChild(copyCodeButton)
    toolbar.appendChild(toggleCodeButton)
    toolbar.appendChild(previewButton)
    diagramWrap.appendChild(toolbar)
    diagramWrap.appendChild(renderHost)
    block.appendChild(diagramWrap)
    block.appendChild(codePanel)

    sourceNode.replaceWith(block)

    const toggleCode = () => {
      const isHidden = codePanel.hidden
      codePanel.hidden = !isHidden
      toggleCodeButton.classList.toggle('active', isHidden)
    }
    toggleCodeButton.addEventListener('click', toggleCode)

    downloadSvgButton.addEventListener('click', () => {
      const svg = renderHost.querySelector('svg')
      if (!svg) return

      const svgClone = svg.cloneNode(true) as SVGElement
      if (!svgClone.getAttribute('xmlns')) {
        svgClone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
      }
      if (!svgClone.getAttribute('xmlns:xlink')) {
        svgClone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
      }

      const serialized = new XMLSerializer().serializeToString(svgClone)
      const blob = new Blob([serialized], { type: 'image/svg+xml;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `mermaid-${props.taskId}-${index + 1}.svg`
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 0)
    })

    copyCodeButton.addEventListener('click', async () => {
      if (!code) return
      const copied = await copyTextToClipboard(code)
      copyCodeButton.classList.toggle('active', copied)
      copyCodeButton.title = copied ? '已复制代码' : '复制失败'
      setTimeout(() => {
        copyCodeButton.classList.remove('active')
        copyCodeButton.title = '复制代码'
      }, 1200)
    })

    previewButton.addEventListener('click', () => {
      if (!renderHost.querySelector('svg')) return
      emit('open-viewer', renderHost)
    })

    try {
      if (!code) {
        throw new Error('未检测到 Mermaid 代码块内容。')
      }
      const mermaid = await getMermaid()
      const renderId = `ss-mermaid-${props.taskId}-${renderVersion}-${index}`
      const result = await mermaid.render(renderId, code)
      if (renderVersion !== mermaidRenderVersion.value) return

      renderHost.innerHTML = result.svg
      result.bindFunctions?.(renderHost)
      normalizeMermaidSvgLayout(renderHost)
      requestAnimationFrame(() => normalizeMermaidSvgLayout(renderHost))
      downloadSvgButton.disabled = false
      previewButton.disabled = false
    } catch (error) {
      if (renderVersion !== mermaidRenderVersion.value) return
      // 渲染失败时直接移除整个 mermaid 块，不显示错误信息
      block.remove()
    }
  }
}

watch(
  [() => props.renderKey, () => props.enabled],
  async () => {
    if (!props.enabled || !props.renderKey) return
    await nextTick()
    try {
      await renderMermaidBlocks()
    } catch (e) {
      console.error('Mermaid render failed:', e)
    }
  },
  { immediate: true },
)
</script>

<template>
  <!-- display:contents 宿主：不产生布局影响，仅提供命令式 DOM 挂载点 -->
  <div ref="rootRef" class="ss-mermaid-block-host" style="display: contents">
    <slot />
  </div>
</template>

<style>
/* ========== Mermaid 渲染容器样式 ========== */
.ss-mermaid-block {
  margin: 1.5rem 0;
  max-width: 100%;
  min-width: 0;
}

.ss-mermaid-diagram-wrap {
  position: relative;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 1rem;
  transition: border-color 0.2s, box-shadow 0.2s;
  max-width: 100%;
  overflow: visible;
}

.ss-mermaid-diagram-wrap:hover {
  border-color: #94a3b8;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}

.ss-mermaid-toolbar {
  position: absolute;
  top: 0.55rem;
  right: 0.55rem;
  z-index: 2;
  display: flex;
  gap: 0.4rem;
  opacity: 0;
  pointer-events: none;
  transform: translateY(-2px);
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.ss-mermaid-diagram-wrap:hover .ss-mermaid-toolbar,
.ss-mermaid-diagram-wrap:focus-within .ss-mermaid-toolbar {
  opacity: 1;
  pointer-events: auto;
  transform: translateY(0);
}

.ss-mermaid-tool-btn {
  border: 1px solid #cbd5e1;
  background: rgba(255, 255, 255, 0.9);
  color: #334155;
  border-radius: 999px;
  width: 1.95rem;
  height: 1.95rem;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background-color 0.15s, border-color 0.15s, color 0.15s;
}

.ss-mermaid-tool-icon {
  width: 0.95rem;
  height: 0.95rem;
  fill: currentColor;
}

.ss-mermaid-tool-btn:hover {
  background: #eff6ff;
  border-color: #60a5fa;
  color: #1d4ed8;
}

.ss-mermaid-tool-btn.active {
  background: #dbeafe;
  border-color: #3b82f6;
  color: #1d4ed8;
}

.ss-mermaid-tool-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.ss-mermaid-render {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 140px;
  max-width: 100%;
  min-width: 0;
  overflow: auto;
}

.ss-mermaid-render svg {
  max-width: 100%;
  height: auto;
  display: block;
  margin-left: auto;
  margin-right: auto;
  flex-shrink: 0;
}

.ss-mermaid-code-panel {
  margin-top: 0.55rem;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 0.8rem;
  overflow-x: auto;
}

.ss-mermaid-code-panel code {
  color: #334155;
  font-size: 0.8rem;
}

/* 渲染失败降级展示样式（C 阶段 backlog 待接入） */
.ss-mermaid-error {
  width: 100%;
  max-width: 100%;
  border: 1px solid #fca5a5;
  border-radius: 8px;
  background: #fff1f2;
  color: #991b1b;
  padding: 0.8rem;
  overflow: hidden;
  box-sizing: border-box;
}

.ss-mermaid-error-title {
  font-size: 0.82rem;
  font-weight: 700;
  margin-bottom: 0.45rem;
  color: #7f1d1d;
}

.ss-mermaid-error-detail {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  font-size: 0.76rem;
  line-height: 1.4;
  background: transparent;
  border: 0;
  padding: 0;
  color: #991b1b;
  font-weight: 500;
  max-width: 100%;
}
</style>
