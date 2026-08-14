# 前端重构计划 — refactor/frontend-design

**Branch**: `refactor/frontend-design`（基于 `public @ 7fbab68`，== origin/public）
**Date**: 2026-08-13
**来源**: grilling 会话（/grill-me）收敛 + 全面前端设计审计（workflow wf_a2d2c985-786，7 子代理，~590k tokens）

---

## 1. 目标与阶段

- **阶段 A（本分支范围，P1–P6）**：设计债清理。严格行为保持——正常路径行为等价（同一输入 → 同一 UI 输出）；缺陷修复仅限错误路径。
- **阶段 B（A 之后）**：新功能铺路。P7 composable 域拆分完成后的能力扩展。
- **阶段 C**：UX/体验改进（backlog 清单见 §7）。

### 用户决策记录（grilling Q1–Q12）

| # | 决策 | 结果 |
|---|---|---|
| Q1 | 目标 | A（设计债清理）为骨架；B/C 在 A 后继续 |
| Q2 | 范围 | 组件 + composables 状态层为主，API 类型契约顺手对齐；工具链/CSS 不动（除非硬伤） |
| Q3 | 交付形态 | 完整计划先行（本文档），实施按小点派子代理 |
| Q4→Q5 | 分支策略 | refactor 分支 push origin；每小点 GitHub PR → merge 进集成分支；整体完成后再 PR → public |
| Q6 | 行为保持 | 严格保持；顺手发现的 UX 问题记入 C 清单 |
| Q7 | 工作包 | 7 包按 P1→P2→P3→P4→P5→P6→P7 顺序执行 |
| Q8 | 设置双入口 | 抽公共 SettingsForm 双入口复用，语义以弹窗（SettingsModal）为准 |
| Q9 | 缺陷修复 | 计入 A 阶段（正常路径等价 + 错误路径被修复） |
| Q10 | 拆分深度 | A 做组件层拆分（P6）；composable 域拆分（P7）为 B 阶段第一个包 |
| Q11 | 目标架构 | 采纳 features/ + shared/ 分层（Vue 惯用 DDD 映射） |
| Q12 | 新依赖 | 批准引入 DOMPurify（P1 XSS 净化，唯一新增依赖） |

---

## 2. 目标架构（Q11）

后端 DDD 概念 → 前端 Vue 惯用映射：

| 后端 DDD | 前端对应物 |
|---|---|
| `src/<domain>/type.py`（dataclass 契约 + 往返测试） | `src/features/<domain>/types.ts`（TS 契约 + 守卫/映射 + 契约测试） |
| `<Domain>Repository`（数据访问） | `<domain>/api.ts`（API 适配器 + per-task 内容缓存） |
| `<Domain>Service`（业务编排） | `<domain>/state.ts`（composable：状态 + 动作，注入 api adapter 可测） |
| 依赖方向 service→repository→storage/util | state → api → shared/{api,ws,utils}；**组件只允许依赖本域 state 与 shared** |
| `src/types/` 跨域共享 | `src/shared/types.ts`（TaskStatus 枚举等跨域类型） |

```
frontend/src/
  app/            # App.vue（装配）、main.ts —— 唯一编排层
  features/
    task/         # types.ts + api.ts + state.ts + components/（TaskList、TaskMetaCard、TaskPartsPanel…）
    upload/       # 提交三通道、BilibiliPartsSelector、LocalFolderSelector、Sidebar 提交区
    settings/     # SettingsForm ×3（公共）、SettingsModal
    transcription/# 内容展示：TaskContentArea、Mermaid 渲染管线
  shared/         # api/（axios 实例+拦截器）、ws/（连接生命周期）、utils/、types/
```

落地节奏：P6 拆分时新抽出组件**直接落位 features/ 目标目录**；存量文件（App.vue、Sidebar 骨架、useTaskViewModel）P7 迁移（24 个 vitest 文件按域切分后是迁移安全网）。

**审计警告（必须遵守）**：composable 域拆分是最高风险项，必须先完成 P1/P2 加固（hasContent/版本/身份守卫语义固化），否则守卫随域拆散。

---

## 3. 工作包

