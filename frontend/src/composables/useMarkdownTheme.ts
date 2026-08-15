/**
 * Markdown 主题管理 Hook
 *
 * 功能：
 * 1. 主题切换和持久化
 * 2. CSS 变量动态应用
 * 3. 自定义主题导入/导出
 * 4. 全局主题状态管理
 */

import { ref, computed, watch } from 'vue'
import {
  MARKDOWN_THEMES,
  DEFAULT_THEME,
  type MarkdownTheme,
  type MarkdownThemeId
} from '../styles/markdown-themes/themes'

// 导出类型供其他模块使用
export type { MarkdownTheme, MarkdownThemeId }

const STORAGE_KEY = 'ShengWen:markdown-theme'

// 全局主题状态（单例模式，确保整个应用共享同一状态）
const currentThemeId = ref<MarkdownThemeId>(DEFAULT_THEME)

// 从 localStorage 加载主题
const loadTheme = (): MarkdownThemeId => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved && saved in MARKDOWN_THEMES) {
      return saved as MarkdownThemeId
    }
  } catch (e) {
    console.warn('[useMarkdownTheme] Failed to load theme from localStorage:', e)
  }
  return DEFAULT_THEME
}

// 保存主题到 localStorage
const saveTheme = (themeId: MarkdownThemeId) => {
  try {
    localStorage.setItem(STORAGE_KEY, themeId)
  } catch (e) {
    console.warn('[useMarkdownTheme] Failed to save theme to localStorage:', e)
  }
}

/**
 * 主题管理 Hook
 *
 * @example
 * ```ts
 * const {
 *   currentThemeId,
 *   currentTheme,
 *   themeList,
 *   setTheme,
 *   importTheme,
 *   exportTheme
 * } = useMarkdownTheme()
 * ```
 */
export function useMarkdownTheme() {
  // 初始化：从 localStorage 加载主题
  if (currentThemeId.value === DEFAULT_THEME) {
    currentThemeId.value = loadTheme()
  }

  // 当前主题对象（computed，自动响应主题变化）
  const currentTheme = computed(() => MARKDOWN_THEMES[currentThemeId.value])

  // 所有主题列表（用于渲染选择器）
  const themeList = computed(() => Object.values(MARKDOWN_THEMES))

  /**
   * 应用主题的 CSS 变量到 DOM
   * @param theme - 要应用的主题对象
   */
  const applyTheme = (theme: MarkdownTheme) => {
    const root = document.documentElement

    // 批量设置 CSS 变量
    Object.entries(theme.cssVariables).forEach(([key, value]) => {
      root.style.setProperty(key, value)
    })
  }

  /**
   * 切换主题
   * @param themeId - 主题 ID
   */
  const setTheme = (themeId: MarkdownThemeId) => {
    if (!(themeId in MARKDOWN_THEMES)) {
      console.warn(`[useMarkdownTheme] Theme "${themeId}" not found`)
      return
    }

    currentThemeId.value = themeId
    const theme = MARKDOWN_THEMES[themeId]

    // 确保主题存在（类型守卫）
    if (!theme) {
      console.warn(`[useMarkdownTheme] Theme "${themeId}" is undefined`)
      return
    }

    // 应用主题到 DOM
    applyTheme(theme)

    // 持久化到 localStorage
    saveTheme(themeId)
  }

  /**
   * 导入自定义主题
   * @param cssContent - CSS 代码内容
   * @param themeName - 主题名称
   * @returns 新导入的主题 ID
   */
  const importTheme = (cssContent: string, themeName?: string): MarkdownThemeId => {
    const customId = `custom-${Date.now()}` as MarkdownThemeId

    // 解析 CSS 提取变量
    const cssVariables: Record<string, string> = {}

    // 匹配 CSS 变量的正则表达式：支持 --md-xxx: value; 格式
    const variableRegex = /(--md-[\w-]+):\s*([^;]+);/g
    let match
    while ((match = variableRegex.exec(cssContent)) !== null) {
      const varName = match[1] // 第一个捕获组：变量名
      const varValue = match[2] // 第二个捕获组：值
      if (varName && varValue) {
        cssVariables[varName] = varValue
      }
    }

    // 检查是否提取到变量
    if (Object.keys(cssVariables).length === 0) {
      throw new Error('未找到有效的 CSS 变量，请确保格式为 --md-xxx: value;')
    }

    // 创建新主题
    const newTheme: MarkdownTheme = {
      id: customId,
      name: themeName || '自定义主题',
      description: '用户导入的自定义主题',
      cssVariables,
    }
    MARKDOWN_THEMES[customId] = newTheme

    // 自动切换到新主题
    setTheme(customId)

    return customId
  }

  /**
   * 导出当前主题为 CSS 文件
   * @param themeId - 要导出的主题 ID（默认为当前主题）
   * @returns CSS 字符串
   */
  const exportTheme = (themeId?: MarkdownThemeId): string => {
    const targetTheme = themeId
      ? MARKDOWN_THEMES[themeId]
      : currentTheme.value

    if (!targetTheme) {
      throw new Error('主题不存在')
    }

    const variables = Object.entries(targetTheme.cssVariables)
      .map(([key, value]) => `  ${key}: ${value};`)
      .join('\n')

    const css = `/* ${targetTheme.name} */\n/* ${targetTheme.description} */\n\n.markdown-theme-container {\n${variables}\n}`

    return css
  }

  /**
   * 重置为默认主题
   */
  const resetTheme = () => {
    setTheme(DEFAULT_THEME)
  }

  // 监听主题变化，自动应用（确保在其他地方修改 currentThemeId 时也能生效）
  watch(currentThemeId, (newThemeId) => {
    const theme = MARKDOWN_THEMES[newThemeId]
    if (theme) {
      applyTheme(theme)
    }
  })

  // 初始化时应用当前主题
  if (typeof document !== 'undefined') {
    const initialTheme = currentTheme.value
    if (initialTheme) {
      applyTheme(initialTheme)
    }
  }

  return {
    // 状态
    currentThemeId,
    currentTheme,
    themeList,

    // 方法
    setTheme,
    applyTheme,
    importTheme,
    exportTheme,
    resetTheme,
  }
}

/**
 * 获取当前主题（简化版，用于非 Vue 组件中）
 */
export function getCurrentTheme(): MarkdownTheme {
  const themeId = loadTheme()
  const theme = MARKDOWN_THEMES[themeId]
  // 使用非空断言，因为 DEFAULT_THEME 总是存在的
  return theme ?? MARKDOWN_THEMES[DEFAULT_THEME]!
}

/**
 * 获取主题（带安全检查）
 */
export function getThemeById(themeId: MarkdownThemeId): MarkdownTheme | undefined {
  return MARKDOWN_THEMES[themeId]
}

