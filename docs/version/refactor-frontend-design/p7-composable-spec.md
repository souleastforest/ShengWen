# P7 composable 域拆分规格（只定规格，不实施）

**Branch**: `refactor/frontend-design`
**Date**: 2026-08-16
**状态**: 规格冻结，待实施（B 阶段第一个包）
**范围**: `useTaskViewModel`（实测 1523 行，plan.md 记 1323 行为 P6 前基线）→ `features/{task,upload,settings}/state.ts` + `shared/ws.ts`；App.vue 只做装配。**本规格不涉及任何业务代码改动。**

---

## 1. 现状地图

### 1.1 composable 全景（frontend/src/composables/ + features/ 下）

| 文件 | 行数 | 导出面（export） | 消费点（生产代码） | 形态 |
|---|---|---|---|---|
| `composables/useTaskViewModel.ts` | 1523 | `useTaskViewModel()`、`__resetTaskContentCaches`、`DEFAULT_MAX_UPLOAD_BYTES` | 仅 `App.vue:4`（import）、`App.vue:86`（调用）；另有 16 个测试文件直接 import | 巨型单组合式：state + actions + WS + 生命周期全内嵌 |
| `composables/useToast.ts` | 142 | `useToast()`、类型 `ToastType`/`ToastItem` | `App.vue`、`ThemeSelector.vue`、`Toast.vue`、`ToastContainer.vue`、`features/transcription/composables/useSummaryImageWorkbench.ts` | **模块级单例 refs**（`toasts` 在模块顶层）——共享状态先例，P7 各域 state 可参考 |
| `composables/useMarkdownTheme.ts` | 235 | `useMarkdownTheme()`、`getCurrentTheme()`、`getThemeById()`、类型 | `features/transcription/components/MarkdownContent.vue`、`ThemeSelector.vue` | 纯函数 + 组合式 |
| `composables/useMermaidViewer.ts` | 448 | `useMermaidViewer(modalRef)` | 仅 `App.vue` | 命令式 DOM 封装 |
| `composables/useSummaryImageExporter.ts` | 1296 | 类型族 + `createDefaultSummaryImageExportSettings()` + `useSummaryImageExporter()` | `App.vue`、`SummaryImageWorkbenchModal.vue` | 实例级（renderCanvasCache 实例缓存，P6-P2-4 共享单实例） |
| `features/transcription/composables/useSummaryImageWorkbench.ts` | 254 | `useSummaryImageWorkbench(options)` + 4 个常量数组 | 仅 `App.vue:698` | **options 注入式 seam 先例**（入参 `{selectedTask, topic, compiledMarkdown, summaryImageExporter}`）——P7 各域 state 的接口风格参照 |

> 备注：`features/transcription/useMarkdownCompile.ts`（185 行）同样是 options 注入式（`{selectedTask, fetchTaskFullContent}`），已落地 P6，是"状态 → 组合式"跨域传递的成熟范式。

### 1.2 `useTaskViewModel` 内部逻辑域分解（实测 1523 行）