### P1 防线加固（安全 + 一致性守卫）— 先做

**范围**（依据审计 high 级 findings）：
1. **XSS 单点净化**：App.vue markdown 编译管线出口（`compiledMarkdown` 赋值处，App.vue:858 附近）加 `DOMPurify.sanitize()`；`TaskContentArea.vue:554` v-html 渲染层兜底断言。引入 `dompurify`（Q12 已批准）。MermaidViewerModal svgContent 风险低（mermaid strict 模式），记录不修。
2. **内容合并守卫统一**：`useTaskViewModel.ts:253`（fetchTasks 合并）、`:705-709`（WS task_update 合并）、`:525-601`（selectTask）——抽取 `mergeTaskWithPreservedContent`（hasContent 守卫）三处共用，修复 **WS 重连后详情回退截断版**（high）。
3. **任务切换视图状态全重置**：`App.vue:711` handleSelectTask 补 `isEditingTopic=false`、`editingTopicValue=''`（防任务 A 编辑文本 PATCH 到任务 B，high）；`TaskPartsPanel.vue:17` expandedPart 按任务 id 重置；`App.vue:879` expandMultipartSummary await 后任务身份重校验（对齐 copyContent/downloadContent 模式）。
4. **summary tab 按需加载补齐**：`useTaskViewModel.ts:609` 摘要 tab 增加与 transcript 同款懒加载 watch（hasContent 判定 + id/版本守卫）——修复断线后总结 tab 长期显示截断版 `_summary_overview`（medium）。
5. **quality 死绑定**：删除 `App.vue:985` v-model:quality 绑定 + composable 中 quality 状态；提交 payload 处改显式常量 `'audio_only'`（行为等价，Q7 已定删除方案）。

**public seam（保持不变）**：App.vue 对外 props/emits 签名；useTaskViewModel 导出签名。守卫与净化均为内部实现。

**测试计划**：red 阶段 = 缺陷复现测试——重连对账后完整 summary 保留；selectTask 后编辑态重置；含 `<img onerror>` 的 summary 渲染后被净化；摘要 tab 首次显示完整版而非截断版。

**验收**：上述缺陷测试转绿 + 全量 vitest/vue-tsc/build。

### P2 网络生命周期

**范围**：
1. **WS 加固**（`useTaskViewModel.ts:753-761`）：`disposed` 标志（onUnmounted 置位，重连定时器先检查）；指数退避 3s→6s→12s→封顶 30s（onopen 复位）；onerror 不主动 close（onclose 兜底，消除双调度）；onmessage `JSON.parse` try/catch（失败 console.warn + 跳过该帧，`:695`）。

   > **与 fix/realtime-progress 重叠注记（2026-08-13）**：WS 生命周期加固的后端半（30s ping 心跳、`_safe_send` 10s 超时、receive 90s 判半开、broadcast 副本迭代）与前端 ping→pong 应答、60s 轮询兜底已随生产事故修复在 `fix/realtime-progress`（commit `27ae849`）落地。P2 剩余前端生命周期项不变：`disposed` 标志、指数退避 3s→6s→12s→30s、onerror 单调度、JSON.parse 畸形帧防护（F5，已确认 P2 范围，见 §7）、axios 共享实例与 timeout 等。

2. **axios 共享实例**：新建 `src/shared/api/client.ts`（baseURL 统一、timeout 默认 60s、上传请求放宽 10min、拦截器统一错误提取），替换 ~30 处裸 axios 调用——各调用点响应处理语义不变，仅底层换实例。
3. **uploadMaxBytes 随 WS onopen 对账刷新**（`fetchUploadConfig` 并入对账，后端运行期改上限后前端预检不陈旧，`:1088`）。
4. **deleteTask tombstone**：`recentlyDeletedIds` 集合，WS 合并命中即忽略（防已删任务复活，`:1241`）。

