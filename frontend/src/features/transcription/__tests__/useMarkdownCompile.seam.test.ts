/**
 * P6 seam 契约测试：features/transcription/useMarkdownCompile.ts
 *
 * App.vue markdown 编译管线下沉（行为逐行等价）：marked 自定义 renderer
 * （mermaid 类名）+ DOMPurify 单点净化 + postProcessCompiledMarkdown +
 * 多P 总结分页（useMultipartSummary 本质）。
 * 契约面：
 * - configureMarkdownRenderer() / compileMarkdownText(summary, { videoUrl })
 * - useMarkdownCompile({ selectedTask, fetchTaskFullContent }) →
 *   { compiledMarkdown, showFullMultipartSummary, multipartPage,
 *     multipartPageCount, expandMultipartSummary, collapseMultipartSummary,
 *     changeMultipartPage }
 */
import { defineComponent, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DOMPurify from 'dompurify'
import {
  compileMarkdownText,
  configureMarkdownRenderer,
  useMarkdownCompile,
} from '../useMarkdownCompile'
import type { Task } from '../../../types'

// 与 App.p1Defenses.test.ts 同款 DOMPurify 行为双（happy-dom 与全量算法不兼容）
vi.mock('dompurify', () => {
  const stripEventHandlers = (html: string) =>
    String(html)
      .replace(/\son\w+=["'][^"']*["']/g, '')
      .replace(/\sstyle=(["'])[^"']*\1/g, '')
  return {
    default: { sanitize: vi.fn(stripEventHandlers) },
  }
})

const makeTask = (overrides: Partial<Task>): Task => ({
  id: 'task-1',
  video_url: 'https://example.com/video.mp4',
  status: 'COMPLETED',
  progress: 1.0,
  created_at: '2026-08-08T10:00:00Z',
  ...overrides,
})

describe('compileMarkdownText（编译管线纯函数）', () => {
  it('mermaid 代码块经自定义 renderer 输出 <pre class="mermaid">', () => {
    configureMarkdownRenderer()
    const html = compileMarkdownText('```mermaid\ngraph TD\n```')
    expect(html).toContain('<pre class="mermaid">graph TD</pre>')
  })

  it('普通代码块输出带 language 类的 code 块（postProcess 追加 ss-block-code）', () => {
    const html = compileMarkdownText('```python\nprint(1)\n```')
    expect(html).toContain('<pre><code class="language-python ss-block-code">print(1)</code></pre>')
  })

  it('XSS 向量经 sanitize 剥离（onerror/style），正常内容保留', () => {
    const html = compileMarkdownText('<img src="x" onerror="alert(1)"><p style="color:red">正文</p>')
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('style=')
    expect(html).toContain('正文')
  })

  it('DOMPurify.sanitize 调用带 FORBID_ATTR/USE_PROFILES 配置（管线出口契约）', () => {
    compileMarkdownText('hello')
    expect(DOMPurify.sanitize).toHaveBeenCalledWith(
      expect.anything(),
      { FORBID_ATTR: ['style'], USE_PROFILES: { html: true } },
    )
  })

  it('去除双大括号占位符 {{topic: xyz}}', () => {
    const html = compileMarkdownText('开头 {{topic: 我的标题}}\n\n正文内容')
    expect(html).not.toContain('{{')
  })

  it('时间芯片后处理：postProcessCompiledMarkdown 追加视频站跳转链接', () => {
    const html = compileMarkdownText('见 (00:12:34)', { videoUrl: 'https://www.bilibili.com/video/BV1' })
    expect(html).toContain('bilibili.com')
  })
})

const mountCompile = (task: Task | null, fetchTaskFullContent = vi.fn()) => {
  let compile!: ReturnType<typeof useMarkdownCompile>
  const selectedTask = ref<Task | null>(task)
  const TestComponent = defineComponent({
    setup() {
      compile = useMarkdownCompile({ selectedTask, fetchTaskFullContent })
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  return { compile, wrapper, selectedTask, fetchTaskFullContent }
}

describe('useMarkdownCompile：多P 总结预览与分页', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('分P任务无分P标记时 overview = 前 12000 字符', async () => {
    const summary = 'x'.repeat(15000)
    const { compile, selectedTask } = mountCompile(makeTask({ has_parts: true, summary }))
    await vi.advanceTimersByTimeAsync(120)
    // marked 会包裹 <p> 等标签，按原始字符计数断言截断生效
    const xCount = (compile.compiledMarkdown.value.match(/x/g) || []).length
    expect(xCount).toBe(12000)
    selectedTask.value = null
  })

  it('分P任务默认预览 = 分P总结标记前的 overview，multipartPageCount 正确', async () => {
    const summary = `总览部分内容\n\n# 分P总结\n\n## P1：第一部分\n内容1\n\n## P2：第二部分\n内容2`
    const { compile, selectedTask } = mountCompile(makeTask({ has_parts: true, summary }))
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.compiledMarkdown.value).toContain('总览部分内容')
    expect(compile.compiledMarkdown.value).not.toContain('分P总结')
    expect(compile.multipartPageCount.value).toBe(1)
    selectedTask.value = null
  })

  it('展开后按页加载，页码切换重新编译', async () => {
    const pages = Array.from({ length: 12 }, (_, i) => `## P${i + 1}：第${i + 1}节\n内容${i + 1}`).join('\n\n')
    const summary = `# 分P总结\n\n${pages}`
    const { compile, selectedTask } = mountCompile(makeTask({ has_parts: true, summary }))
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.multipartPageCount.value).toBe(2) // 12 节 → 每页 10

    compile.showFullMultipartSummary.value = true
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.compiledMarkdown.value).toContain('P1')
    expect(compile.compiledMarkdown.value).not.toContain('P11')

    compile.changeMultipartPage(1)
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.compiledMarkdown.value).toContain('P11')
    selectedTask.value = null
  })

  it('changeMultipartPage 越界夹逼', () => {
    const pages = Array.from({ length: 3 }, (_, i) => `## P${i + 1}：x${i + 1}\n内容`).join('\n\n')
    const { compile, selectedTask } = mountCompile(makeTask({ has_parts: true, summary: `# 分P总结\n\n${pages}` }))
    compile.changeMultipartPage(99)
    expect(compile.multipartPage.value).toBe(0)
    compile.showFullMultipartSummary.value = true
    compile.changeMultipartPage(99)
    expect(compile.multipartPage.value).toBe(0) // 单页 → 上限 0
    selectedTask.value = null
  })

  it('expandMultipartSummary：fetchTaskFullContent 成功且身份一致才展开', async () => {
    const task = makeTask({ id: 't1', has_parts: true, summary: '截断' })
    const fetchTaskFullContent = vi.fn().mockResolvedValue(undefined)
    const { compile, selectedTask } = mountCompile(task, fetchTaskFullContent)
    await compile.expandMultipartSummary()
    expect(fetchTaskFullContent).toHaveBeenCalledWith('t1')
    expect(compile.showFullMultipartSummary.value).toBe(true)
    selectedTask.value = null
  })

  it('expandMultipartSummary 身份守卫：等待期间切换任务不展开', async () => {
    const task = makeTask({ id: 't1', has_parts: true, summary: '截断' })
    let resolveFetch!: () => void
    const fetchTaskFullContent = vi.fn().mockReturnValue(new Promise<void>((resolve) => { resolveFetch = resolve }))
    const { compile, selectedTask } = mountCompile(task, fetchTaskFullContent)

    const pending = compile.expandMultipartSummary()
    selectedTask.value = makeTask({ id: 't2' })
    resolveFetch()
    await pending
    expect(compile.showFullMultipartSummary.value).toBe(false)
    selectedTask.value = null
  })

  it('任务切换重置分页状态（showFullMultipartSummary=false, page=0）', async () => {
    const summary = `# 分P总结\n\n## P1：x1\n内容1`
    const { compile, selectedTask } = mountCompile(makeTask({ has_parts: true, summary }))
    await vi.advanceTimersByTimeAsync(120)
    compile.showFullMultipartSummary.value = true
    compile.changeMultipartPage(0)

    selectedTask.value = makeTask({ id: 't2', summary: '另一个任务' })
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.showFullMultipartSummary.value).toBe(false)
    expect(compile.multipartPage.value).toBe(0)
    expect(compile.compiledMarkdown.value).toContain('另一个任务')
    selectedTask.value = null
  })
})
