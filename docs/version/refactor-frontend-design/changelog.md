# Refactor: 前端设计重构 — Change Log

**Branch**: `refactor/frontend-design`（基于 `public @ 7fbab68`，== origin/public）
**Date**: 2026-08-14

## Change Log

- 2026-08-18: **bugfix: B 站分P探针网络类失败降级为单P提交，不再误报 422**（fix/bilibili-dns-422 → PR #12）。
  - **根因**：`POST /tasks/` 分P探针（tasks.py）在瞬时 DNS/网络故障（如 `Temporary failure in name resolution`）时抛异常，
    被无差别转 422 业务错误（"无法确认 B 站分P信息"），状态码与文案均误导；前端 video-info 失败→静默降级提交→后端探针同样失败→422 放大问题。
  - **P0（tasks.py）**：探针异常按网络类/确定性分类——网络类（aiohttp 类型判定为主 + httpx/消息关键词兜底，见下）
    降级为整视频单P语义继续创建任务（不 422，DNS 恢复后由下载器重试），warning 日志带 video_url；确定性错误（无效 BV/视频不存在/业务性拒绝）与多P检测保持 422。
  - **防御（bilibili.py /bilibili/video-info）**：同样分类，网络类失败返回 200 `is_multi_part=false`（title 为空），确定性错误保持 500。
  - **对抗评审修订**（P1-1/P1-2/P2-3，2026-08-18 追加 commit）：
    - **P1-1 分类器真实异常面**：评审实测 bilibili_api 客户端注册顺序为 httpx → aiohttp → curl_cffi，curl_cffi 未装且 aiohttp 可用时
      `selected_client="aiohttp"`——原实现 `isinstance(exc, httpx.TransportError)` 在生产零命中，事故降级全靠关键词兜底碰巧命中（该路径零测试）。
      修订：新增 aiohttp 类型判定（`ClientConnectionError` 覆盖 ClientConnectorError/ClientOSError/ServerDisconnectedError，`ClientPayloadError`）；
      `NetworkException status>=500`（CDN 5xx 典型瞬时故障）归网络类、`<500`（412 风控/404）确定性；关键词兜底前显式排除 `ApiException` 业务异常
      （含 ResponseCodeException，防未来关键词误命中）；docstring 前提修正。
    - **P1-2 loguru exc_info 误用**：`logger.error(..., exc_info=True)` 中 exc_info 作为 format kwargs 被**静默丢弃**（traceback 不记录；
      异常消息含 `{}` 时反而触发 format IndexError）。改 `logger.opt(exception=e)`。此前 changelog 的"f-string + 大括号会崩"机制描述更正
      （零参数 f-string 不触发 format，旧 tasks.py 该行实际不崩）。
    - **P2-3 确定性错误文案区分**：确定性错误 → "无法确认 B 站分P信息，请检查链接是否正确后重试。"（原"请先在分P选择器中…"误导，
      用户无法选择）；多P 文案保留原文案。
  - **测试**：新增 17 用例（TDD 先红后绿；真实异常面：aiohttp.ClientConnectorError/ServerDisconnectedError 构造断言、NetworkException
    502 降级 201/200、**412 风控保持 422/500 回归**、ApiException 业务文案含网络关键词不误分类、httpx 用例标注兜底类型）；
    顺带修复 `test_create_task_publishes_task_created` 环境相关 flake（假 BV 直连真实 api.bilibili.com，结果随网络状态漂移→探针打桩）。
  - **验证**：pytest 289+新用例全绿（5 torch 相关用例因环境缺 torch 为既有失败，与本次无关）；ruff/basedpyright 改动文件零新增问题。
  - **Backlog（未实施）**：P2-1 多P视频 + DNS 故障降级后分P1 可能静默部分完成（probe_degraded 标记 follow-up）；
    P2-2 bilibili_api sync 探针阻塞事件循环（30s 卡顿，异步直连/收紧超时 follow-up）。