> **修订轮注记（2026-08-13，refactor/p2-network-lifecycle）**：
> - **F1**：`/upload` 不再用固定超时（原规划"上传放宽 10min"作废）——axios/XHR timeout 为整请求总时长（不被 onUploadProgress 重置），600s 会误杀 <3.4MB/s 慢链路上传；调用点显式 `timeout: 0` 保持旧行为，依赖进度条可见性 + 后端 Content-Length 预检/流式 413 + M3 断连核对三层兜底。
> - **S1**：onerror 排定 ~5s 兜底重连（与 onclose 退避共用同一定时器槽位互斥，后到者作废）——防"只发 error 不发 close"环境重连永久停滞。
> - **S3**：重连定时器与墓碑清理定时器 id 留存，onUnmounted 统一 clearTimeout（wsDisposed 双检查保留）。
> - **S2/S4/S5 确认不修**：握手挂起看门狗（环境性非回归，不动）；对账重复 fetchTasks/fetchQueueSnapshot（无害幂等，不动）；墓碑过期幽灵复活（后端删除后广播残留的证据近零，且 60s 过期窗口已收敛，不动）。

**public seam**：useTaskViewModel 导出不变；各 axios 调用点语义不变。

**测试**：fake timers 断言退避序列 3s/6s/12s 与卸载后不重连；畸形报文单帧不中断后续消息；删除后同任务广播不复活。

### P3 收敛（状态映射 + 工具函数）

1. **`src/shared/utils/taskStatus.ts` 单源映射**：`getStatusLabel/getStatusClass/getStatusIcon` 三表合一（TaskStatus 枚举驱动）——Sidebar `:436-504`、TaskInfoModal `:43-65`、TaskPartsPanel `:22-39`（改用 TaskStatus 类型，替换字符串字面量）三处改单点引用。
2. **formatters 统一**：`formatFileSize` 补 GB 档（Sidebar `:259-263` 无 GB 档 vs LocalFolderSelector `:21-26` 有——收敛进 utils/formatters.ts）；`formatDuration` 单一策略（formatters.ts 英文 / TaskPartsPanel `:51-58` 'X分XX秒' / BilibiliPartsSelector HH:MM:SS 三种收敛）。

**测试**：taskStatus 全枚举覆盖断言（types.test.ts 风格，新增 TaskStatus 值必测）；formatFileSize 档位边界（1023MB / 1GB / 2GB）。

**验收**：同一枚举值 → 三处映射结果行为等价（对比断言）。

### P4 类型契约

1. `CreateTaskRequest` 补 `bilibili_parts?: BilibiliPartsConfig`（`types.ts:81-87`）；`submitTaskWithParts` 改用该类型（审计发现类型检查对 B 站分 P 提交完全失效）。
2. 新增 `ReTranscribeRequest` / `ReSummarizeRequest`（`reTranscribe:1285`、`reSummarize:1257` 改用类型，消除内联裸对象；generate_topic 契约注明"缺省沿用任务已存值"）。
3. `TaskPart` 类型强化（updated_at 等字段；e2e 27 字段类型副本改从 src/types 导入）。
4. `types.test.ts` 补形状断言。

### P5 死代码与工程残留

1. **删除文件（清单 §8 交用户确认后执行）**：`TaskHeader.vue`（93 行，零引用，与 TaskMetaCard 主题编辑逻辑重复）、`HelloWorld.vue`（脚手架遗留）、脚手架 `vue.svg`（如存在）。
2. **console.log 清理**：Toast.vue `:48/53/59`、useToast.ts `:67/78`、useMarkdownTheme `:86/89/117/160`、useSummaryImageExporter `:368/638-640`；WS 连接/断开关键日志保留并降级为 debug 条件输出。
3. **双 ToastContainer 单例**（App.vue `:929-937`）：合并为单一容器，position 响应式切换（隐藏实例不再绑定 hover 事件，防隐藏实例 pauseToast 副作用）。
4. **dev proxy**：`vite.config.ts` 补 `/bilibili`、`/local-path`、`/local-folder` 三前缀（本地开发 B 站分 P 检测/本地路径/文件夹扫描必坏，medium）+ 默认 target `8000→21001` + 补 `.env.example` 文档化 `VITE_API_BASE_URL/VITE_WS_BASE_URL/VITE_DEV_API_TARGET`。
5. **git 追踪清理**：`frontend/.vite/deps` 4 个 vite 预构建旧缓存文件 `git rm --cached` + `.gitignore` 补 `.vite/`、`test-results`；`frontend/.ruff_cache` 清理（确认身份：纯缓存，无业务价值）。

