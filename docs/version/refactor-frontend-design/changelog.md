# Refactor: 前端设计重构 — Change Log

**Branch**: `refactor/frontend-design`（基于 `public @ 7fbab68`，== origin/public）
**Date**: 2026-08-13

## Change Log

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