- 2026-08-16: **P7 composable 域拆分完成**（refactor/p7-implement → PR #11，行为保持 Q6，逐逻辑块等价搬移）。
  - **useTaskViewModel（实测 1523 行）按域拆分**：`features/task/state.ts`（853 行：列表/选择/详情/分P/重试/删除/重转录/重总结/重下载/改主题/per-task 内容缓存/懒加载/墓碑/轮询兜底）+ `features/upload/state.ts`（470 行：三通道提交/进度/取消/上传配置/本地路径批量/B站多P/URL 提取/env 判定 + `DEFAULT_MAX_UPLOAD_BYTES`）+ `features/settings/state.ts`（342 行：LLM/转录/总结三表单保存测试/vibevoice 扫描启停/校验/Cookie）+ `shared/ws.ts`（231 行：连接/指数退避 3s→30s 封顶/onerror 5s 兜底槽位互斥/心跳 ping-pong/畸形帧防护/事件总线）。
  - **App.vue 只做装配**：三域 state + ws 订阅接线 + 生命周期宿主（D4：onMounted 并发 7 fetch + ws.connect + startPolling；onBeforeUnmount ws.dispose + task.dispose）；模板消费面零改动。
  - **D1**：summaryMode/generateTopic 归 upload 域；`reTranscribe(taskId, mode?)` 新增可选参数（替代内读 summaryMode），App 装配层 `reTranscribe(taskId, upload.summaryMode.value)` 显式传参——task 对 upload 零 import，依赖图无环。
  - **D2**：error→toast 装配层三路独立 watch 聚合（等价平移单一 error ref 语义；规避数组 watch 的旧文案遮蔽新错误偏差）。**有意接受的行为差异**（对抗评审 P1-1，2026-08-16）：同一 tick 内多域先后置 error 时，旧版单一 error ref 仅弹 1 条（后值遮蔽前值），新版最多弹 3 条（各域错误各自呈现，信息更完整，非信息丢失）。
  - **D3**：全部迁移后 grep 零 import 残留，删除 useTaskViewModel.ts（`chore(p7): remove useTaskViewModel after domain split (zero refs)`）。
  - **api adapter**：规格 §4 方案 A——模块级单例 `apiClient` 沿用，`useTaskState(options?: { api? })` 可选签名保留为 seam 扩展。
  - **测试**：15 个 useTaskViewModel.* 测试 + sharedApiClient 按域迁移（断言原样保留）；新增 seam 契约测试 33 用例（TDD 先行）。`App.p1Defenses/App.toastContainer` 零改动全绿。
  - **验证**：vitest 397 全绿（基线 364 + 新增 33）；`vue-tsc -b` 通过；`npm run build` 通过（dist 重建）；playwright 冒烟 19/19（21001：P6 场景复用 + 提交 payload 接线 + WS 活性 + reTranscribe D1 显式 mode 双路径）。
  - **流程档位**：T2（子代理实施）。
- 2026-08-15: **P6 结构拆分完成**（refactor/p6-structure → PR #10，行为保持 Q6，纯搬移）。
  - **Sidebar（1586 行）拆片**：UploadForm（提交三通道+大小预检，props+emit 化）、
    TaskList / TaskSearch（+ 域内 taskDisplay.ts 共用展示辅助）、SettingsForm×3 落位
    features/；Sidebar 剩余部分引用拆出组件并转发事件（public seam 不变，selectTask
    后关闭侧栏保留）。
  - **设置双入口复用（Q8，弹窗语义为准）**：SettingsFormLlm/Transcription/Summarization
    由 SettingsModal 与 Sidebar 面板共用，消灭约 400 行重复与漂移——转录表单补齐
    transcriber_type/vibevoice_*/路径校验/浏览器读 Cookie；Agent 表单删除 Sidebar
    旧版夹逼（仅保留 max_agent_value_chars≥100）；LLM 表单补齐 extra_headers。
    Sidebar 新增 4 个加性转发 emit。注：Sidebar 内联面板为休眠 UI（isSettingsPanelOpen
    恒 false，无打开入口），活动入口为 SettingsModal。
  - **TaskContentArea（797 → 188 行）拆片**：MermaidBlock（命令式 DOM 封装，
    display:contents 宿主 + renderVersion 防竞态）、MarkdownContent（渲染容器：
    标题收集/IntersectionObserver、搜索高亮、XSS DEV 兜底断言）；.ss-mermaid-* 样式
    随迁（含 C 阶段 backlog 的 .ss-mermaid-error 降级样式）。
  - **markdown 编译管线下沉**：useMarkdownCompile.ts（marked renderer +
    DOMPurify 单点净化 + postProcess 的 compileMarkdownText 纯函数 + 多P 分页状态机
    useMultipartSummary 本质，120ms 防抖与身份守卫保留）。
  - **App.vue 编排下沉**（1225 → 947 行，-22.7%）：useMarkdownCompile +
    useSummaryImageWorkbench（一键成图工作台：持久化/预览序列防竞态/脏标记/分页）。
  - **验证**：vitest 360 全绿（基线 268 + 新增 92：seam 契约 9 文件 84 用例 +
    Sidebar 任务筛选交互 3 + SettingsModal 设置保存交互 5）；vue-tsc -b 通过；
    npm run build 通过；playwright 冒烟 16/16（21001：提交表单/任务列表/设置弹窗
    三 tab/内容区原文 tab，无未捕获页面错误）。
  - **对抗评审修订（2026-08-15，P0 无）**：P1-1 TaskSearch 恢复 ASR 分片计数标签
    （抽入 taskDisplay.ts 同源）；P1-2 SettingsModal 三表单 v-if 链改 v-show 常挂载
    （切 tab 保留未保存输入）；P2-3 弹窗打开即按需拉取 VibeVoice 服务状态（v-show
    下 watch 与拆片前根层语义同时机）；P2-4 App 传入单实例 exporter 共享预览/导出
    canvas 缓存；P2-5 theme tab 条件链变化（不可达，修复旧隐藏 bug）记录。
    TDD 新增 4 用例（先红后绿），vitest 364 全绿。
  - **流程档位**：T2（子代理实施）。
