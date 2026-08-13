# Refactor: 前端设计重构 — Change Log

**Branch**: `refactor/frontend-design`（基于 `public @ 7fbab68`，== origin/public）
**Date**: 2026-08-13

## Change Log

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