**测试**：全量 vitest 为行为网；ToastContainer 单例 DOM 断言。

### P6 结构拆分（纯搬移，新组件直接落位 features/）

1. **Sidebar（1625 行）拆片**：
   - `features/upload/components/UploadForm.vue`（提交三通道 + 大小预检）
   - `features/task/components/TaskList.vue`、`TaskSearch.vue`
   - `features/settings/components/SettingsFormLlm.vue` / `SettingsFormTranscription.vue` / `SettingsFormSummarization.vue`（props+emit，Sidebar 面板与 SettingsModal 双入口复用——Q8 语义以弹窗为准，夹逼与字段统一，消灭 400 行重复与漂移）
2. **TaskContentArea（785 行）拆片**：`features/transcription/components/MermaidBlock.vue`（命令式 DOM 封装，renderVersion 防竞态保留）、`MarkdownContent.vue`；markdown 编译管线（App.vue `:782-789` 自定义 renderer + postProcessCompiledMarkdown）下沉为 `features/transcription/useMarkdownCompile.ts`。
3. **App.vue 编排下沉**（部分）：useMultipartSummary 等可抽 composable 先行（App 1164 行本包只减不增）。

**测试**：seam 契约测试（拆出组件的 props/emits 面）+ 既有全量 vitest 为网；playwright 冒烟（提交/列表/设置弹窗/内容展示）。

**验收**：行为等价（冒烟 + 全量测试）。巨型组件（Sidebar/SettingsModal）补首批组件级单测（TDD 模板，工具已就绪：@vue/test-utils + happy-dom）。

### P7 composable 域拆分（B 阶段第一个包，本分支只定规格不实施）

- `useTaskViewModel`（1323 行）→ `features/{task,upload,settings}/state.ts` + `shared/ws.ts`；App.vue 只做装配。
- 前置：P1/P2 守卫语义固化；24 个 vitest 文件按域切分。
- 规格含：各域 state 的导出签名（public seam）、api adapter 注入方式、测试迁移清单。

---

## 4. 子代理执行模板（每个包统一使用）

```
【数据保护（强制）】
- 禁止 rm/unlink/os.remove/shutil.rmtree/mv 项目根任何文件（除非本包明确创建且明确允许清理的临时产物）；
  禁止 git clean（全变体）、git reset --hard、git checkout .（还原用 git checkout -- <明确列出的文件>）
- 禁止删除任何 .db/.sqlite/.json 数据文件与备份；删除"杂散文件"前必须确认身份
- 子代理不得自行决定清理项目根文件；删除/改名清单交主流程确认后执行
- 临时脚本/中间产物放会话 job tmp 目录

【包规格】<§3 对应包的 范围 / public seam / 测试计划 / 验收>

【任务】请使用 TDD，先确认 public seam，再按 red-green-refactor 实现：
1. 先确认 public seam：读代码，列出本包公共 API 面（组件 props/emits/defineModel 签名、
   composable 导出、TS 类型），写入 seam 规格——声明哪些必须保持不变
2. TDD 三色循环：red（写表达目标行为的测试，跑红并确认失败原因正确）
   → green（最小实现）→ refactor（保持全绿）
3. 行为保持：正常路径行为等价；缺陷修复仅限错误路径；禁止顺手改 UX
4. 验证：uv run vitest（frontend）、vue-tsc -b、npm run build 全过

【产出】seam 规格 + 测试变更清单 + diff + 验收自检（逐条打勾）
【边界】删除/改名文件列清单交主流程；不得自行执行任何文件清理
```

---

## 5. 质量门与流程

