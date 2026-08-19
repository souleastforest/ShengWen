/**
 * 章节导航面板宽屏默认收起（遮挡修复）
 *
 * P7 引入分P面板后，宽屏章节面板默认展开（isWidePanelCollapsed 初始 false）
 * 覆盖内容区顶部 541px——分P面板前几行被白色面板遮挡，行右侧不可点击。
 * 修复：宽屏默认收起（与窄屏一致），点击"章节"按钮展开。
 *
 * 每个用例失败 = 实现缺陷。
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

const headings: MarkdownHeadingItem[] = [
  { id: 'h1', text: '总体概览', level: 1 },
  { id: 'h2', text: '1. 命理学概述', level: 2 },
]

const mountNav = () =>
  mount(FloatingToolbarChapterNav, {
    props: { headings, activeHeadingId: 'h1' },
  })

describe('宽屏章节面板默认收起（遮挡修复）', () => {
  it('宽屏挂载后：面板默认收起（不遮挡内容区）', () => {
    installMatchMediaMock()
    const wrapper = mountNav()
    const panel = wrapper.find('[class*="w-80"]')
    // 默认收起：invisible / pointer-events-none
    expect(panel.classes()).toContain('invisible')
    expect(panel.classes()).toContain('pointer-events-none')
    wrapper.unmount()
  })

  it('点击"章节"按钮展开面板，再点收起', async () => {
    installMatchMediaMock()
    const wrapper = mountNav()
    const btn = wrapper.find('button[title="章节导航"]')
    await btn.trigger('click')
    expect(wrapper.find('[class*="w-80"]').classes()).toContain('visible')
    await btn.trigger('click')
    expect(wrapper.find('[class*="w-80"]').classes()).toContain('invisible')
    wrapper.unmount()
  })

  it('窄屏保持默认收起，展开后跳转自动收起', async () => {
    installMatchMediaMock()
    isWide = false
    mediaListeners.forEach((cb) => cb({ matches: false }))
    const wrapper = mountNav()
    const panel = wrapper.find('[class*="w-80"]')
    expect(panel.classes()).toContain('invisible')
    const btn = wrapper.find('button[title="章节导航"]')
    await btn.trigger('click')
    expect(panel.classes()).toContain('visible')
    const firstHeadingBtn = panel.findAll('button')[0]
    expect(firstHeadingBtn).toBeTruthy()
    await firstHeadingBtn!.trigger('click')
    await wrapper.vm.$nextTick()
    expect(panel.classes()).toContain('invisible')
    wrapper.unmount()
  })
})