- 2026-08-15: **P5 死代码与工程残留完成**（refactor/p5-cleanup，行为保持 Q6）。
  - **删除**（清单 §8，用户确认后执行）：TaskHeader.vue（零引用，TaskMetaCard 取代）、
    HelloWorld.vue、src/assets/vue.svg（脚手架）；.vite/deps 4 个预构建缓存 git rm +
    .gitignore 补 `.vite/`/`test-results`；.ruff_cache、根目录 20 个 `<MagicMock*`
    loguru 测试日志残留、0 字节 .codex。recovered_urls.txt 按规保留未动。
  - **console.log 清理**：Toast.vue/useToast/useMarkdownTheme/useSummaryImageExporter
    共 13 处调试日志删除（连带清理仅被日志引用的死变量）；WS 连接/断开日志保留并降级为
    DEV 条件 console.debug（import.meta.env.DEV 可摇树）。console.error/warn 未动。
  - **双 ToastContainer 单例**：App.vue 两处 CSS 响应式实例合并为单实例 +
    matchMedia 驱动 position 切换（桌面 bottom-right / 移动 bottom-center，断点对齐
    Tailwind md）；ToastContainer props/事件面不变。TDD：新增
    src/__tests__/App.toastContainer.test.ts 3 用例（单实例断言 + 双视口位置断言），
    先红后绿。
  - **dev proxy**：vite.config.ts 补 /bilibili、/local-path、/local-folder 三前缀
    （本地开发 B 站分 P/本地路径/文件夹扫描此前必坏）；默认 target 8000→21001
    （VITE_DEV_API_TARGET 覆盖保留）；新增 .env.example 文档化三个 VITE_* 变量。
  - **vitest e2e 误收录修复（P4 P2-1）**：vitest.config.ts exclude `e2e/**`（展开
    defaultExclude 保留默认排除）；新增 tsconfig.e2e.json 入根 references——
    vue-tsc -b 真实检查 e2e spec，类型单源化收益兑现。基线 1 failed file → 全绿。
  - **验证**：vitest 38 文件 268 用例全绿（265 基线 + 3 新增）；vue-tsc -b 通过；
    npm run build 通过；eslint（项目无配置，跳过）。
  - **流程档位**：T2（子代理实施，清理清单用户已确认）。
- 2026-08-14: **P3 收敛完成**（refactor/p3-converge → 待合并进 refactor/frontend-design）。
  - **状态映射单源化**（src/shared/utils/taskStatus.ts，types.ts const 对象 + 联合类型驱动）：
    getStatusLabel/getStatusClass/getStatusIcon 三表合一，Sidebar/TaskInfoModal/TaskPartsPanel
    三消费点改单点引用（Sidebar 状态筛选标签同源派生，消除第 4 处重复）；分P级
    getPartStatusLabel/getPartStatusClass 一并收敛（'PROCESSING' 为后端已停产的遗留值，
    保留兜底映射防旧数据回归）。各枚举取值输出与旧实现逐值一致。
  - **formatters 统一**（utils/formatters.ts）：formatDuration 三处实现合一（clock 默认 /
    chinese / floor+不补零，最小 options 驱动，各调用点输出逐字符不变，floor 假设整数秒）；
    formatFileSize 收敛为 B/KB/MB/GB 四档（补 GB 档）。
  - **唯一预期行为变化**：Sidebar 文件大小 ≥1GB 由错档 '1024.0 MB' 修正为 '2.00 GB'
    （plan 明确修复，Sidebar.upload 断言同步更新）。
  - **对抗验证**（code-reviewer）：通过（无 P0/P1），4 条 P2 建议已落地（floor 整数秒
    假设注释、invalid/null 与整数秒锁定断言、'枚举'措辞精确为 const 对象 + 联合类型、
    changelog 本条目）。
  - **测试**：vitest 259 全绿（新增 taskStatus/formatters 共 23 用例：全取值覆盖——
    新增不同 value 未映射即红、旧实现输出对拍、消费点 mount 断言）；vue-tsc/build 通过；
    e2e spec 被 vitest 误收录为基线既有失败（与 P3 无关，主流程另行处理）。
  - **流程档位**：T2（子代理实施 + 对抗验证）。
