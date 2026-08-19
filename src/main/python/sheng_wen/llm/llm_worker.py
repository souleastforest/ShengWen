from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from loguru import logger

from ..config.settings import config
from ..summarization.chunked_summarizer import ChunkedSummarizer
from ..summarization.chunker import count_timestamp_lines, split_transcript_into_chunks
from ..worker import TaskCancelledError, Worker
from .llm import LLM, LLMError, LLMMessage


VALID_SUMMARY_MODES = {"auto", "standard", "agent", "none"}

# 标题生成只用全文开头的有限长度即可抓住主旨：避免超长转录
# 撑爆上下文窗口，同时控制成本（标准总结仍走全文/分块，不受影响）。
TOPIC_GENERATION_MAX_CHARS = 12000

TOPIC_GENERATION_SYSTEM_PROMPT = (
    "你是视频标题生成助手。请根据用户提供的视频转录全文，"
    "生成一个简洁、准确的中文标题，概括视频的核心内容。"
    "只输出标题本身，不要任何解释、引号或前后缀，控制在 30 字以内。"
)


async def generate_topic_for_task(
    llm_worker: Any,
    task_id: str,
    transcript: str,
) -> None:
    """
    仅转录模式：对转录全文生成标题并写入 task.topic。

    复用现有 LLM 客户端（LLMWorker.generate_topic，即"api同款"单次调用）。
    LLM 缺失/调用失败/返回空时静默降级：只记 warning，不阻塞任务终态、
    不失败任务。multipart 等场景由调用方保证只对最终全文调用一次。
    """
    if not transcript or not transcript.strip():
        logger.info(f"[TopicGenerator] 转录文本为空，跳过标题生成: task_id={task_id}")
        return
    if llm_worker is None or not hasattr(llm_worker, "generate_topic"):
        logger.warning(
            f"[TopicGenerator] 缺少 LLM worker，跳过标题生成: task_id={task_id}"
        )
        return
    try:
        topic = await llm_worker.generate_topic(transcript)
    except Exception as e:
        logger.warning(
            f"[TopicGenerator] 标题生成失败，静默降级: task_id={task_id}, error={e}"
        )
        return
    if not topic or not isinstance(topic, str) or not topic.strip():
        logger.info(f"[TopicGenerator] 标题生成为空，跳过写入: task_id={task_id}")
        return
    from ..task_updater import update_and_notify

    await update_and_notify(task_id, {"topic": topic.strip()})
    logger.info(
        f"[TopicGenerator] 已写入标题: task_id={task_id}, topic={topic.strip()!r}"
    )


