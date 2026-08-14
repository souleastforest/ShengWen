/**
 * formatters 统一测试（P3 收敛）
 *
 * 背景：formatFileSize 此前 Sidebar 无 GB 档（≥1GB 落到 MB 档显示 '1024.0 MB'），
 * LocalFolderSelector 有 GB 档（'1.00 GB'）——收敛为 utils/formatters.ts 单一实现（含 GB 档）。
 * formatDuration 此前三份实现：formatters.ts 时钟式（round、全补零、无效 '--'）、
 * TaskPartsPanel 中文分秒式（round、无效 '0秒'、分钟不设小时档）、
 * BilibiliPartsSelector 时钟式（floor、不补小时/分钟位）——收敛为单一实现 + 最小 options。
 *
 * 验收（行为保持）：
 * 1. formatFileSize 档位边界（0 / 1B / 1KB / 1MB / 1023MB / 1GB / 2GB）正确；
 *    与旧 Sidebar 实现（无 GB 档）在 <1GB 区间逐值相等；≥1GB 为 plan 明确的预期修复（GB 显示）。
 * 2. formatDuration 三种调用方式（clock 默认 / chinese / floor+不补零）与各自旧实现逐值相等。
 */
import { describe, expect, it } from 'vitest'
import { formatDuration, formatFileSize } from '../utils/formatters'

// ---- 对比基准：P3 前旧实现（行为等价验证的基准快照） ----
// 旧 Sidebar.vue:258-262（无 GB 档）
const oldSidebarFormatFileSize = (bytes: number): string => {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}
// 旧 LocalFolderSelector.vue:21-26（含 GB 档）
const oldLocalFolderFormatFileSize = (bytes: number): string => {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB'
}
// 旧 utils/formatters.ts:1-10（clock：round、全补零、无效 '--'）
const oldClockDuration = (seconds?: number): string => {
  if (!seconds || Number.isNaN(seconds) || seconds <= 0) return '--'
  const total = Math.round(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  if (h > 0) return `${pad(h)}:${pad(m)}:${pad(s)}`
  return `${pad(m)}:${pad(s)}`
}
// 旧 TaskPartsPanel.vue:73-80（chinese：round、无效 '0秒'、分钟不设小时档）
const oldChineseDuration = (seconds?: number): string => {
  const value = Math.max(0, Math.round(Number(seconds || 0)))
  const minutes = Math.floor(value / 60)
  const remaining = value % 60
  return minutes ? minutes + '分' + String(remaining).padStart(2, '0') + '秒' : remaining + '秒'
}
// 旧 BilibiliPartsSelector.vue:21-29（clock：floor、不补小时/分钟位、无效 '0:00'）
const oldBilibiliDuration = (seconds: number): string => {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }
  return `${minutes}:${String(secs).padStart(2, '0')}`
}

const MB = 1024 * 1024
const GB = 1024 * 1024 * 1024

describe('formatFileSize：档位边界', () => {
  it('B/KB/MB/GB 四档边界正确', () => {
    expect(formatFileSize(0)).toBe('0 B')
    expect(formatFileSize(1)).toBe('1 B')
    expect(formatFileSize(1023)).toBe('1023 B')
    expect(formatFileSize(1024)).toBe('1.0 KB')
    expect(formatFileSize(1024 * 1024 - 1)).toBe('1024.0 KB')
    expect(formatFileSize(MB)).toBe('1.0 MB')
    expect(formatFileSize(1023 * MB)).toBe('1023.0 MB')
    expect(formatFileSize(GB)).toBe('1.00 GB')
    expect(formatFileSize(2 * GB)).toBe('2.00 GB')
    expect(formatFileSize(3.5 * GB)).toBe('3.50 GB')
  })

  it('行为等价：<1GB 区间与旧 Sidebar 实现（无 GB 档）逐值相等', () => {
    const inputs = [0, 1, 500, 1023, 1024, 4096, MB - 1, MB, 100 * MB, 1023 * MB, 1023.999 * MB]
    for (const bytes of inputs) {
      expect(formatFileSize(bytes)).toBe(oldSidebarFormatFileSize(bytes))
    }
  })

  it('行为等价：全档位与旧 LocalFolderSelector 实现逐值相等', () => {
    const inputs = [0, 1, 1023, 1024, MB, 1023 * MB, GB, 2 * GB, 3.5 * GB]
    for (const bytes of inputs) {
      expect(formatFileSize(bytes)).toBe(oldLocalFolderFormatFileSize(bytes))
    }
  })

  it('预期修复（plan 明确）：≥1GB 显示正确 GB 档而非 MB 错档', () => {
    expect(formatFileSize(GB)).not.toBe('1024.0 MB')
    expect(formatFileSize(2 * GB)).not.toBe('2048.0 MB')
    expect(formatFileSize(2 * GB)).toMatch(/ GB$/)
  })
})

