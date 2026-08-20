/**
 * seam 契约测试：features/transcription/useMarkdownCompile.ts
 *
 * App.vue markdown 编译管线（行为逐行等价）：marked 自定义 renderer
 * （mermaid 类名）+ DOMPurify 单点净化 + postProcessCompiledMarkdown +
 * 多P 总结一P一页分页状态机。
 * 契约面：
 * - configureMarkdownRenderer() / compileMarkdownText(summary, { videoUrl })
 * - useMarkdownCompile({ selectedTask, parts, partDetails }) →
 *   { overviewCompiledMarkdown, pageCompiledMarkdown, multipartPage,
 *     multipartPageCount, multipartPagePart, changeMultipartPage }
 *
 * 缺陷回归（重构前旧逻辑）：
 * - 旧 getMultipartPages 按每 10 个 P 拼一页 → 页数 ≠ parts 数；
 * - 旧 getMultipartOverview 无 "# 分P总结" 标记时 slice(0, 12000) 截断。
 *
 * 真实 API 契约（对抗评审修正 + 主流程 e2e 实测修正）：
 * - GET /tasks/{id}/parts 列表行不含 summary/transcript（include_text=False
 *   剥离），分P内容只来自 GET /tasks/{id}/parts/{idx}（partDetails）；
 * - 主行 summary 存在两种分P格式：一级 "# 分P总结" 标记，且该标记可能位于
 *   所有完整分P段【之后】（任务 0f13aa14 实测：总览 → 完整分P段 →
 *   "# 分P总结" → 精简分P段）；
 * - 总览提取 = 双标记取 min：一级标记位置与首个 "## Pn：" 位置（均可能 -1）
 *   中 >= 0 的最小值；都无 → 整个 summary。
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
import type { Task, TaskPart } from '../../../types'

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

const makePart = (partIndex: number, overrides: Partial<TaskPart> = {}): TaskPart => ({
  task_id: 'task-1',
  part_index: partIndex,
  status: 'COMPLETED',
  progress: 1,
  duration: 60,
  title: `P${partIndex + 1}`,
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

const mountCompile = (
  task: Task | null,
  parts: TaskPart[] = [],
  partDetails: Record<number, TaskPart> = {},
) => {
  let compile!: ReturnType<typeof useMarkdownCompile>
  const selectedTask = ref<Task | null>(task)
  const partsRef = ref<TaskPart[]>(parts)
  const partDetailsRef = ref<Record<number, TaskPart>>(partDetails)
  const TestComponent = defineComponent({
    setup() {
      compile = useMarkdownCompile({ selectedTask, parts: partsRef, partDetails: partDetailsRef })
      return () => null
    },
  })
  const wrapper = mount(TestComponent)
  return { compile, wrapper, selectedTask, parts: partsRef, partDetails: partDetailsRef }
}

describe('useMarkdownCompile：多P 一P一页分页', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('非多P任务：主行 summary 全量渲染（回归），multipartPageCount=0', async () => {
    const { compile, selectedTask } = mountCompile(makeTask({ summary: '普通总结全部内容' }))
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.overviewCompiledMarkdown.value).toContain('普通总结全部内容')
    expect(compile.multipartPageCount.value).toBe(0)
    expect(compile.pageCompiledMarkdown.value).toBe('')
    selectedTask.value = null
  })

  it('多P任务：总览 = "# 分P总结" 标记前部分（常驻编译），当前页 = P1 summary（来自 partDetails）', async () => {
    const summary = `总览部分内容\n\n# 分P总结\n\n## P1：第一部分\n内容1\n\n## P2：第二部分\n内容2`
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary }),
      // 真实契约：parts 列表行无 summary，分P内容只来自 partDetails
      [makePart(0), makePart(1)],
      { 0: makePart(0, { summary: 'P1的总结' }) },
    )
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.overviewCompiledMarkdown.value).toContain('总览部分内容')
    expect(compile.overviewCompiledMarkdown.value).not.toContain('分P总结')
    expect(compile.pageCompiledMarkdown.value).toContain('P1的总结')
    selectedTask.value = null
  })

  it('总览提取：无一级标记、首个 "## Pn：" 段落标记 → 标记前部分', async () => {
    const summary = [
      '# 总体概览',
      '',
      '{{分P视频标题}}',
      '',
      '## 总体概览',
      '',
      '正文内容...',
      '',
      '## P1：命理学概述 🔮',
      '### 1. 内容',
      '',
      '## P2：命能不能算 🧭',
      '### 2. 内容',
    ].join('\n')
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary }),
      [makePart(0), makePart(1)],
    )
    await vi.advanceTimersByTimeAsync(120)
    const overview = compile.overviewCompiledMarkdown.value
    // 总览自身段落（含 "## 总体概览" 二级标题）完整保留：不得被误判为分P起点
    expect(overview).toContain('总体概览')
    expect(overview).toContain('正文内容')
    // 分P段落不得泄漏进总览
    expect(overview).not.toContain('命理学概述')
    expect(overview).not.toContain('命能不能算')
    selectedTask.value = null
  })

  it('总览三段式 ②：半角冒号 "## P1:" 段落标记同样识别', async () => {
    const summary = '总览正文\n\n## P1: 内容\n### 1. x'
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary }),
      [makePart(0)],
    )
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.overviewCompiledMarkdown.value).toContain('总览正文')
    expect(compile.overviewCompiledMarkdown.value).not.toContain('P1:')
    selectedTask.value = null
  })

  it('0f13aa14 真实格式：一级 "# 分P总结" 标记位于【所有分P段之后】，总览 = 首个 "## Pn：" 之前（min 判据）', async () => {
    // 真实数据契约（主流程 e2e 实测，任务 0f13aa14）：
    // 总览（总体概览 + mermaid）→ 11 个完整分P段（2000-3000 字符/段）
    // → 一级 "# 分P总结" 标记在【段后】→ 后半 11 个精简分P段。
    // 旧三段式先命中一级标记 → slice(0, marker) 把全部完整分P段泄进总览。
    const summary = [
      '# 总体概览',
      '',
      '正文',
      '',
      '```mermaid',
      'graph TD',
      '```',
      '',
      '## P1：第一段完整内容',
      '详细内容...',
      '',
      '## P2：第二段完整内容',
      '详细内容...',
      '',
      '# 分P总结',
      '',
      '## P1：精简版',
    ].join('\n')
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary }),
      [makePart(0), makePart(1)],
    )
    await vi.advanceTimersByTimeAsync(120)
    const overview = compile.overviewCompiledMarkdown.value
    // 总览自身（含 mermaid）完整保留
    expect(overview).toContain('总体概览')
    expect(overview).toContain('正文')
    expect(overview).toContain('graph TD')
    // 任何分P段内容（完整段或精简段）不得泄漏进总览
    expect(overview).not.toContain('第一段完整内容')
    expect(overview).not.toContain('第二段完整内容')
    expect(overview).not.toContain('精简版')
    selectedTask.value = null
  })

  it('ed4cd000 格式：一级 "# 分P总结" 标记位于分P段之前 → 总览不含该标题', async () => {
    const summary = [
      '# 总体概览',
      '',
      '```mermaid',
      'graph TD',
      '```',
      '',
      '# 分P总结',
      '',
      '## P1: xxx',
    ].join('\n')
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary }),
      [makePart(0)],
    )
    await vi.advanceTimersByTimeAsync(120)
    const overview = compile.overviewCompiledMarkdown.value
    expect(overview).toContain('总体概览')
    expect(overview).toContain('graph TD')
    expect(overview).not.toContain('分P总结')
    expect(overview).not.toContain('P1')
    selectedTask.value = null
  })

  it('总览三段式 ③：无任何标记（纯文本）→ 整个 summary，不做截断', async () => {
    const summary = '纯文本总览，无分P段落标题\n\n第二段内容'
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary }),
      [makePart(0)],
    )
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.overviewCompiledMarkdown.value).toContain('纯文本总览，无分P段落标题')
    expect(compile.overviewCompiledMarkdown.value).toContain('第二段内容')
    selectedTask.value = null
  })

  it('回归修复：无 "# 分P总结" 标记时总览 = 整个 summary，不再截断 12000 字符', async () => {
    const summary = 'x'.repeat(15000)
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary }),
      [makePart(0)],
    )
    await vi.advanceTimersByTimeAsync(120)
    const xCount = (compile.overviewCompiledMarkdown.value.match(/x/g) || []).length
    expect(xCount).toBeGreaterThan(12000)
    expect(compile.overviewCompiledMarkdown.value).toContain(summary)
    selectedTask.value = null
  })

  it('回归修复：页数 = parts 数量（一P一页），而非每 10 个 P 拼一页', async () => {
    const parts = [makePart(0), makePart(1), makePart(2)]
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary: '总览' }),
      parts,
    )
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.multipartPageCount.value).toBe(3)
    selectedTask.value = null
  })

  it('非多P任务即使传入 parts 也保持 multipartPageCount=0', async () => {
    const { compile, selectedTask } = mountCompile(
      makeTask({ summary: '普通总结' }),
      [makePart(0, { summary: 'x' })],
    )
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.multipartPageCount.value).toBe(0)
    selectedTask.value = null
  })

  it('翻页后当前页内容切换重新编译（仅 pageCompiledMarkdown 变化）', async () => {
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary: '总览' }),
      [makePart(0), makePart(1)],
      { 0: makePart(0, { summary: 'P1总结' }), 1: makePart(1, { summary: 'P2总结' }) },
    )
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.pageCompiledMarkdown.value).toContain('P1总结')

    compile.changeMultipartPage(1)
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.pageCompiledMarkdown.value).toContain('P2总结')
    expect(compile.pageCompiledMarkdown.value).not.toContain('P1总结')
    // 总览常驻：翻页不影响总览段
    expect(compile.overviewCompiledMarkdown.value).toContain('总览')
    selectedTask.value = null
  })

  it('真实契约：parts 列表行无 summary 时不产生页面内容，summary 只来自 partDetails（懒拉驱动）', async () => {
    const parts = [makePart(0)] // 列表行无 summary（include_text=False 剥离）
    const { compile, selectedTask, partDetails } = mountCompile(
      makeTask({ has_parts: true, summary: '总览' }),
      parts,
    )
    await vi.advanceTimersByTimeAsync(120)
    // parts 行无 summary → 页面无内容（App 由 watch 懒拉 fetchTaskPart 填充 partDetails）
    expect(compile.pageCompiledMarkdown.value).toBe('')
    // 占位状态判断仍回退 parts 行（status 可透出）
    expect(compile.multipartPagePart.value).toStrictEqual(parts[0])

    // partDetails 由 fetchTaskPart 填充后：详情产生内容并重新编译
    partDetails.value = { 0: makePart(0, { summary: '详情完整版' }) }
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.pageCompiledMarkdown.value).toContain('详情完整版')
    selectedTask.value = null
  })

  it('分P summary 缺失（处理中）：pageCompiledMarkdown 为空，multipartPagePart 透出状态供占位判断', async () => {
    const parts = [makePart(0, { status: 'TRANSCRIBING', summary: undefined })]
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary: '总览' }),
      parts,
    )
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.pageCompiledMarkdown.value).toBe('')
    expect(compile.multipartPagePart.value?.status).toBe('TRANSCRIBING')
    selectedTask.value = null
  })

  it('changeMultipartPage 越界夹逼到 0..N-1', async () => {
    const parts = [makePart(0, { summary: 'a' }), makePart(1, { summary: 'b' }), makePart(2, { summary: 'c' })]
    const { compile, selectedTask } = mountCompile(
      makeTask({ has_parts: true, summary: '总览' }),
      parts,
    )
    compile.changeMultipartPage(99)
    expect(compile.multipartPage.value).toBe(2)
    compile.changeMultipartPage(-1)
    expect(compile.multipartPage.value).toBe(0)
    selectedTask.value = null
  })

  it('任务切换：multipartPage 重置为 0，编译产物切换到新任务（防串台）', async () => {
    const taskA = makeTask({ id: 'task-a', has_parts: true, summary: 'A总览' })
    const taskB = makeTask({ id: 'task-b', summary: 'B全部内容' })
    const { compile, selectedTask, parts, partDetails } = mountCompile(
      taskA,
      [makePart(0), makePart(1)],
      { 0: makePart(0, { summary: 'A的P1' }), 1: makePart(1, { summary: 'A的P2' }) },
    )
    await vi.advanceTimersByTimeAsync(120)
    compile.changeMultipartPage(1)
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.pageCompiledMarkdown.value).toContain('A的P2')

    // 切换任务（App 会清空 parts 与 partDetails）
    parts.value = []
    partDetails.value = {}
    selectedTask.value = taskB
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.multipartPage.value).toBe(0)
    expect(compile.multipartPageCount.value).toBe(0)
    expect(compile.overviewCompiledMarkdown.value).toContain('B全部内容')
    expect(compile.overviewCompiledMarkdown.value).not.toContain('A总览')
    selectedTask.value = null
  })

  it('generation 守卫：120ms 防抖窗口内切换任务，旧任务编译产物不得落地', async () => {
    const taskA = makeTask({ id: 'task-a', has_parts: true, summary: 'AAAA' })
    const taskB = makeTask({ id: 'task-b', summary: 'BBBB' })
    const { compile, selectedTask, parts } = mountCompile(taskA, [makePart(0)])
    // 任务 A 编译定时器在途时立即切换任务
    selectedTask.value = taskB
    parts.value = []
    await vi.advanceTimersByTimeAsync(120)
    expect(compile.overviewCompiledMarkdown.value).toContain('BBBB')
    expect(compile.overviewCompiledMarkdown.value).not.toContain('AAAA')
    selectedTask.value = null
  })
})