class LLMWorker(Worker):
    """
    使用 LLM 将转录文本转为总结文本。
    支持三种策略：
    - standard: 单次总结
    - agent: 分块总结
    - auto: 自动判定（长文本走 agent）
    - none: 仅转录模式由 TranscriberWorker 直接终态处理，不派发本 worker；
      若经 re-summarize 等入口仍携带 none 到达此处，按 auto 判定兜底生成总结。
    """

    def __init__(self, name: str, llm_client: LLM):
        super().__init__(name)
        self._llm_client = llm_client
        self.system_prompt: str | None = None
        self._chunk_prompt_cache_path: str | None = None
        self._chunk_prompt_cache_text: str | None = None

    def load_system_prompt(self, prompt_file: str):
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                self.system_prompt = f.read()
            logger.info(f"[{self.name}] 系统提示已从 {prompt_file} 成功加载。")
        except FileNotFoundError:
            logger.error(f"[{self.name}] 错误: 在 {prompt_file} 未找到提示文件。")
            self.system_prompt = None
        except Exception as e:
            logger.error(f"[{self.name}] 加载提示文件时发生错误: {e}", exc_info=True)
            self.system_prompt = None

    async def _process_multipart_resummarize(self, payload: dict[str, Any]) -> None:
        from ..task_parts import get_task_parts, update_task_part

        storage_dir = config.storage.resolved_base_dir
        task_id = str(payload.get("task_id") or "")
        parts = get_task_parts(task_id)
        for part in parts:
            index = int(part["part_index"])
            transcript = str(part.get("transcript") or "")
            if not transcript.strip():
                update_task_part(
                    task_id,
                    index,
                    {"status": "FAILED", "error_message": "该分P没有可用转录文本"},
                )
                continue
            update_task_part(
                task_id,
                index,
                {
                    "status": "SUMMARIZING",
                    "progress": 0,
                    "error_message": None,
                    "summary": None,
                },
            )
            temp_file = os.path.join(
                storage_dir, "{}_p{}_re.txt".format(task_id, index + 1)
            )
            output_file = os.path.join(
                storage_dir, "{}_p{}_re_summary.md".format(task_id, index + 1)
            )
            os.makedirs(storage_dir, exist_ok=True)
            with open(temp_file, "w", encoding="utf-8") as file:
                file.write(transcript)
            try:
                await self.process_task(
                    {
                        "task_id": task_id,
                        "intermediate_file_path": temp_file,
                        "output_file": output_file,
                        "summary_mode": payload.get("summary_mode") or "auto",
                        "multipart_part": {
                            "index": index,
                            "title": part.get("title") or "",
                            "duration": part.get("duration") or 0,
                        },
                    }
                )
            except Exception as error:
                update_task_part(
                    task_id, index, {"status": "FAILED", "error_message": str(error)}
                )

        refreshed = get_task_parts(task_id)
        successful = [
            part
            for part in refreshed
            if part.get("status") == "COMPLETED" and part.get("summary")
        ]
        if not successful:
            await self._mark_failed(task_id, "所有分P均无法重新生成总结")
            return
        overview_file = os.path.join(storage_dir, "{}_overview_re.txt".format(task_id))
        overview_output = os.path.join(
            storage_dir, "{}_overview_re_summary.md".format(task_id)
        )
        with open(overview_file, "w", encoding="utf-8") as file:
            file.write("请根据以下各分P总结生成整套视频的总体概览。\n\n")
            file.write(
                "\n\n".join(
                    "## P{}：{}\n\n{}".format(
                        part["part_index"] + 1, part.get("title") or "", part["summary"]
                    )
                    for part in successful
                )
            )
        await self.process_task(
            {
                "task_id": task_id,
                "intermediate_file_path": overview_file,
                "output_file": overview_output,
                "summary_mode": payload.get("summary_mode") or "auto",
                "multipart_overview": True,
            }
        )

    async def process_task(self, payload: dict[str, Any]):
        intermediate_file_path = payload.get("intermediate_file_path")
        output_file = payload.get("output_file")
        task_id = str(payload.get("task_id") or "").strip() or None
        multipart_part = payload.get("multipart_part")

        if payload.get("multipart_resummarize") and task_id:
            await self._process_multipart_resummarize(payload)
            return

        if not intermediate_file_path or not output_file:
            await self._mark_failed(
                task_id,
                "payload 中缺少 'intermediate_file_path' 或 'output_file'",
                multipart_part=multipart_part,
            )
            return

        if task_id and self.is_task_cancelled(task_id):
            raise TaskCancelledError(f"任务已取消，跳过总结: {task_id}")

        try:
            with open(intermediate_file_path, "r", encoding="utf-8") as f:
                transcript_text = f.read()
        except FileNotFoundError:
            await self._mark_failed(
                task_id,
                f"找不到中间转录文件 {intermediate_file_path}",
                multipart_part=multipart_part,
            )
            return
        except Exception as e:
            await self._mark_failed(
                task_id, f"读取中间文件时出错: {e}", multipart_part=multipart_part
            )
            return

        if not transcript_text.strip():
            empty_error = "转录文本为空，无法生成总结；已跳过 LLM 请求（可能是 ASR 未生成有效转录片段）。"
            if multipart_part and task_id:
                from ..task_parts import update_task_part

                update_task_part(
                    task_id,
                    int(multipart_part["index"]),
                    {
                        "status": "FAILED",
                        "progress": 0,
                        "error_message": empty_error,
                    },
                )
            else:
                await self._mark_failed(task_id, empty_error)
            return

        task_data = None
        if task_id:
            from ..db import db

            task_data = db.get_task(task_id)
            if task_data is None:
                raise TaskCancelledError(f"任务已被删除，停止总结: {task_id}")

        requested_mode = self._resolve_requested_mode(payload, task_data)
        effective_mode = self._resolve_effective_mode(
            requested_mode=requested_mode,
            task_data=task_data,
            transcript_text=transcript_text,
        )

        auto_metrics = self._collect_auto_mode_metrics(
            task_data=task_data, transcript_text=transcript_text
        )
        logger.info(
            f"[{self.name}] 任务 {task_id or '<unknown>'} 总结模式: "
            f"requested={requested_mode}, effective={effective_mode}, "
            f"audio_duration_sec={auto_metrics['audio_duration_sec']:.2f}, "
            f"transcript_timestamp_lines={auto_metrics['line_count']}"
        )
        if requested_mode == "auto":
            logger.info(
                f"[{self.name}] 任务 {task_id or '<unknown>'} Auto 判定依据: "
                f"audio_duration {auto_metrics['audio_duration_sec']:.2f}s"
                f"{'>=' if auto_metrics['audio_triggered'] else '<'}"
                f"{config.summarization.auto_chunk_min_audio_duration_sec}s, "
                f"timestamp_lines {auto_metrics['line_count']}"
                f"{'>=' if auto_metrics['line_triggered'] else '<'}"
                f"{config.summarization.auto_chunk_min_transcript_lines}, "
                f"result={effective_mode}"
            )

        try:
            if effective_mode == "agent":
                final_summary, topic, summary_meta = await self._run_chunked_summary(
                    transcript_text=transcript_text,
                    task_id=task_id,
                    mode_value=requested_mode,
                    multipart_part=multipart_part,
                )
                mode_used = "agent"
            else:
                final_summary, topic = await self._run_standard_summary(
                    transcript_text=transcript_text,
                    task_id=task_id,
                    multipart_part=multipart_part,
                )
                summary_meta = {}
                mode_used = "standard"
        except asyncio.CancelledError:
            logger.info(f"[{self.name}] 任务被取消: {task_id or '<unknown>'}")
            raise
        except TaskCancelledError as e:
            logger.info(f"[{self.name}] {e}")
            return
        except Exception as e:
            if (
                effective_mode == "agent"
                and config.summarization.fallback_to_standard_on_agent_error
            ):
                logger.warning(f"[{self.name}] 分块总结失败，回退标准模式: {e}")
                try:
                    final_summary, topic = await self._run_standard_summary(
                        transcript_text=transcript_text,
                        task_id=task_id,
                        multipart_part=multipart_part,
                    )
                    summary_meta = {
                        "fallback_triggered": True,
                        "fallback_reason": str(e),
                    }
                    mode_used = "standard"
                except Exception as fallback_err:
                    await self._mark_failed(
                        task_id,
                        f"分块总结失败且回退标准模式失败: {fallback_err}",
                        multipart_part=multipart_part,
                    )
                    return
            else:
                await self._mark_failed(
                    task_id,
                    f"LLM 处理过程中发生错误: {e}",
                    multipart_part=multipart_part,
                )
                return

        # 纵深防御：写入前的最终校验，任何模式下都不允许空总结静默 COMPLETED
        if not (final_summary or "").strip():
            await self._mark_failed(
                task_id, "总结结果为空，任务标记失败", multipart_part=multipart_part
            )
            return

        if task_id and self.is_task_cancelled(task_id):
            raise TaskCancelledError(f"任务已取消，停止写入总结结果: {task_id}")

        try:
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(final_summary)
        except Exception as e:
            await self._mark_failed(
                task_id, f"写入总结结果失败: {e}", multipart_part=multipart_part
            )
            return

        if multipart_part and task_id:
            from ..task_parts import update_task_part

            update_task_part(
                task_id,
                int(multipart_part["index"]),
                {"status": "COMPLETED", "progress": 100, "summary": final_summary},
            )
            return

        if task_id:
            logger.info(
                f"[LLMWorker] Finalizing task: task_id={task_id}, "
                f"status: SUMMARIZING→COMPLETED"
            )

            from ..db import db, TaskStatus

            final_status = TaskStatus.COMPLETED
            if payload.get("multipart_overview"):
                from ..task_parts import get_task_parts

                parts = get_task_parts(task_id)
                separator = chr(10) * 2
                sections = [
                    "## P{}：{}{}{}".format(
                        part["part_index"] + 1,
                        part.get("title") or "",
                        separator,
                        part["summary"],
                    )
                    for part in parts
                    if part.get("summary")
                ]
                final_summary = (
                    "# 总体概览"
                    + separator
                    + final_summary
                    + separator
                    + "# 分P总结"
                    + separator
                    + separator.join(sections)
                )
                if any(part["status"] == "FAILED" for part in parts):
                    final_status = TaskStatus.PARTIAL

            update_data: dict[str, Any] = {
                "summary": final_summary,
                "status": final_status,
                "progress": 100,
                "summary_mode": mode_used,
                "summary_meta": json.dumps(summary_meta, ensure_ascii=False)
                if summary_meta
                else None,
            }
            if topic:
                update_data["topic"] = topic

            if mode_used != "agent":
                update_data["summary_chunk_total"] = None
                update_data["summary_chunk_done"] = None

            # Agent 模式下，等待所有待处理的分块更新完成
            if mode_used == "agent":
                await self._await_pending_updates(timeout=5.0)

            from ..task_updater import update_and_notify

            await update_and_notify(task_id, update_data)

    def _resolve_requested_mode(
        self, payload: dict[str, Any], task_data: dict[str, Any] | None
    ) -> str:
        payload_mode = str(payload.get("summary_mode") or "").strip().lower()
        if payload_mode in VALID_SUMMARY_MODES:
            return payload_mode

        task_mode = str((task_data or {}).get("summary_mode") or "").strip().lower()
        if task_mode in VALID_SUMMARY_MODES:
            return task_mode

        cfg_mode = str(config.summarization.mode or "auto").strip().lower()
        if cfg_mode in VALID_SUMMARY_MODES:
            return cfg_mode
        return "auto"

    def _resolve_effective_mode(
        self,
        requested_mode: str,
        task_data: dict[str, Any] | None,
        transcript_text: str,
    ) -> str:
        if requested_mode in {"standard", "agent"}:
            return requested_mode

        metrics = self._collect_auto_mode_metrics(
            task_data=task_data, transcript_text=transcript_text
        )
        if metrics["audio_triggered"] or metrics["line_triggered"]:
            return "agent"
        return "standard"

    def _collect_auto_mode_metrics(
        self,
        task_data: dict[str, Any] | None,
        transcript_text: str,
    ) -> dict[str, float | int | bool]:
        audio_duration = 0.0
        if task_data:
            try:
                audio_duration = float(task_data.get("audio_duration") or 0.0)
            except (TypeError, ValueError):
                audio_duration = 0.0
        line_count = count_timestamp_lines(transcript_text)
        audio_triggered = (
            audio_duration >= config.summarization.auto_chunk_min_audio_duration_sec
        )
        line_triggered = (
            line_count >= config.summarization.auto_chunk_min_transcript_lines
        )
        return {
            "audio_duration_sec": audio_duration,
            "line_count": line_count,
            "audio_triggered": audio_triggered,
            "line_triggered": line_triggered,
        }

    async def generate_topic(self, transcript: str) -> str | None:
        """
        对转录全文生成一个简洁标题（仅转录模式"总结标题"开关用）。

        单次非流式 LLM 调用，复用本 worker 的 _llm_client（即"api同款"）。
        失败时返回 None 并记录 warning，由调用方静默降级。
        """
        client = self._llm_client
        text = str(transcript or "").strip()
        if client is None:
            logger.warning("[LLMWorker] 无 LLM 客户端，跳过标题生成")
            return None
        if not text:
            return None

        # 只取全文开头一段用于标题生成（长转录成本/上下文保护）
        topic_source = text[:TOPIC_GENERATION_MAX_CHARS]
        messages = [
            LLMMessage(role="system", content=TOPIC_GENERATION_SYSTEM_PROMPT),
            LLMMessage(role="user", content=topic_source),
        ]
        response_chunks: list[str] = []
        llm_error: LLMError | None = None

        def callback(chunk: str | LLMError):
            nonlocal llm_error
            if isinstance(chunk, LLMError):
                llm_error = chunk
                return
            response_chunks.append(chunk)

        try:
            await client.response(
                messages=messages,
                resp_callback=callback,
                stream=False,
                timeout=60,
            )
        except Exception as e:
            logger.warning(f"[LLMWorker] 标题生成请求失败，静默降级: {e}")
            return None
        if llm_error:
            logger.warning(f"[LLMWorker] 标题生成响应错误，静默降级: {llm_error}")
            return None

        topic = "".join(response_chunks).strip()
        # 收敛：只取第一行并去掉常见引号包裹，避免模型输出多行/带引号
        first_line = topic.splitlines()[0].strip()
        cleaned = first_line.strip("\"'“”‘’「」『』《》【】")
        return cleaned.strip() or None

    async def _run_standard_summary(
        self,
        transcript_text: str,
        task_id: str | None,
        multipart_part: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        if not self.system_prompt:
            raise RuntimeError("未加载系统提示词，无法执行标准总结。")

        messages = [
            LLMMessage(role="system", content=self.system_prompt),
            LLMMessage(role="user", content=transcript_text),
        ]
        response_chunks: list[str] = []
        llm_error: LLMError | None = None

        # 防抖控制
        last_update_time = 0.0
        update_in_progress = False

        async def flush_partial_summary():
            nonlocal last_update_time, update_in_progress

            # 分P子任务：流式中间快照不得写主行（最终总结由 process_task
            # 的 multipart_part 分支写入 task_parts，见 :337-345）
            if multipart_part:
                return

            if not task_id or update_in_progress:
                return

            # 防抖：距离上次更新不足0.5秒则跳过
            now = asyncio.get_event_loop().time()
            if now - last_update_time < 0.5:
                return

            update_in_progress = True
            try:
                from ..db import db, TaskStatus
                from ..task_updater import update_and_notify

                # 只有当任务仍在 SUMMARIZING 状态时才更新，避免覆盖 COMPLETED 状态
                current_task = db.get_task(task_id)
                if (
                    not current_task
                    or current_task.get("status") != TaskStatus.SUMMARIZING
                ):
                    return

                await update_and_notify(
                    task_id,
                    {
                        "status": TaskStatus.SUMMARIZING,
                        "summary": "".join(response_chunks),
                        "summary_mode": "standard",
                        "summary_chunk_total": None,
                        "summary_chunk_done": None,
                    },
                )
                last_update_time = now
            finally:
                update_in_progress = False

        def callback(chunk: str | LLMError):
            nonlocal llm_error
            if task_id and self.is_task_cancelled(task_id):
                raise asyncio.CancelledError()

            if isinstance(chunk, LLMError):
                llm_error = chunk
                return

            response_chunks.append(chunk)
            # 每次收到新内容就尝试更新（防抖在flush内部处理）
            if task_id:
                self._submit_coro(flush_partial_summary())

        await self._llm_client.response(messages=messages, resp_callback=callback)
        if llm_error:
            raise llm_error

        final_summary = "".join(response_chunks)
        # 空结果视为失败：禁止静默生成空总结（与 agent 分块路径同规格）
        if not final_summary.strip():
            raise LLMError("标准总结结果为空")
        topic = _extract_topic(final_summary)
        return final_summary, topic

    async def _run_chunked_summary(
        self,
        transcript_text: str,
        task_id: str | None,
        mode_value: str,
        multipart_part: dict[str, Any] | None = None,
    ) -> tuple[str, str | None, dict[str, Any]]:
        chunk_prompt = self._load_chunk_prompt(config.summarization.chunk_prompt_file)
        if not chunk_prompt:
            raise RuntimeError("分块提示词为空，无法执行 Agent 增强模式。")

        task_label = task_id or "<unknown>"
        logger.info(
            f"[{self.name}] 任务 {task_label} 准备执行 Agent 分块总结: "
            f"target={config.summarization.chunk_target_duration_sec}s, "
            f"min={config.summarization.chunk_min_duration_sec}s, "
            f"max={config.summarization.chunk_max_duration_sec}s, "
            f"boundary_jump={config.summarization.boundary_jump_sec}s"
        )
        try:
            preview_chunks = split_transcript_into_chunks(
                transcript_text=transcript_text,
                target_duration_sec=config.summarization.chunk_target_duration_sec,
                min_duration_sec=config.summarization.chunk_min_duration_sec,
                max_duration_sec=config.summarization.chunk_max_duration_sec,
                boundary_jump_sec=config.summarization.boundary_jump_sec,
            )
            logger.info(
                f"[{self.name}] 任务 {task_label} Agent 预分块结果: total_chunks={len(preview_chunks)}"
            )
            for idx, chunk in enumerate(preview_chunks, start=1):
                logger.info(
                    f"[{self.name}] 任务 {task_label} 分块 {idx}/{len(preview_chunks)}: "
                    f"time={chunk.time_range_hms}, duration={chunk.duration_sec}s, lines={chunk.line_count}"
                )
        except Exception as e:
            logger.warning(f"[{self.name}] 任务 {task_label} Agent 预分块日志失败: {e}")

        def cancel_check() -> bool:
            return bool(task_id and self.is_task_cancelled(task_id))

        last_stream_update = 0.0
        last_stream_summary = ""
        update_in_progress = False

        def update_chunk_progress(done: int, total: int, partial_summary: str):
            nonlocal last_stream_summary
            # 分P子任务：流式中间快照不得写主行（最终总结由 process_task
            # 的 multipart_part 分支写入 task_parts，见 :337-345）
            if multipart_part:
                return
            if not task_id:
                return
            from ..db import db, TaskStatus

            if self.is_task_cancelled(task_id):
                return

            current_task = db.get_task(task_id)
            if not current_task:
                return
            current_status = str(current_task.get("status") or "")
            # 避免晚到的分块进度回写覆盖最终状态（例如已 COMPLETED 却被写回 SUMMARIZING）。
            if current_status != TaskStatus.SUMMARIZING.value:
                return

            progress = int((done / total) * 100) if total > 0 else 0
            from ..task_updater import update_and_notify

            self._submit_coro(
                update_and_notify(
                    task_id,
                    {
                        "status": TaskStatus.SUMMARIZING,
                        "summary": partial_summary,
                        "progress": progress,
                        "summary_mode": mode_value
                        if mode_value in {"auto", "agent"}
                        else "agent",
                        "summary_chunk_total": total,
                        "summary_chunk_done": done,
                    },
                )
            )
            last_stream_summary = partial_summary

        async def flush_chunk_stream(done: int, total: int, streaming_summary: str):
            """异步防抖更新函数"""
            nonlocal last_stream_update, last_stream_summary, update_in_progress

            # 分P子任务：流式中间快照不得写主行（最终总结由 process_task
            # 的 multipart_part 分支写入 task_parts，见 :337-345）
            if multipart_part:
                return

            if not task_id or not streaming_summary or update_in_progress:
                return

            # 防抖：距离上次更新不足0.5秒则跳过
            now = asyncio.get_event_loop().time()
            if now - last_stream_update < 0.5:
                return

            if streaming_summary == last_stream_summary:
                return

            update_in_progress = True
            try:
                from ..db import db, TaskStatus
                from ..task_updater import update_and_notify

                # 只有当任务仍在 SUMMARIZING 状态时才更新
                current_task = db.get_task(task_id)
                if not current_task:
                    return
                current_status = str(current_task.get("status") or "")
                if current_status != TaskStatus.SUMMARIZING.value:
                    return

                progress = int((done / total) * 100) if total > 0 else 0
                await update_and_notify(
                    task_id,
                    {
                        "status": TaskStatus.SUMMARIZING,
                        "summary": streaming_summary,
                        "progress": progress,
                        "summary_mode": mode_value
                        if mode_value in {"auto", "agent"}
                        else "agent",
                        "summary_chunk_total": total,
                        "summary_chunk_done": done,
                    },
                )
                last_stream_update = now
                last_stream_summary = streaming_summary
            finally:
                update_in_progress = False

        def update_chunk_stream(done: int, total: int, streaming_summary: str):
            """同步回调函数，每次收到新内容就触发更新"""
            if self.is_task_cancelled(task_id):
                return
            # 每次收到新内容就尝试更新（防抖在flush内部处理）
            self._submit_coro(flush_chunk_stream(done, total, streaming_summary))

        summarizer = ChunkedSummarizer(
            llm_client=self._llm_client,
            chunk_system_prompt=chunk_prompt,
            chunk_target_duration_sec=config.summarization.chunk_target_duration_sec,
            chunk_min_duration_sec=config.summarization.chunk_min_duration_sec,
            chunk_max_duration_sec=config.summarization.chunk_max_duration_sec,
            boundary_jump_sec=config.summarization.boundary_jump_sec,
            prev_tail_timestamp_lines_m=config.summarization.prev_tail_timestamp_lines_m,
            prev_summary_tail_chars_j=config.summarization.prev_summary_tail_chars_j,
            llm_call_retry_max=config.summarization.llm_call_retry_max,
            max_agent_value_chars=config.summarization.max_agent_value_chars,
            cancel_check=cancel_check,
            chunk_debug_dump_enabled=config.summarization.chunk_debug_dump_enabled,
            chunk_debug_dump_dir=config.summarization.chunk_debug_dump_dir,
        )

        result = await summarizer.summarize(
            transcript_text=transcript_text,
            on_chunk_progress=update_chunk_progress,
            on_chunk_stream=update_chunk_stream,
        )
        topic = _extract_topic(result.summary_text)
        summary_meta = {
            "mode": "agent",
            "chunk_total": result.chunk_total,
            "chunk_done": result.chunk_done,
            "warnings": result.warnings,
            "assembly_logs": result.assembly_logs,
        }
        return result.summary_text, topic, summary_meta

    def _load_chunk_prompt(self, prompt_file: str) -> str:
        normalized = str(prompt_file or "").strip()
        if not normalized:
            return ""
        if (
            self._chunk_prompt_cache_path == normalized
            and self._chunk_prompt_cache_text is not None
        ):
            return self._chunk_prompt_cache_text

        try:
            with open(normalized, "r", encoding="utf-8") as f:
                text = f.read()
            self._chunk_prompt_cache_path = normalized
            self._chunk_prompt_cache_text = text
            logger.info(f"[{self.name}] 分块提示词已加载: {normalized}")
            return text
        except Exception as e:
            logger.error(f"[{self.name}] 读取分块提示词失败: {normalized}, error={e}")
            return ""

    async def _mark_failed(
        self,
        task_id: str | None,
        error_message: str,
        multipart_part: dict[str, Any] | None = None,
    ) -> None:
        logger.error(f"[{self.name}] {error_message}")
        if not task_id:
            return
        if self.is_task_cancelled(task_id):
            return
        if multipart_part:
            # 分P子任务失败只写 task_parts（P1-2）；父任务终态由
            # _process_bilibili_multipart finalize 汇总收敛（FAILED/PARTIAL）
            from ..task_parts import update_task_part

            update_task_part(
                task_id,
                int(multipart_part["index"]),
                {"status": "FAILED", "progress": 0, "error_message": error_message},
            )
            return
        from ..db import TaskStatus
        from ..task_updater import update_and_notify

        await update_and_notify(
            task_id, {"status": TaskStatus.FAILED, "error_message": error_message}
        )


def _extract_topic(summary: str) -> str | None:
    for match in re.finditer(r"\{\{(.*?)\}\}", summary or "", re.IGNORECASE):
        token = (match.group(1) or "").strip()
        if not token:
            continue
        if token.lower().startswith("chunk_"):
            continue
        if token.lower().startswith("opt-tools"):
            continue
        return token
    return None