| 功能域 | 行区间 | 近似行数 | 关键内容 | 依赖 |
|---|---|---|---|---|
| **task 域**（列表/选择/详情/分P/重试/删除/重转录/重总结/重下载/改主题/内容缓存/懒加载） | 103–175（模块级缓存与合并守卫）、314–358、530–802、return 内联 `copyContent`(1372–1425)/`deleteTask`(1426–1449)/`reSummarize`(1450–1464)/`reTranscribe`(1465–1498)/`reDownloadAudio`(1499–1511)/`updateTaskTopic`(1512–1521) | **~540** | per-task 内容缓存 `taskFullContentCache`/`taskFullContentPending`/`taskContentVersion`、`mergeTaskWithPreservedContent` 守卫、`hasContent`、`PROCESSING_STATUSES`、墓碑 map、selectTask 往返恢复、两个懒加载 watch、下载/复制 | `apiClient`、`../types`；跨域读 `summaryMode`/`generateTopic`（见 §4） |
| **upload 域**（三通道提交/进度/取消/上传配置/本地路径批量/B站多P/URL 提取/env 判定） | 37–101（`fallbackCopyToClipboard`/`extractFirstUrl`/`isBilibiliUrl`）、178–195（baseUrl/loopback env）、360–528、1134–1251 | **~400** | `submitTask`/`submitLocalPathTask`/`uploadFile`（onUploadProgress + timeout:0）/`cancelSubmitting`/`fetchUploadConfig`/`submitLocalPathTasks`/`submitTaskWithParts`/`checkBilibiliVideoInfo`/`checkLocalPath`/`scanLocalFolder` | `apiClient`、`isCanceledRequest`、`getAxiosErrorMessage`；不依赖任何 task 状态 |
| **settings 域**（LLM/转录/总结三表单保存与测试、vibevoice 扫描启停状态、model path 校验、浏览器读 Cookie） | 219–248（settings 相关 refs + `watch(transcriptionSettings)`）、931–1132 | **~250** | `fetch/updateLlmSettings`、`fetch/updateTranscriptionSettings`、`fetch/updateSummarizationSettings`、`testLlm`、`validateModelPath`、`scanVibeVoiceServices`/`start`/`stop`/`fetchStatus`、`readBilibiliCookieFromBrowser` | `apiClient`、`isAxiosError`；不依赖 task/upload 状态 |
| **WS 层**（连接/重连退避/onerror 兜底/心跳/事件分发/轮询兜底/墓碑定时器） | 250–312（ws 变量 + 重连调度 + 墓碑 + 轮询变量）、804–929（connectWebSocket + onmessage 分发）、1253–1297 中 WS/轮询生命周期 | **~200** | 退避 3s→30s、onerror 兜底 5s 槽位互斥、ping/pong、`task_update`/`progress_update` 内联分发、60s 轮询兜底、`wsDisposed` 卸载防护 | 仅浏览器 WebSocket；**事件分发与 task 状态 refs 强耦合**（onmessage 内联写 tasks/queues/selectedTask） |
| **装配**（return 对象 + onMounted/onUnmounted 编排） | 1299–1523（return）、1253–1297（生命周期） | ~250 | 63 个导出键；onMounted 并发 8 个 fetch + ws.connect + 轮询；onUnmounted 全量清理 | 拆后归各域，App.vue 保留最小装配 |

**跨域耦合盘点（P7 拆分必须处理的全部数据流）**：
1. `reSummarize`/`reTranscribe`（task 域）读取 `summaryMode`/`generateTopic`（upload 域）——`reSummarize` 已有显式 `mode?` 参数，`reTranscribe` 为内读（1466–1480）；
2. `deleteTask`（task 域）写入墓碑 map，WS 广播过滤依赖它（849）；
3. WS `onopen`（ws 层）回调触发 `fetchTasks`/`fetchQueueSnapshot`/`fetchUploadConfig`/`selectTask`——跨三域的重连对账；
4. 共享单一 `error` ref：App.vue:643 `watch(error)` → toast（拆后需装配层聚合）。

---

## 2. 目标结构

```
frontend/src/
├── App.vue                         # 只做装配：new 三个 state + ws，接线 + 生命周期编排 + error 聚合 watch
├── shared/
│   ├── api/client.ts               # 不动（P2-B 唯一 HTTP 出口，模块级单例 apiClient）
│   ├── utils/taskStatus.ts         # 不动（纯展示函数）
│   └── ws.ts                       # 新增：WS 连接/退避/心跳/事件总线（无业务语义）
├── features/
│   ├── task/state.ts               # 新增：任务列表/选择/详情/分P/重试/删除/内容缓存守卫/墓碑
│   ├── upload/state.ts             # 新增：三通道提交/进度/取消/上传配置/URL 与 B站辅助
│   └── settings/state.ts           # 新增：LLM/转录/总结设置/vibevoice/校验/测试
```

依赖方向（禁止反向与跨域直连）：

```
                    ┌─────────────────┐
                    │     App.vue      │  (装配层：唯一允许跨域接线处)
                    └────────┬────────┘
       ┌─────────────────────┼─────────────────────┐
       ▼                     ▼                     ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ task/state   │   │ upload/state │   │ settings/state│
└──────┬───────┘   └──────┬───────┘   └──────────────┘
       │ (只 import       │ (不 import task)   (不 import task/upload)
       │  types + shared) │
       ▼                  ▼
┌──────────────────────────────────┐
│  shared/  (api/client + ws + utils)│   ← 无业务域依赖，纯下层
└──────────────────────────────────┘
src/types (公共类型层) ← 各域均只读不依赖
```

- **域间数据流一律经"事件（WS）"或"装配层显式传参"**，禁止 `state.ts` 之间互相 import（§4、§5）；
- `features/{task,upload,settings}/state.ts` 均可独立单测：mock 依赖仅 `shared/` 与 `src/types`。

---

## 3. public seam 规格（核心）

