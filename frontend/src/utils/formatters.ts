/** 时长输出风格：clock=冒号时钟（MM:SS / H:MM:SS），chinese=中文分秒（X分XX秒 / X秒）。默认 clock */
export type DurationFormatStyle = 'clock' | 'chinese'
/**
 * 秒数取整：round=四舍五入（默认），floor=向下取整（B 站时长源旧行为）。
 * floor 风格假设输入为整数秒（后端 duration 强制 int()）：对浮点输入按整数截断，
 * 不复现旧实现的浮点域 FP 噪声输出（如 '59:59.6'）——此为 P3 有意修正，勿"顺手修浮点"。
 */
export type DurationRounding = 'round' | 'floor'

export interface FormatDurationOptions {
  format?: DurationFormatStyle
  rounding?: DurationRounding
  /** 无效输入（0/负数/NaN/undefined）占位文本，默认 '--'；chinese 风格忽略（固定 '0秒'） */
  placeholder?: string
  /** clock 风格下小时/无小时分支的分钟是否补零（默认 true）；B 站旧样式不补（'1:05'），传 false 保持 */
  pad?: boolean
}

/**
 * 时长格式化（P3 收敛：三处旧实现合一，options 保持各调用点输出逐字符不变）：
 * - 默认 clock：TaskMetaCard / 导出图（旧 utils/formatters.ts：round、全补零、无效 '--'）；
 * - { format: 'chinese' }：TaskPartsPanel（round、无效 '0秒'、分钟不设小时档 → 3600 = '60分00秒'）；
 * - { rounding: 'floor', pad: false, placeholder: '0:00' }：BilibiliPartsSelector
 *   （floor、不补小时/分钟位、无效 '0:00'）。floor 风格假设输入为整数秒（后端 duration
 *   强制 int()），浮点输入按整数截断——旧实现的 '59:59.6' 属 FP 噪声缺陷，P3 已修正。
 */
export const formatDuration = (seconds?: number, options: FormatDurationOptions = {}): string => {
  const { format = 'clock', rounding = 'round', placeholder = '--', pad = true } = options
  const numeric = Number(seconds)
  const valid = seconds !== undefined && seconds !== null && !Number.isNaN(numeric) && numeric > 0
  if (!valid) {
    return format === 'chinese' ? '0秒' : placeholder
  }
  const total = rounding === 'floor' ? Math.floor(numeric) : Math.round(numeric)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (format === 'chinese') {
    const minutes = Math.floor(total / 60)
    const remaining = total % 60
    return minutes ? `${minutes}分${String(remaining).padStart(2, '0')}秒` : `${remaining}秒`
  }
  const pad2 = (n: number) => String(n).padStart(2, '0')
  if (h > 0) return `${pad ? pad2(h) : h}:${pad2(m)}:${pad2(s)}`
  return `${pad ? pad2(m) : m}:${pad2(s)}`
}

/** 文件大小人性化显示（B/KB/MB/GB 四档）。GB 档与 LocalFolderSelector 旧实现一致。 */
export const formatFileSize = (bytes: number): string => {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB'
}

export const formatTranscriptionDuration = (seconds?: number) => {
  if (!seconds || Number.isNaN(seconds) || seconds <= 0) return '--'
  return `${seconds.toFixed(1)}s`
}

export const formatConversionRatio = (audioSeconds?: number, transcriptionSeconds?: number) => {
  if (
    !audioSeconds
    || Number.isNaN(audioSeconds)
    || audioSeconds <= 0
    || !transcriptionSeconds
    || Number.isNaN(transcriptionSeconds)
    || transcriptionSeconds <= 0
  ) {
    return '--'
  }
  return (audioSeconds / transcriptionSeconds).toFixed(2)
}

export const formatDateTime = (date: string | Date) => {
  const d = typeof date === 'string' ? new Date(date) : date
  return d.toLocaleString([], {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export const countWords = (markdown: string) => {
  if (!markdown) return 0
  const plain = markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`[^`]*`/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]+\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[>#*_~\-|]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  const cjkCount = (plain.match(/[\u4E00-\u9FFF]/g) || []).length
  const latinWords = (plain.replace(/[\u4E00-\u9FFF]/g, ' ').match(/[A-Za-z0-9]+/g) || []).length
  return cjkCount + latinWords
}

export const stripDoubleBracePlaceholders = (text: string) => {
  if (!text) return ''
  return text
    .replace(/\{\{[\s\S]*?\}\}/g, '')
    .replace(/<p>\s*<\/p>/g, '')
    .trim()
}