- **每包完成标准**：① vitest 全量通过；② `vue-tsc -b` + `npm run build` 通过；③ code-reviewer 子代理对抗验证（阻塞项清零——重点：空结果/异常吞没、竞态、边界输入、seam 破坏）；④ 定向 e2e 按主题批次执行（21001 + playwright），相邻模式不破坏。
- **⚠️ 类型门禁（P4 起强制）**：根 tsconfig 是 solution-style（`files: []` + references），`vue-tsc --noEmit`（无 `-b`）**不检查任何文件、恒 exit 0**——P1–P3 的 `--noEmit` 验收环节实际未生效（真实门禁在 `npm run build` 的 `vue-tsc -b` 内一直生效，故无类型回归；教训记录于 P4 批次）。验证一律显式 `vue-tsc -b`。
- **PR 流程**（Q5B）：每包 GitHub PR → merge 进 `refactor/frontend-design`（回滚点 = 每个 merge commit）；每主题批次结束时汇总给用户确认；A 阶段整体完成后 PR → public。
- **前端改动必须 `npm run build` 重建 dist**（21010 静态包约束，CLAUDE.md 强制）。

## 6. 依赖变更

- 新增 `dompurify`（P1，2026-08-13 用户批准）。
- 其余零新增依赖（组件级 TDD 工具已就绪：@vue/test-utils、happy-dom、vitest、playwright）。

## 7. Backlog（C 阶段 UX / 后续）

- FloatingToolbar group-hover 下拉触屏不可用、桌面隐藏态交互混淆
- 多文件提交部分成功不可见（allSettled + toast 汇总，`useTaskViewModel.ts:1017`）
- 截断版总结的"加载完整总结"提示
- 菜单项文案统一（FloatingToolbar / TaskInfoModal / Sidebar）
- Mermaid 渲染失败降级展示（`.ss-mermaid-error` 样式已存在未使用，`TaskContentArea:449-453`）
- updateTaskTopic 乐观回显（`:1311`，WS 死时 UI 旧主题）
- 分 P 详情缓存条件放宽（`:485-488`，无内容字段响应每次重请求）
- 错误 toast 去重限流（挂载期最多 5 连弹，App.vue:764）
- 大型组件组件级单测补全（搜索/设置表单/高亮/mermaid/Workbench 手势）
- 引入 eslint（或 biome）工具链（待用户定，不在 A 阶段）
- 任务分 P 失败降级不拖垮详情加载（`:569` Promise.all 解耦）
- 双 ToastContainer 已入 P5；错误提取已入 P2；身份守卫已入 P1
- 无总结任务每次选中重复 GET include_content=true（code-reviewer S-1：watch 触发前查 taskFullContentCache 定论跳过）
- 非分P任务超长总结（>12000 字）总结 tab 永久截断无入口（code-reviewer S-2：`summary.length >= 12000` 时也显示展开入口）
- XSS 向量 e2e 用例（img onerror / script / javascript: href / svg xlink / style url()）纳入后续 e2e 批次（code-reviewer S-3）
- WS onmessage 畸形帧防护（非 JSON 帧 JSON.parse 抛错不崩溃）——code-reviewer F5 确认 P2 范围，fix/realtime-progress 分支不做，待本 backlog 排期
- e2e/ 无 tsconfig 项目覆盖（tsconfig.app.json 仅 src/**），vue-tsc -b 不检查 spec——类型单源化收益未兑现；与 P5 的 vitest 误收录处理绑定（vitest.config.ts exclude e2e/ + 补 tsconfig.e2e.json 或 e2e 类型检查步骤）（code-reviewer P2-1，2026-08-14）
- `submitLocalPath` 已随 P4 修订类型化（LocalPathCreateTaskRequest）——P2-2 已落地，此条关闭

## 8. 文件变更清单（删除/改名，需用户确认后执行）

| 操作 | 路径 | 身份判断 |
|---|---|---|
| 删除 | `frontend/src/components/TaskHeader.vue` | 93 行，全仓零引用，功能已被 TaskMetaCard 取代（审计确认） |
| 删除 | `frontend/src/components/HelloWorld.vue` | 脚手架遗留，零引用 |
| 删除 | `frontend/public/vue.svg`（如存在） | 脚手架遗留，零引用 |
| git rm --cached | `frontend/.vite/deps/*`（4 文件） | vite 预构建旧缓存（4 月产物），无业务价值 |
| 清理 | `frontend/.ruff_cache` | 后端 ruff 缓存误落前端目录，纯缓存 |

> 每包实施前若有新增删除项，由子代理列清单 → 主流程确认 → 执行。