> 铁律：**App.vue 的消费面（63 个导出键的键名、类型、语义）必须逐键保持**；组件层（Sidebar/SettingsModal/TaskContentArea 等 props/emit 面）零改动。App.vue 内 `useTaskViewModel()` 一行替换为三行装配 + 显式接线。

### 3.1 `features/task/state.ts`

```ts
import type { Task, TaskPart, SummaryMode, QueueSnapshot } from '../../types'
import type { WsClient } from '../../shared/ws'

export interface TaskState {
  // --- state（全部 Ref，语义与现状逐键一致）---
  tasks: Ref<Task[]>
  queues: Ref<QueueSnapshot[]>
  selectedTask: Ref<Task | null>
  taskParts: Ref<TaskPart[]>
  taskPartDetails: Ref<Record<number, TaskPart>>
  loadingPartIndex: Ref<number | null>
  activeTab: Ref<'summary' | 'transcript'>   // v-model 语义不变（FloatingToolbar/TaskContentArea）
  isSidebarOpen: Ref<boolean>                // v-model 语义不变（Sidebar/遮罩）
  error: Ref<string | null>                  // 本域错误（装配层聚合 watch）

  // --- actions（签名 = 现状 export 面，逐键不变）---
  fetchTasks(): Promise<void>
  fetchQueueSnapshot(): Promise<void>
  selectTask(task: Task): void
  fetchTaskFullContent(taskId: string): Promise<Task>
  fetchTaskParts(taskId: string): Promise<TaskPart[]>
  fetchTaskPart(taskId: string, partIndex: number): Promise<TaskPart | undefined>
  retryFailedParts(taskId: string): Promise<void>
  downloadContent(type: 'summary' | 'transcript'): Promise<void>
  copyContent(type: 'summary' | 'transcript'): Promise<boolean>
  deleteTask(taskId: string): Promise<boolean>
  reSummarize(taskId: string, mode?: SummaryMode): Promise<void>   // 签名不变（已有显式 mode）
  reTranscribe(taskId: string, mode?: SummaryMode): Promise<void>  // ★ 唯一签名变化：新增可选 mode 参数（替代内读 summaryMode，见 §4）
  reDownloadAudio(taskId: string): Promise<void>
  updateTaskTopic(taskId: string, newTopic: string): Promise<void>

  // --- WS 挂接（新增，装配层调用一次）---
  syncWithWs(ws: WsClient): void
}

export function useTaskState(options?: { api?: ApiAdapter }): TaskState
export function __resetTaskContentCaches(): void   // 测试钩子，语义不变
```

**语义不变点（测试断言依赖）**：
- `selectTask` 的 A→B→A 缓存恢复、轻量列表项不得覆盖完整内容（`mergeTaskWithPreservedContent` 默认 preserve）、`latest_modified_at` 陈旧判定；
- 懒加载 watch：`activeTab==='transcript'` 补拉 `include_content=true`；summary tab 三态判定（`undefined` 不抢跑、`PROCESSING_STATUSES` 不触发、null/'' 触发）；
- `deleteTask` 墓碑 60s TTL；`reTranscribe` 的"清空总结确认窗口"语义（content 信号 + 模式信号）；
- `copyContent`/`downloadContent` 的"等待期间切任务即中止"身份守卫；
- WS 广播合并 `{ preserveContent: false }`（广播内容权威，re-transcribe 的 '' 重置必须传播）。

### 3.2 `features/upload/state.ts`

