/**
 * P6 seam 契约测试：features/transcription/components/MermaidBlock.vue
 *
 * TaskContentArea mermaid 命令式 DOM 渲染封装（纯搬移，行为等价）：
 * - props: taskId / renderKey / enabled
 * - emits: open-viewer
 * - renderVersion 防竞态保留（渲染期间内容变更 → 陈旧渲染丢弃）
 */
import { defineComponent, h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import MermaidBlock from '../components/MermaidBlock.vue'
import { getMermaid } from '../../../utils/mermaidLoader'

vi.mock('../../../utils/mermaidLoader', () => ({
  getMermaid: vi.fn(),
}))

const mockedGetMermaid = vi.mocked(getMermaid)

const createDeferred = () => {
  let resolve!: (value: { svg: string; bindFunctions?: unknown }) => void
  const promise = new Promise<{ svg: string; bindFunctions?: unknown }>((res) => { resolve = res })
  return { promise, resolve }
}

/**
 * 宿主包装：通过 innerHTML 模拟生产环境 v-html 内容更新
 * （compiledMarkdown 变化 → 内容整体重建 → .mermaid 节点重新出现）
 */
const mountHost = (html: string, extraProps: Record<string, unknown> = {}) => {
  const Wrapper = defineComponent({
    props: { html: String },
    emits: ['open-viewer'],
    setup(props, { emit: hostEmit }) {
      return () => h(MermaidBlock, {
        taskId: 't1',
        renderKey: props.html ?? '',
        enabled: true,
        onOpenViewer: (target: HTMLElement) => hostEmit('open-viewer', target),
      }, {
        default: () => h('div', { 'data-summary-content': '', innerHTML: props.html ?? '' }),
      })
    },
  })
  const wrapper = mount(Wrapper, { props: { html, ...extraProps } })
  return wrapper
}

describe('MermaidBlock seam：初始化渲染', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('enabled 且 renderKey 非空时替换 .mermaid 节点为 ss-mermaid-block（含工具栏）', async () => {
    mockedGetMermaid.mockResolvedValue({
      render: vi.fn().mockResolvedValue({ svg: '<svg><text>diagram</text></svg>' }),
    } as never)
    const wrapper = mountHost('<pre class="mermaid">graph TD</pre>')
    await nextTick()
    await nextTick()

    expect(wrapper.find('.ss-mermaid-block').exists()).toBe(true)
    expect(wrapper.find('.ss-mermaid-toolbar').exists()).toBe(true)
    expect(wrapper.findAll('.ss-mermaid-tool-btn')).toHaveLength(4)
    // 代码面板默认隐藏
    expect(wrapper.find('.ss-mermaid-code-panel').element.getAttribute('hidden')).not.toBeNull()
    // 渲染产物注入
    expect(wrapper.find('.ss-mermaid-render svg').exists()).toBe(true)
  })

  it('空 renderKey 不触发渲染', async () => {
    const wrapper = mountHost('')
    await nextTick()
    expect(mockedGetMermaid).not.toHaveBeenCalled()
    expect(wrapper.find('pre.mermaid').exists()).toBe(false)
  })
})

describe('MermaidBlock seam：预览 emit 与竞态防护', () => {
  it('预览按钮 emit open-viewer（携带渲染宿主）', async () => {
    mockedGetMermaid.mockResolvedValue({
      render: vi.fn().mockResolvedValue({ svg: '<svg><text>d</text></svg>' }),
    } as never)
    const wrapper = mountHost('<pre class="mermaid">graph TD</pre>')
    await nextTick()
    await nextTick()

    const preview = wrapper.findAll('.ss-mermaid-tool-btn').find((b) => b.attributes('title') === '预览')!
    await preview.trigger('click')
    const emits = wrapper.emitted('open-viewer')!
    expect(emits).toHaveLength(1)
    expect(emits[0]![0]).toBeTruthy()
    expect((emits[0]![0] as HTMLElement).querySelector('svg')).toBeTruthy()
  })

  it('renderVersion 防竞态：渲染在途时内容变更，陈旧渲染被丢弃', async () => {
    const deferred = createDeferred()
    const renderMock = vi.fn().mockImplementation((_id: string, code: string) => {
      if (code === 'old-code') return deferred.promise
      return Promise.resolve({ svg: `<svg><text>${code}</text></svg>` })
    })
    mockedGetMermaid.mockResolvedValue({ render: renderMock } as never)

    const wrapper = mountHost('<pre class="mermaid">old-code</pre>')
    await nextTick()
    // 渲染在途：内容更新（v-html 重建，.mermaid 节点重新出现）
    await wrapper.setProps({ html: '<pre class="mermaid">new-code</pre>' })
    await nextTick()
    // 陈旧渲染完成：应被 renderVersion 守卫丢弃
    deferred.resolve({ svg: '<svg><text>stale-diagram</text></svg>' })
    await nextTick()
    await nextTick()

    expect(renderMock).toHaveBeenCalledTimes(2)
    // 最终 DOM 呈现新渲染产物而非陈旧产物
    expect(wrapper.find('.ss-mermaid-render svg text').text()).toBe('new-code')
    expect(wrapper.text()).not.toContain('stale-diagram')
  })
})
