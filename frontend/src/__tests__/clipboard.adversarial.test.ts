/**
 * 对抗性测试：utils/clipboard.ts copyText 的完整语义
 *
 * 需求：复制优先 navigator.clipboard.writeText，失败降级 textarea+execCommand。
 * 对抗点：
 * 1. 复制内容必须是**完整原 URL**——超长 URL 的 UI truncate 不得影响复制内容；
 *    B 站带 query 追踪参数（?spm_id_from=...&vd_source=...）必须原样复制；
 * 2. clipboard.writeText 抛错 → 降级 execCommand 仍成功时返回 true（"可复制"成立）；
 * 3. execCommand 失败/抛错 → 返回 false（组件据此不显示"已复制"反馈）；
 * 4. navigator.clipboard 缺失（如 HTTP 局域网环境）→ 降级路径可用；
 * 5. 降级后 textarea 必须被清理（无 DOM 泄漏）；
 * 6. 空串直接返回 false，不触发任何复制调用。
 * 每个用例失败 = 实现缺陷。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { copyText } from '../utils/clipboard'

const BILI_TRACKING_URL =
  'https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333.999.0.0&vd_source=abc123def456'

const LONG_URL =
  'https://example.com/very/long/path/' +
  'segment-one/segment-two/segment-three/segment-four/' +
  'segment-five/segment-six/segment-seven?q=标题%20%E7%89%B9%E6%AE%8A%E5%AD%97%E7%AC%A6&x=1'

describe('对抗性：clipboard.copyText 完整语义', () => {
  let writeTextMock: ReturnType<typeof vi.fn>
  let execCommandSpy: ReturnType<typeof vi.fn>
  const originalNavigator = globalThis.navigator

  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    writeTextMock = vi.fn().mockResolvedValue(undefined)
    execCommandSpy = vi.fn().mockReturnValue(true)
    Object.defineProperty(document, 'execCommand', {
      value: execCommandSpy,
      configurable: true,
      writable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    Object.defineProperty(globalThis, 'navigator', {
      value: originalNavigator,
      configurable: true,
    })
    // 降级路径不得遗留 textarea
    document.body.innerHTML = ''
  })

  it('clipboard 可用：writeText 收到完整原 URL（含 B 站追踪参数与特殊字符）', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: writeTextMock } })

    const ok = await copyText(BILI_TRACKING_URL)
    expect(ok).toBe(true)
    expect(writeTextMock).toHaveBeenCalledTimes(1)
    expect(writeTextMock).toHaveBeenCalledWith(BILI_TRACKING_URL)
    expect(execCommandSpy).not.toHaveBeenCalled()
  })

  it('超长 URL：writeText 收到未截断的完整文本（truncate 只影响展示）', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: writeTextMock } })

    const ok = await copyText(LONG_URL)
    expect(ok).toBe(true)
    expect(writeTextMock).toHaveBeenCalledWith(LONG_URL)
  })

  it('clipboard.writeText 抛错 → 降级 execCommand；execCommand 成功 → 返回 true', async () => {
    vi.stubGlobal('navigator', {
      clipboard: { writeText: writeTextMock.mockRejectedValue(new Error('denied')) },
    })

    // 在 execCommand 被调用时捕获降级 textarea 的内容（此时 textarea 尚在 DOM）
    let textareaValueAtCopy: string | null = null
    execCommandSpy.mockImplementation((cmd: string) => {
      if (cmd === 'copy') {
        textareaValueAtCopy = document.querySelector('textarea')?.value ?? null
      }
      return true
    })

    const ok = await copyText(BILI_TRACKING_URL)
    expect(ok).toBe(true)
    expect(execCommandSpy).toHaveBeenCalledWith('copy')
    // 降级 textarea 的 value 必须是完整 URL（不被截断）
    expect(textareaValueAtCopy).toBe(BILI_TRACKING_URL)
    // 复制完成后 textarea 已从 DOM 移除（无泄漏）
    expect(document.querySelectorAll('textarea')).toHaveLength(0)
  })

  it('navigator.clipboard 缺失（HTTP 局域网）→ 直接走降级路径', async () => {
    vi.stubGlobal('navigator', { clipboard: undefined })

    const ok = await copyText(LONG_URL)
    expect(ok).toBe(true)
    expect(execCommandSpy).toHaveBeenCalledWith('copy')
    expect(writeTextMock).not.toHaveBeenCalled()
  })

  it('clipboard 存在但 writeText 非函数 → 降级路径', async () => {
    vi.stubGlobal('navigator', { clipboard: {} })

    const ok = await copyText('https://example.com/a')
    expect(ok).toBe(true)
    expect(execCommandSpy).toHaveBeenCalled()
  })

  it('execCommand 返回 false → copyText 返回 false（组件不显示"已复制"）', async () => {
    vi.stubGlobal('navigator', { clipboard: undefined })
    execCommandSpy.mockReturnValue(false)

    const ok = await copyText('https://example.com/a')
    expect(ok).toBe(false)
  })

  it('execCommand 抛异常 → copyText 返回 false 且不向上抛', async () => {
    vi.stubGlobal('navigator', { clipboard: undefined })
    execCommandSpy.mockImplementation(() => {
      throw new Error('execCommand boom')
    })

    const ok = await copyText('https://example.com/a')
    expect(ok).toBe(false)
  })

  it('空串 → 直接 false，不调用 clipboard 也不降级', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: writeTextMock } })

    expect(await copyText('')).toBe(false)
    expect(writeTextMock).not.toHaveBeenCalled()
    expect(execCommandSpy).not.toHaveBeenCalled()
  })

  it('纯空白串按实现语义：视为可复制文本走 writeText（不做空白过滤）', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: writeTextMock } })

    const ok = await copyText('   ')
    expect(ok).toBe(true)
    expect(writeTextMock).toHaveBeenCalledWith('   ')
  })
})
