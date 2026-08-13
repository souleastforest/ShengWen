# Fix: 生产实时进度事故修复（WS 广播加固/双向心跳/轮询兜底 + ASR 分片进度）- Change Log

**Branch**: `fix/realtime-progress`（基于 refactor/frontend-design，含 P1 防线加固；计划后续 cherry-pick 到 public 同步生产 21010）
**Date**: 2026-08-13

## Change Log

- 2026-08-13: **生产实时进度事故修复**（生产任务 `93b857d0`——"制度的异化"/A 的视频会议，~1h 录音、10 个 ASR 分片，前端状态栏全程不实时更新；5 路侦查子代理 + 实施子代理确认根因组合，commit `27ae849`）。
  - **根因组合**：① `websocket.py` 广播循环串行 `await send_text` 无超时 + `except Exception: pass` 吞错 + 失败连接不移除——半开连接（TCP 缓冲满无 RST）可挂起数分钟，堵死后续所有广播；② 两端无心跳/保活，半开永远无法发现（生产实证：浏览器 WS 04:04 建立后 55 分钟无数据也无重连，05:08 任务完成后刷新页面才看到结果）；③ 前端无轮询兜底，数据刷新仅挂载时 + WS onopen 时，广播丢失 = 永久冻结。
  - **修复 1 后端 WS 加固**（`websocket.py` +96）：`_safe_send`（`asyncio.wait_for` 10s 超时）失败即移除连接并 close；broadcast 迭代副本；heartbeat task 每 30s 发 `{"type":"ping"}`；receive 90s 超时判半开 close+移除；协议注释声明 ping/pong 与向后兼容（旧客户端对未知 type 忽略）。
  - **修复 2 前端**：60s 轮询兜底（fetchTasks + fetchQueueSnapshot，onUnmounted 清理）；ping→pong 应答；未知 type 忽略。
  - **修复 3 ASR 分片进度展示**（用户期望"转录分片 10/10"）：新增 `asr_chunk_total/done` 字段（加性 schema 迁移，ALTER 幂等，与 source_name 同模式）；transcriber `run_chunks` 写 total（首事件）与 done（每片递增，与 progress 合并上报不重复广播）；转录完成/重转录 reset_data/multipart 合并三路径置 None（F1 全覆盖）；前端 Sidebar"转录中 (x/y)"标签 + TaskMetaCard"转录分片 x/y"（clamp + `!has_parts` 排除 multipart 父任务）。
  - **澄清**：用户观察的"分块进度 1/1"非 bug——`summary_chunk_*` 只属总结分块（agent 模式真实单块：3588s 音频尾块 < 1800s 合并规则），ASR 转录分片是另一维度（此前从不落库）。
  - **对抗验证**（code-reviewer）：无阻塞项；F1（清空路径补齐）、F2（弱断言改写）、F4（multipart 排除 + clamp）修订完成；F5（onmessage JSON.parse 畸形帧防护）确认 P2 范围（排期 refactor-frontend-design plan §7）。
  - **测试**：后端 307 passed（+17：test_websocket_heartbeat 7 项、test_asr_chunk_progress 8 项、修订 F1/F2 2 项）；前端 217 passed（+21：4 个新测试文件 Sidebar.asrChunkLabel / TaskMetaCard.asrChunk / useTaskViewModel.heartbeat / useTaskViewModel.polling）；vue-tsc/build 通过；ruff/basedpyright 干净。
  - **E2E（21001）**：快速场景（服务端 ping 35s 实证、100s 连接无重连 = pong 保活、60s 轮询 /tasks/ ×3、渲染回归）+ 慢速场景（真实 11min 音频 multipart 上传 → asr=(2/0)→(2/1) 广播实证、progress 单调、COMPLETED 后置 None、任务清理）。
  - **流程档位**：T2（生产异常、跨模块），子代理三步走 + 对抗验证；同日完成（commit `27ae849` @ 2026-08-13 06:29 UTC）。
