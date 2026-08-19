# Fix: B 站 412 反爬修复（yt-dlp 版本对齐 + 三调用点统一防爬请求头）- Change Log

**Branch**: `fix/ytdlp-bump-2026-7-4-bilibili-412`（基于 refactor/frontend-design）
**Date**: 2026-08-16

## Change Log

- 2026-08-16: **B 站 412 修复**（测试环境任务 `b87e7a69` 作者解析三连 412；commit `0a94f9a`）。
  - **根因**：B 站对 `x/player/wbi/playurl` 反爬收紧（HTTP 412，非字段结构变更——字段变更应表现为解析错误）。钉死版 `yt-dlp==2025.12.8`（7 个月前）三组合（裸/浏览器headers/headers+Cookie）实测全部 412；最新稳定版 `2026.7.4` 三组合全部成功（代理与直连均验证）。上游 issue #14830/#16587 未关闭、PR #16889（X-BILI-SEC-TOKEN）不稳定未合并，但实测新版可用。
  - **修复 1 版本对齐**（`pyproject.toml` + `uv.lock`）：`yt-dlp==2025.12.8` → `==2026.7.4`；`yt_dlp.utils` 等运行时 API 逐项验证兼容。
  - **修复 2 统一防爬请求头**（新增 `downloader/bilibili_headers.py` 共享模块）：主下载 worker 原有内联 UA/Referer/Origin + SESSDATA Cookie 防御（video_downloader_worker.py:1110 注释"否则触发 412 反爬"）抽为 `build_bilibili_http_headers()`，并接入此前裸调的两个同型调用点——`bilibili_author_resolver.py`（本次报错来源，新增 `sessdata` 参数透传）与 `bilibili_info_worker.py`；`deps.py` 作者回填路径增加全局 SESSDATA 懒加载（走 api 单例，参考 tasks.py 既有模式）；`_sanitize_cookie_value` 三处重复收敛为共享实现（settings_manager 独立副本保留，避免跨域依赖）。
  - **单测**（新增 `tests/test_bilibili_author_resolver.py`，13 项，先红后绿——旧代码复现 `KeyError: 'http_headers'`）：headers/Cookie 传递断言、字段提取与 channel 回退、playlist unwrap、URL 归一化、412/超时错误映射。
  - **全量测试**：worktree venv（新 yt-dlp）293 passed；torch 相关 47 项因 worktree venv 缺 torch（磁盘不足装不下，环境差异非代码问题）改由主 venv 验证通过；存量失败 1 项 `test_create_task_publishes_task_created`（假 bvid `BV1test` 无法通过 bilibili_api 分P校验返回 422，旧 yt-dlp 下同样失败，与本次无关）。
  - **basedpyright**：改动前后 error 19→19（基线对比仅行号偏移），全部为 yt-dlp 2026.7.4 新增类型标注暴露的存量 typing 债务，本次未新增 error（保守版：不混入修复提交，遗留见下）。
  - **E2E（worktree 独立实例 21099，自包含环境）**：真实 URL `b23.tv/BWZ4WKD` 建任务 → 字幕直取关闭强制走 yt-dlp 下载路径 → 下载成功（无 Cookie 仅浏览器头）→ 作者回填 `来点思考` + space URL → 服务日志严格匹配 0 次 412 → delete_task 清理 + 专属视频文件删除 + 实例停止。
  - **影响面**：全库仅 1 个任务受影响（`b87e7a69`，作者字段未回填；修复上线后可通过 GET 触发回填）。生产同步待用户指令（回填路径：合并 fix 分支 → prod checkout → uv sync → 重启 21010）。
  - **流程档位**：T1+（根因 2 分钟内锁定到 file:line + 三组合矩阵实证；修复面 5 文件但涉及依赖升级，按保守版执行，未派子代理三步走，对抗验证以基线对比 + e2e 断言替代）。

## 遗留

1. **存量测试失败**：`test_create_task_publishes_task_created`（假 bvid 422）与本次修复无关，需单独排期更新测试数据为合法 BV 号。
2. **存量 typing 债务**：19 个 basedpyright error（`_Params`/`_InfoDict` 类型不匹配、`Worker = None` 默认值、deps 泛型缺失等），yt-dlp 新类型标注暴露，建议单独 cleanup commit。
3. **动态更新检测器**：用户提出"不重启即可用最新 yt-dlp"的设想。进程内热更新不可行（模块缓存 + extractor 注册表）；可行方案为子进程隔离架构（重 refactor）或"检测不自动更新"——定期 canary 探针（裸请求固定视频，412 即告警）+ PyPI 版本新鲜度提示，更新仍走显式 uv sync + 重启。待用户决策是否立项。