```ts
import type {
  CreateTaskRequest, LocalPathCreateTaskRequest, SummaryMode,
  BilibiliVideoInfo, BilibiliPartsConfig, LocalPathCheckResult, LocalFolderScanResult,
} from '../../types'

export interface UploadState {
  videoUrl: Ref<string>                    // v-model（Sidebar）
  selectedFile: Ref<File | null>           // v-model（Sidebar）
  localFilePath: Ref<string>               // v-model（Sidebar）
  isLocalClient: Ref<boolean>              // env 判定（submitTask 路由 + Sidebar 显示）
  summaryMode: Ref<Exclude<SummaryMode, 'auto'>>   // v-model（Sidebar/UploadForm）
  generateTopic: Ref<boolean>              // v-model（Sidebar/UploadForm）
  isSubmitting: Ref<boolean>               // Sidebar/UploadForm 消费
  uploadProgress: Ref<number>              // 真实进度 0-100
  uploadMaxBytes: Ref<number>              // 与后端 /upload/config 同源
  error: Ref<string | null>

  submitTask(): Promise<void>
  submitLocalPathTask(filePath: string): Promise<void>
  uploadFile(file: File): Promise<void>    // onUploadProgress + timeout:0 + 413 清空 selectedFile
  cancelSubmitting(): void                 // AbortController.abort + isSubmitting/uploadProgress 复位
  fetchUploadConfig(): Promise<void>       // 失败静默回退 DEFAULT_MAX_UPLOAD_BYTES
  submitLocalPathTasks(paths: string[], mode: 'merge' | 'separate'): Promise<void>
  submitTaskWithParts(videoUrl: string, partsConfig: BilibiliPartsConfig, abortSignal?: AbortSignal): Promise<void>
  checkBilibiliVideoInfo(url: string): Promise<BilibiliVideoInfo | null>
  checkLocalPath(filePath: string): Promise<LocalPathCheckResult | null>
  scanLocalFolder(folderPath: string): Promise<LocalFolderScanResult | null>
  isBilibiliUrl(url: string): boolean
}

export function useUploadState(options?: { api?: ApiAdapter }): UploadState
export const DEFAULT_MAX_UPLOAD_BYTES: number   // = 2GB，语义不变
```

**语义不变点**：三通道提交优先级（localhost 本地路径 → 文件 → URL）；`quality: 'audio_only'` 显式常量；`generate_topic` 仅 `summary_mode==='none'` 时发送；payload 取消走 `isCanceledRequest` 静默返回；提交成功后不主动 fetchTasks（WS 通知，行为不变）；`submitLocalPathTasks` merge 模式报"开发中"。

### 3.3 `features/settings/state.ts`

```ts
import type {
  LLMProvider, LLMSettings, UpdateLLMSettingsRequest, TranscriptionSettings,
  UpdateTranscriptionSettingsRequest, SummarizationSettings, UpdateSummarizationSettingsRequest,
  BilibiliCookieFromBrowserResult, ModelPathValidationRequest, ModelPathValidationResult,
  VibeVoiceServiceScanResult, VibeVoiceServiceStatus,
} from '../../types'

export interface SettingsState {
  llmProviders: Ref<LLMProvider[]>
  llmSettings: Ref<LLMSettings | null>
  isUpdatingLlmSettings: Ref<boolean>
  transcriptionSettings: Ref<TranscriptionSettings | null>
  isUpdatingTranscriptionSettings: Ref<boolean>
  summarizationSettings: Ref<SummarizationSettings | null>
  isUpdatingSummarizationSettings: Ref<boolean>
  isReadingBilibiliCookieFromBrowser: Ref<boolean>
  modelPathValidationResult: Ref<ModelPathValidationResult | null>
  isValidatingModelPath: Ref<boolean>
  vibevoiceInferenceMode: Ref<'local' | 'api'>   // 随 transcriptionSettings watch 同步（现 244–248）
  vibevoiceApiUrl: Ref<string>
  vibevoiceServiceStatus: Ref<VibeVoiceServiceStatus | null>
  isScanningVibeVoice: Ref<boolean>
  isStartingVibeVoice: Ref<boolean>
  isStoppingVibeVoice: Ref<boolean>
  error: Ref<string | null>

  fetchLlmProviders(): Promise<void>
  fetchLlmSettings(): Promise<void>
  updateLlmSettings(payload: UpdateLLMSettingsRequest): Promise<LLMSettings>        // 失败 throw + error
  fetchTranscriptionSettings(): Promise<void>
  updateTranscriptionSettings(payload: UpdateTranscriptionSettingsRequest): Promise<TranscriptionSettings>
  fetchSummarizationSettings(): Promise<void>
  updateSummarizationSettings(payload: UpdateSummarizationSettingsRequest): Promise<SummarizationSettings>
  validateModelPath(request: ModelPathValidationRequest): Promise<ModelPathValidationResult>  // 失败返回 valid:false 结果并 throw
  scanVibeVoiceServices(): Promise<VibeVoiceServiceScanResult[]>   // 失败返回 []（不 throw）
  startVibeVoiceService(modelPath: string, port: number, dtype: string): Promise<void>
  stopVibeVoiceService(): Promise<void>
  fetchVibeVoiceServiceStatus(): Promise<VibeVoiceServiceStatus>
  testLlm(): Promise<{ status: string; message: string }>          // 失败 throw + error
  readBilibiliCookieFromBrowser(): Promise<BilibiliCookieFromBrowserResult>  // 成功后内部 refetch transcriptionSettings
  clearModelPathValidation(): void
}

export function useSettingsState(options?: { api?: ApiAdapter }): SettingsState
```