describe('formatDuration：clock（默认，旧 utils/formatters 语义）', () => {
  it('无效输入 → "--"（与旧实现一致）', () => {
    for (const input of [undefined, 0, -5, Number.NaN] as const) {
      expect(formatDuration(input)).toBe('--')
    }
  })

  it('输出与旧 utils/formatters 实现逐值相等', () => {
    const inputs = [0.4, 59.6, 60, 61, 65, 3599, 3600, 3661, 7325.9]
    for (const seconds of inputs) {
      expect(formatDuration(seconds)).toBe(oldClockDuration(seconds))
    }
  })

  it('关键输出快照：round 取整、全补零、H:MM:SS', () => {
    expect(formatDuration(0.4)).toBe('00:00')
    expect(formatDuration(59.6)).toBe('01:00')
    expect(formatDuration(65)).toBe('01:05')
    expect(formatDuration(3599)).toBe('59:59')
    expect(formatDuration(3600)).toBe('01:00:00')
    expect(formatDuration(3661)).toBe('01:01:01')
  })
})

describe('formatDuration：chinese（旧 TaskPartsPanel 语义）', () => {
  it('无效输入 → "0秒"（与旧实现一致）', () => {
    for (const input of [undefined, 0, -5] as const) {
      expect(formatDuration(input, { format: 'chinese' })).toBe('0秒')
    }
  })

  it('输出与旧 TaskPartsPanel 实现逐值相等', () => {
    const inputs = [0.4, 59.6, 60, 61, 65, 3599, 3600, 3661, 7325.9]
    for (const seconds of inputs) {
      expect(formatDuration(seconds, { format: 'chinese' })).toBe(oldChineseDuration(seconds))
    }
  })

  it('关键输出快照：分钟不设小时档（3600 → "60分00秒"）', () => {
    expect(formatDuration(59.6, { format: 'chinese' })).toBe('1分00秒')
    expect(formatDuration(65, { format: 'chinese' })).toBe('1分05秒')
    expect(formatDuration(3599, { format: 'chinese' })).toBe('59分59秒')
    expect(formatDuration(3600, { format: 'chinese' })).toBe('60分00秒')
    expect(formatDuration(7325.9, { format: 'chinese' })).toBe('122分06秒')
  })
})

describe('formatDuration：B 站风格（floor + 不补小时/分钟位 + 无效 "0:00"，旧 BilibiliPartsSelector 语义）', () => {
  // B 站时长源为后端 int() 整数秒；旧实现无无效值守卫（0/NaN 直接格式化）。
  // placeholder: '0:00' 保持旧实现对该输入的输出。
  const biliStyle = (seconds?: number) =>
    formatDuration(seconds, { rounding: 'floor', pad: false, placeholder: '0:00' })

  it('输出与旧 BilibiliPartsSelector 实现逐值相等（整数秒输入）', () => {
    const inputs = [0, 59, 60, 61, 65, 3599, 3600, 3661, 7326]
    for (const seconds of inputs) {
      expect(biliStyle(seconds)).toBe(oldBilibiliDuration(seconds))
    }
  })

  it('关键输出快照：分钟/小时不补零、无效 "0:00"', () => {
    expect(biliStyle(0)).toBe('0:00')
    expect(biliStyle(65)).toBe('1:05')
    expect(biliStyle(3599)).toBe('59:59')
    expect(biliStyle(3600)).toBe('1:00:00')
    expect(biliStyle(3661)).toBe('1:01:01')
  })
})

describe('formatDuration：三处调用点输出互异且各自保持', () => {
  it('同一输入（65s）三种调用方式输出逐字符保持各自旧值', () => {
    expect(formatDuration(65)).toBe('01:05') // TaskMetaCard / 导出图（旧 formatters.ts）
    expect(formatDuration(65, { format: 'chinese' })).toBe('1分05秒') // TaskPartsPanel
    expect(formatDuration(65, { rounding: 'floor', pad: false, placeholder: '0:00' })).toBe('1:05') // BilibiliPartsSelector
  })
})
