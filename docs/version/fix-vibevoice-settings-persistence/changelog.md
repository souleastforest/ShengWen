# Fix: 任务"原文/全文转录"内容为空 - Change Log

**Branch**: `fix/vibevoice-settings-persistence`
**Date**: 2026-08-08

## Change Log

- 2026-08-08: 修复"任务已完成但原文/全文转录内容为空、无法复制"的回归 bug。根因：commit `7293823`（perf: avoid loading full multipart summaries on select）将任务选中时的详情请求改为 `GET /tasks/{id}?include_content=false`，后端在该模式下剥离 `transcript` 字段；而唯一加载完整内容（`include_content=true`）的入口 `fetchTaskFullContent` 只挂在 multipart 任务的"展开完整分P总结"按钮上，导致所有历史已完成任务选中后 `transcript` 恒为 null（"AI 总结"tab 正常，因为后端保留截断后的 summary）。修复方案（保持轻量加载性能优化不变）：前端 `useTaskViewModel.ts` 新增三条转录按需加载链路——① watch `activeTab`，切换到"原文"tab 且 transcript 为空时调用 `fetchTaskFullContent` 懒加载完整内容；② `selectTask` 详情响应合并时保留已加载的 transcript，防止轻量响应覆盖；③ `copyContent('transcript')` 在 transcript 为空时先加载完整内容再复制（任务确实无转录时仍返回失败提示）。数据库与后端接口无问题（已验证目标任务 `c54fa795` transcript 完整 5416 字符，`include_content=true` 路径完好）。
- 2026-08-08: 新增测试覆盖：前端 `useTaskViewModel.transcript.test.ts`（5 个用例：selectTask 轻量请求 / 切"原文"tab 触发完整加载 / 已加载 transcript 不被覆盖 / 复制前先加载 / 无转录时返回 false）；后端 `tests/test_task_include_content.py`（4 个用例：默认返回完整内容 / `include_content=false` 剥离 transcript 与 summary_meta / 超长 summary 截断 / 分P标记处截断）。