**语义不变点**：各 update 的"失败抛错 + error 置值"双通道（App 的 handle* 依赖 throw 以静默吞）；`validateModelPath` 失败构造 `{valid:false, ...}` 安全视图；`scanVibeVoiceServices` 失败返回 `[]`；cookie 读取成功自动刷新转录设置。

### 3.4 必须保持不变的消费面（零改动清单）

- 全部 17 个 `.vue` 组件的 props/emits/defineModel 面（P6 已定型，本包不触碰）；
- `shared/api/client.ts` 的 5 个导出（`apiClient`/`getAxiosErrorMessage`/`isCanceledRequest`/`isAxiosError`/`DEFAULT_TIMEOUT_MS`）；
- `shared/utils/taskStatus.ts`、`features/task/taskDisplay.ts` 纯函数层；
- `useToast`/`useMarkdownTheme`/`useMermaidViewer`/`useSummaryImageExporter`/`useSummaryImageWorkbench`/`useMarkdownCompile` 全部不动。

---

## 4. api adapter 注入方式

**现状**：`shared/api/client.ts` 模块级单例 `apiClient`（P2-B 收敛的唯一 HTTP 出口，60s 超时；`axios.create` 缺失时回退默认导出——该回退正是 16 个测试文件 `vi.mock('axios')` 工厂无 `create` 的兼容设计，见 client.ts:14-17 注释）。

**候选方案**：

| 方案 | 形态 | 优点 | 缺点 |
|---|---|---|---|
| **A. 模块级单例（推荐）** | 各 state.ts 直接 `import { apiClient } from '../../shared/api/client'` | 零接线噪音；16 个 `useTaskViewModel.*.test` 的 `vi.mock('axios')` 契约原样迁移，测试改写量最小；与 P2-B"唯一出口"约束一致 | state 单测依赖 axios mock 而非真 fake；跨测试共享 mock 需沿用现有 `beforeEach` 重置模式 |
| B. factory 注入 | `useTaskState({ api: fakeApi })`，App 装配层 `const api = createApiClient()` 传入 | 可注入真 fake，去掉 vi.mock；域测试隔离性更好 | 需重写 16 个测试文件的 mock 基建；App 装配层 +3 行；收益与 P2-B 约束重叠 |
| C. 模块级可替换 | 单例 + `setApiClient(fake)` 测试钩子 | 折中 | 全局可变单例，测试污染风险（需显式 reset） |

**推荐 A**：实施阶段保持 `useTaskState(options?: { api?: ApiAdapter })` 的**可选**签名作为未来 seam 扩展（类型先行，生产不传），但测试仍走 `vi.mock('axios')` 既有契约——两个理由：① 行为等价铁律下，16 个文件的 mock 语义是经过 P1/P2/P5 多轮对抗验证的稳定契约，重写 = 引入回归风险；② P2-B 已把"错误提取/取消识别"收敛为共享纯函数，域测试的 HTTP 语义已足够薄。

---

## 5. WS 拆分规格（shared/ws.ts）

### 5.1 导出面

```ts
// shared/ws.ts —— 无业务语义的传输层 + 事件总线；不得 import 任何 features/*
import type { Task, QueueSnapshot } from '../types'

export type WsConnectionStatus = 'connecting' | 'open' | 'closed' | 'error'

export interface WsTaskUpdateMessage { type: 'task_update'; task: Task; queues?: QueueSnapshot[] }  // queues 可选字段向后兼容
export interface WsProgressUpdateMessage { type: 'progress_update'; task_id: string; progress: number }
export interface WsPingMessage { type: 'ping' }
export type WsServerMessage = WsTaskUpdateMessage | WsProgressUpdateMessage | WsPingMessage

export interface WsClient {
  readonly status: Ref<WsConnectionStatus>            // 装配层可 watch（非必须）
  readonly reconnectAttempts: Ref<number>             // 退避步进（3s→6s→12s→24s→30s 封顶）
  subscribe<T extends WsServerMessage['type']>(
    type: T,
    handler: (msg: Extract<WsServerMessage, { type: T }>) => void,
  ): () => void                                        // 返回退订函数；dispose 后自动失效
  send(payload: unknown): void                         // 仅 OPEN 态发送（ping/pong 内部处理）
  connect(): void                                      // 幂等；onopen 复位退避并广播 'open' 状态
  dispose(): void                                      // 幂等；关连接、清退避/兜底定时器、清空订阅
}

export function useWebSocket(options?: { baseUrl?: string }): WsClient
```

