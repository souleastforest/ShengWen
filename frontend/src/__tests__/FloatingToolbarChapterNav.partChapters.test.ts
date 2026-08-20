/**
 * 章节胶囊分P章节区（FloatingToolbarChapterNav）
 *
 * 分P项由 App.vue 合并进 headings（id 'part-' 前缀）；组件侧职责：
 * - 在总览标题之后渲染分P项（level 2 缩进/字号）
 * - 总览区/分P区之间渲染"分P"分区标签（仅同时存在总览标题与分P项时）
 * - 点击分P项 emit jump('part-x')，窄屏下自动收起（与普通跳转一致）
 * - activeHeadingId 命中分P项时高亮
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import FloatingToolbarChapterNav from '../components/FloatingToolbarChapterNav.vue'
import type { MarkdownHeadingItem } from '../types'

type MediaListener = (e: { matches: boolean }) => void
let isWide = true
const mediaListeners: MediaListener[] = []

const installMatchMediaMock = () => {
  isWide = true
  mediaListeners.length = 0
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: isWide,
      media: query,
      addEventListener: (_type: string, cb: MediaListener) => mediaListeners.push(cb),
      removeEventListener: () => {},
      addListener: (cb: MediaListener) => mediaListeners.push(cb),
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    })),
  )
}

const headingsWithParts: MarkdownHeadingItem[] = [
  { id: '总体概览', text: '总体概览', level: 1 },
  { id: 'part-0', text: 'P1 第一章', level: 2 },
  { id: 'part-1', text: 'P2 第二章', level: 2 },
]

const mountNav = (headings: MarkdownHeadingItem[], activeHeadingId?: string) =>
  mount(FloatingToolbarChapterNav, {
    props: { headings, activeHeadingId: activeHeadingId ?? headings[0]?.id },
  })

const expandPanel = async (wrapper: ReturnType<typeof mountNav>) => {
  await wrapper.find('button[title="章节导航"]').trigger('click')
}

describe('章节胶囊分P章节区', () => {
  it('渲染分P项（总览标题之后），总览/分P区之间显示"分P"分区标签', async () => {
    installMatchMediaMock()
    const wrapper = mountNav(headingsWithParts)
    await expandPanel(wrapper)

    const buttons = wrapper.findAll('[class*="w-80"] button')
    expect(buttons.length).toBe(3)
    expect(buttons[0]!.text()).toBe('总体概览')
    expect(buttons[1]!.text()).toBe('P1 第一章')
    expect(buttons[2]!.text()).toBe('P2 第二章')

    // 分区标签位于总览标题与首个分P项之间
    const label = wrapper.find('[data-testid="chapter-nav-part-label"]')
    expect(label.exists()).toBe(true)
    expect(label.text()).toContain('分P')

    wrapper.unmount()
  })

  it('点击分P项 emit jump(part-id)；窄屏下自动收起（与普通跳转一致）', async () => {
    installMatchMediaMock()
    isWide = false
    const wrapper = mountNav(headingsWithParts, 'part-0')
    await expandPanel(wrapper)

    const buttons = wrapper.findAll('[class*="w-80"] button')
    const partButton = buttons[2]!
    await partButton.trigger('click')
    await wrapper.vm.$nextTick()

    const jumpEmits = wrapper.emitted('jump')
    expect(jumpEmits).toBeTruthy()
    expect(jumpEmits![jumpEmits!.length - 1]![0]).toBe('part-1')
    // 窄屏点击后自动收起
    expect(wrapper.find('[class*="w-80"]').classes()).toContain('invisible')

    wrapper.unmount()
  })

  it('activeHeadingId 命中分P项：高亮该分P项', async () => {
    installMatchMediaMock()
    const wrapper = mountNav(headingsWithParts, 'part-1')
    await expandPanel(wrapper)

    const buttons = wrapper.findAll('[class*="w-80"] button')
    expect(buttons[2]!.classes()).toContain('border-blue-500')
    expect(buttons[0]!.classes()).not.toContain('border-blue-500')

    wrapper.unmount()
  })

  it('无分P项（纯总览）时不渲染分区标签', async () => {
    installMatchMediaMock()
    const wrapper = mountNav([{ id: 'h1', text: '总体概览', level: 1 }])
    await expandPanel(wrapper)

    expect(wrapper.findAll('[class*="w-80"] button').length).toBe(1)
    expect(wrapper.find('[data-testid="chapter-nav-part-label"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="chapter-nav-part-item"]').exists()).toBe(false)

    wrapper.unmount()
  })
})