- 2026-08-13: **P1 防线加固完成**（refactor/p1-defenses → PR #5，合并进 refactor/frontend-design）。
  - **XSS 单点净化**：App.vue markdown 管线出口（marked 编译后、postProcess 前）加 DOMPurify.sanitize
    `{ FORBID_ATTR: ['style'], USE_PROFILES: { html: true } }`（引入 dompurify 3.4.13，用户已批准）；
    TaskContentArea v-html 渲染层 DEV-only 兜底断言（import.meta.env.DEV 可摇树）。Chromium 实测：
    onerror/style/svg/script 剥离、class/mermaid pre/table/img/h1-h6 保留。
  - **内容合并守卫统一**：mergeTaskWithPreservedContent（hasContent 守卫 + preserveContent 选项）
    三处共用（fetchTasks/selectTask/WS task_update 广播 authoritative）；修复重连后详情回退截断版。
  - **I-1（code-reviewer 发现回归）**：latest_modified_at 作内容版本信号——双开标签页场景断线期间
    内容被重置时，fetchTasks 合并与 selectTask 缓存恢复分支均失效陈旧缓存（taskFullContentCache.delete
    + bumpTaskContentVersion），由懒加载 watch 补拉新内容；isTaskCacheUsable 缓存版本校验。
  - **任务切换视图状态全重置**：handleSelectTask 退出主题编辑态（防 A 文本 PATCH 到 B）；TaskPartsPanel
    taskId prop 重置 expandedPart；expandMultipartSummary await 后身份重校验。
  - **I-2**：详情接口失败后 summary 置 null 触发懒加载补拉（不卡死在 undefined）；types.ts 加宽
    `Task.summary?: string | null`（加性兼容）。
  - **summary tab 懒加载补齐**：与原文 tab 同款 watch（PROCESSING/undefined 跳过），修复断线后
    总结 tab 长期显示截断版/空内容。
  - **quality 死绑定清理**：移除 v-model:quality + composable quality 状态，payload 显式常量
    'audio_only'（行为等价）。
  - **S-4/S-5（顺带）**：TaskPartsPanel refreshKey prop 联动重试收起展开区；saveTopic await 后
    任务身份守卫（防误关新任务编辑态）。
  - **对抗验证**（code-reviewer）：首轮无阻塞项但 3 Important（I-1/I-2/I-3）+ 2 顺带全部修复；
    S-1/S-2/S-3 排期 C 阶段/e2e 批次（入 plan.md backlog）。
  - **测试**：vitest 200 全绿（新增 21 用例含 7 个真 RED 复现）；vue-tsc/build 通过；定向 e2e
    （21001 + playwright）：编辑态切换重置（残留 0）、编辑文本不残留、同任务重选内容保留，全部通过。
  - **流程档位**：T2（子代理实施 + 对抗验证），包规模约 6 小时。
- 2026-08-13: **全面前端设计审计 + 重构计划确立**（grilling 会话收敛）。
  - **审计**：workflow 7 子代理（5 路分层映射 + 设计隐患/数据流双视角综合，~590k tokens）。
    架构事实：无路由/无 Pinia Vue 3.5 SPA，App.vue（1164 行）+ useTaskViewModel（1323 行）双中枢，
    19 组件全走 props/emits；三层防竞态设计（内容缓存 Map + in-flight 去重 + 版本计数 + 身份重校验）为高质量防御。
  - **High 级发现**：v-html 直渲 LLM 内容 XSS 面（marked v17 无 sanitize）；设置双入口行为漂移
    （Sidebar 有夹逼/弹窗无、字段不对称）；v-model:quality 死绑定；fetchTasks/WS 合并无 hasContent 守卫
    （重连后详情回退截断版）；WS 无 disposed/退避/防护；任务切换编辑残留（A 文本可 PATCH 到 B）。
  - **决策**（grilling Q1–Q12）：A 阶段设计债清理 7 包（P1 防线加固 → P2 网络生命周期 → P3 收敛 →
    P4 类型契约 → P5 死代码工程残留 → P6 结构拆分 → P7 composable 域拆分[B 阶段]）；严格行为保持；
    目标架构 features/ + shared/（Vue 惯用 DDD 映射）；DOMPurify 引入获批（唯一新依赖）。
  - **产出**：`docs/version/refactor-frontend-design/plan.md`（完整计划：工作包/seam/测试/验收/
    子代理 TDD 模板/删除清单）。
  - **流程档位**：规划会话（无代码变更），grilling 收敛后建分支。