**内部职责（自 shared/ws.ts 携带，语义与现状逐条等价）**：
- 重连指数退避 `RECONNECT_BASE_DELAY_MS=3s → ×2 → 30s 封顶`；`onopen` 复位；
- `onerror` 不主动 close；5s 兜底重连定时器与 onclose 退避**共用槽位互斥**（后到作废，防双调度）；
- `wsDisposed` 语义保留（dispose 后重连回调双检查：调用前 + 定时器到期时）；
- 畸形帧（非 JSON）`console.warn` 跳过，不影响后续帧；`ping` 分支在 parse 之后、OPEN 态回 `pong`；
- 未知 type 帧静默忽略（协议扩展向后兼容）。
- **不含**：墓碑过滤、内容合并守卫、列表/选中任务更新——这些是 task 域语义，留在 `task/state.ts` 的订阅回调里（§5.2）。

### 5.2 挂接方式：事件 → 状态更新在订阅层做

```
task/state.ts (syncWithWs)  task 域订阅:
  subscribe('task_update')  → 墓碑过滤 → 合并 tasks/selectedTask/queues → 缓存守卫与版本递增 → scheduleTaskPartsRefresh
  subscribe('progress_update') → 写 task.progress / selectedTask.progress
  watch(status==='open')    → 重连对账：fetchTasks + fetchQueueSnapshot + selectTask(currentTask)（现 onopen 行为）
upload/state.ts (syncWithWs) 订阅:
  watch(status==='open')    → fetchUploadConfig（上传配置对账，P2-C）
settings/state.ts           → 不订阅（vibevoice 状态为轮询/显式拉取，现状无 WS 依赖）

装配层（App.vue）:
  const ws = useWebSocket(); task.syncWithWs(ws); upload.syncWithWs(ws)
  ws.connect() 于装配层 onMounted（或 useWebSocket 内部注册 onMounted——二选一，实施时定）
  ws.dispose() 于装配层 onUnmounted（替代现状 wsDisposed/定时器清理）
```

- **重连对账的触发权从 ws.ts 转移到各域订阅回调**——ws.ts 只发 `status==='open'`，各域自己决定重连时做什么，域职责自洽且 ws.ts 保持业务无关；
- 轮询兜底（60s fetchTasks/fetchQueueSnapshot）是 task 域的"断流保险"，随 task state 生命周期管理（现状 onMounted 注册、onUnmounted 清理，语义不变）。

---

## 6. 测试迁移清单

现状：`__tests__/` 40 文件 266 用例 + features seam 9 文件 86 用例（合计约 352；以 `vitest run` 实测为准）。本包涉及迁移的为直接耦合 `useTaskViewModel` 的 16 文件（112 用例）+ 2 个契约文件。

### 6.1 按域搬移/改写清单

