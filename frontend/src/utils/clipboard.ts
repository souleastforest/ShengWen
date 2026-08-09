// 复制文本到剪贴板：优先现代 Clipboard API（需安全上下文 HTTPS/localhost），
// 失败或不可用时降级到传统 textarea + execCommand（兼容局域网 HTTP）。
export const copyText = async (text: string): Promise<boolean> => {
  if (!text) return false

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch (err) {
    console.error('Clipboard API failed, falling back:', err)
  }

  try {
    return fallbackCopyToClipboard(text)
  } catch (err) {
    console.error('Fallback copy failed:', err)
    return false
  }
}

// 传统复制方法（兼容非安全上下文，如局域网 HTTP）
const fallbackCopyToClipboard = (text: string): boolean => {
  const textarea = document.createElement('textarea')
  textarea.value = text

  // 设置样式使其不可见但仍可操作
  textarea.style.position = 'fixed'
  textarea.style.top = '0'
  textarea.style.left = '0'
  textarea.style.width = '2em'
  textarea.style.height = '2em'
  textarea.style.padding = '0'
  textarea.style.border = 'none'
  textarea.style.outline = 'none'
  textarea.style.boxShadow = 'none'
  textarea.style.background = 'transparent'
  textarea.style.opacity = '0'

  document.body.appendChild(textarea)

  try {
    // 选中文本
    textarea.focus()
    textarea.select()
    textarea.setSelectionRange(0, text.length)

    // 执行复制命令
    return document.execCommand('copy')
  } finally {
    // 清理 DOM
    document.body.removeChild(textarea)
  }
}