| 现有文件（用例数） | 归属域 | 改动类型 | 说明 |
|---|---|---|---|
| `useTaskViewModel.adversarial.test.ts` (20) | task + upload | **拆分** | 转录切换修复/内容缓存/重转录自愈/过期在途响应 → `features/task/__tests__/state.taskContent.test.ts`；payload 部分 → upload 域 |
| `useTaskViewModel.p1Defenses.test.ts` (11) | task（+upload 的 quality 断言） | **拆分** | 合并守卫/懒加载 → task；[P1-5] quality payload → upload |
| `useTaskViewModel.transcript.test.ts` (10) | task | 搬移 + 改 import | 原文懒加载 |
| `useTaskViewModel.summaryModeTab.adversarial.test.ts` (16) | task + upload + __tests__ | **拆分** | composable 层 payload → upload；确认窗口/版本计数 → task；Sidebar 层/TaskMetaCard 渲染断言留 `__tests__` |
| `useTaskViewModel.queue.test.ts` (5) | task | 搬移 + 改 import | 队列快照 |
| `useTaskViewModel.redownload.test.ts` (4) | task | 搬移 + 改 import | re-download |
| `useTaskViewModel.deleteTombstone.test.ts` (4) | task（墓碑）+ ws 相关断言 | **拆分** | 墓碑语义在 task，广播过滤断言随 task 订阅回调 |
| `useTaskViewModel.polling.test.ts` (2) | task | 搬移 + 改 import | 60s 轮询兜底 |
| `useTaskViewModel.generateTopic.test.ts` (7) + `.adversarial` (4) | upload | 搬移 + 改 import | 提交 payload 的 generate_topic 语义 |
| `useTaskViewModel.uploadConfig.test.ts` (2) + `uploadConfigReconcile.test.ts` (2) | upload | 搬移 + 改 import | 上传上限对账 |
| `useTaskViewModel.vibevoice.test.ts` (4) | settings | 搬移 + 改 import | vibevoice 服务状态 |
| `useTaskViewModel.heartbeat.test.ts` (3) | shared/ws | 搬移 + 改 import | ping/pong + 畸形帧（随 ws 语义） |
| `useTaskViewModel.wsLifecycle.test.ts` (8) | shared/ws | 重写 | 退避/dispose/定时器清理改测 `useWebSocket`；其中"广播合并到 selectedTask"断言随 task 订阅回调迁 task 域 |
| `sharedApiClient.test.ts` (5) | shared/api | 搬移 + 改 import | → `shared/api/__tests__/client.seam.test.ts`；B5"useTaskViewModel 全部走共享实例"改断言为"三个 state 域全部走共享实例" |
| `types.test.ts` (13) | 契约层 | 保留 `__tests__` | 字典往返 + 编译期 payload 形状（仅注释引用 useTaskViewModel 行号，更新注释即可） |
| `App.p1Defenses.test.ts` (7) / `App.toastContainer.test.ts` (3) | 装配层回归 | **不改**（seam 保持则零改动） | mount App，验证 App.vue 装配接线与 error→toast 聚合 |
| Sidebar.* (6 文件 38) / SettingsModal.* (14) / TaskInfoModal.* (13) / TaskMetaCard.* (10) / FloatingToolbar.* (8) / TaskPartsPanel.* (3) | 组件层 | **不改**（props/emit seam 不变） | mount 组件传 props，与 state 域无直接 import |
| features seam 9 文件 (86) | features | 不改 | P6 产出，与 P7 无耦合 |

### 6.2 新增 seam 契约测试计划（TDD 先行）

| 新文件 | 用例方向 |
|---|---|
| `features/task/__tests__/state.seam.test.ts` | 导出面形状断言（63 键逐键映射）；合并守卫三态；懒加载 watch 触发条件；`__resetTaskContentCaches` 语义 |
| `features/upload/__tests__/state.seam.test.ts` | 三通道 payload 形状（含 quality 常量、generate_topic 条件）；取消语义；uploadMaxBytes 对账；isBilibiliUrl 判定 |
| `features/settings/__tests__/state.seam.test.ts` | 三表单保存 payload + 失败抛错双通道；validateModelPath 安全视图；scanVibeVoiceServices 失败返回 [] |
| `shared/ws/__tests__/ws.seam.test.ts` | 订阅/退订；退避序列与复位；dispose 后无重连；畸形帧跳过 |
| `App.vue` 装配层新增（并入 App.p1Defenses 风格） | 三 state 实例 + ws 接线后重连对账联动（onopen → 三域 fetch 被调用） |

**迁移纪律**：搬移即改写 import（`../composables/useTaskViewModel` → 各域 state.ts），**断言数与语义不得减少**——行为等价铁律下，112 用例的迁移后总和 ≥ 112。

---

## 7. 风险与决策点

### 7.1 循环依赖规避（已定方向）

- `shared/ws.ts`、`shared/api/client.ts`、`src/types`：纯下层，禁止 import 任何 features/*；
- `upload/state.ts` / `settings/state.ts`：不 import task/upload（现状已满足：提交后靠 WS 通知，settings 与 task 无前端状态流）；
- **唯一循环风险**：task 域 `reTranscribe` 内读 upload 域 `summaryMode`（§1.2 耦合 1）。规避方案见决策点 D1——推荐"显式传参"，使 task 对 upload 零 import，依赖图严格无环。

### 7.2 响应性边界（事件 vs 直接引用）

| 数据流 | 机制 | 理由 |
|---|---|---|
| WS 广播 → task 列表/选中任务/队列 | 事件订阅（task 域自处理） | ws.ts 保持业务无关；墓碑/合并守卫是 task 语义 |
| upload 提交成功 → 列表刷新 | **不主动刷新，靠 WS task_update**（现状行为，保持） | 现状已如此，"No need to fetchTasks here, WS will notify" |
| 重连成功 → 各域对账 | `status==='open'` 事件，各域订阅自做 | 触发权归订阅层，域职责自洽 |
| summaryMode → reTranscribe 确认窗口 | 装配层显式传参（D1） | 避免 task→upload import |
| error → toast | 装配层聚合 watch（D2） | 单一 error ref 拆为三域后，聚合在装配层恢复等价语义 |
| settings 保存 → 后续任务行为 | 无前端同步（后端生效） | 现状即如此 |

### 7.3 P1/P2 守卫语义在 state.ts 层的落位

- **P1（身份守卫/内容守卫）**：`mergeTaskWithPreservedContent`、`hasContent`、`PROCESSING_STATUSES`、内容版本计数、`latest_modified_at` 陈旧判定 → 全部留在 `task/state.ts`（模块级，语义逐条平移）；quality 死绑定常量 → `upload/state.ts`；
- **P2（网络生命周期）**：重连退避/onerror 兜底/畸形帧/ping-pong/dispose → `shared/ws.ts`；取消识别（`isCanceledRequest`）→ upload 域沿用 `shared/api` 纯函数；墓碑防复活 → task 域订阅回调；
- 测试锚点：`useTaskViewModel.p1Defenses.test.ts` 与 `wsLifecycle.test.ts` 迁移后逐用例对应，作为守卫语义未漂移的验收证据。

### 7.4 决策点（需实施阶段确认，推荐已给）

- **D1. `summaryMode`/`generateTopic` 归属与 `reTranscribe` 签名**：
  - 推荐 A：归 `upload/state.ts`；`reTranscribe(taskId, mode?)` 新增可选参数；App 装配层 `task.reTranscribe(taskId, upload.summaryMode.value)` 传参——task 对 upload 零 import，plan 的"upload→task 单向"字面方向无法实现（实际数据流是 task 读 upload 的提交配置），故以"零循环 + 装配层传参"为准；
  - 备选 B：上提 `shared/submitConfig.ts`（偏离四文件计划，但消除跨域读）；
  - 备选 C：允许 `task/state.ts → upload/state.ts` 单向 import（无环但违反域纯净）。
- **D2. 三域 `error` ref 的 toast 聚合**：推荐装配层 `watch([task.error, upload.error, settings.error])` 聚合（App.vue:643 现有 watch 的等价平移）；备选 error 总线/回调注入（过度设计，不推荐）。
- **D3. `useTaskViewModel` 兼容层是否保留**：推荐**直接删除**（生产消费点仅 App.vue，16 个测试迁移后无消费者；保留只会鼓励旧 import）；删除动作实施时列入文件清单交主流程确认（数据保护约束）。
- **D4. 生命周期宿主**：推荐装配层持有 `onMounted`（并发 8 个 fetch + `ws.connect()`）与 `onUnmounted`（`ws.dispose()` + task 轮询清理）；各 state 不自行注册生命周期钩子（与现状"单一宿主"等价，测试挂载组合更容易）。

---

## 8. 验收标准（实施阶段完成判据）

1. **vitest 全绿**：迁移 + 新增 seam 测试后 `cd frontend && uv run vitest`（或 `npm test`）全过；迁移断言数 ≥ 原 112（行为等价铁律）；`__tests__/` 中不再有 `useTaskViewModel` 引用（除 App/Sidebar 等组件层测试的间接依赖）；
2. **类型门禁**：`vue-tsc -b` 通过（**必须显式 `-b`**——root tsconfig 为 solution-style，`--noEmit` 不检查任何文件，P4 教训）；
3. **构建**：`npm run build` 通过（含 dist 重建——21010 静态包约束；本包无行为变更，dist 随构建更新即可）；
4. **行为等价冒烟**：playwright 冒烟（21001）：提交任务（URL/文件/本地路径三通道）→ 列表出现；任务选中/切换 A→B→A 内容完整；re-transcribe 清空确认窗口；设置三 tab 保存 + vibevoice 状态扫描；断网重连后列表对账无截断覆盖；`App.p1Defenses.test.ts`/`App.toastContainer.test.ts` 不改而全绿 = App 装配 seam 未被破坏的直接证据；
5. **结构断言**：`grep -rn "features/task/state\|features/upload/state\|features/settings/state" --include="*.ts" src | grep -v "__tests__"` 结果仅 App.vue（装配层唯一接线点）；state.ts 之间零相互 import；
6. **守卫语义锚点**：P1/P2 测试迁移后逐用例对照（§7.3 锚点清单）无缺失。

---

## 附：与 plan.md 的行数差异说明

plan.md §P7 记 `useTaskViewModel（1323 行）`为 P6 前基线；实测当前 1523 行（P6 期间 composable 回填增量：下载/复制按需加载、summary tab 懒加载、墓碑定时器清理等累计）。本规格以实测 1523 行为准。
