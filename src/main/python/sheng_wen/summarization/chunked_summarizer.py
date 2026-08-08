from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from loguru import logger

from ..llm.llm import LLM, LLMError, LLMMessage
from ..worker import TaskCancelledError
from .assembler import assemble_chunk_summaries
from .chunker import TranscriptChunk, split_transcript_into_chunks, tail_timestamp_lines
from .prompt_builder import build_chunk_user_prompt, generate_structure_overview
from .protocol import parse_state_ops, strip_state_instruction_blocks
from .state_manager import DynamicStateManager


def remove_duplicate_paragraphs(text: str) -> str:
    """移除连续重复的段落"""
    paragraphs = text.split('\n\n')
    result = []
    prev = None
    for para in paragraphs:
        para_stripped = para.strip()
        if para_stripped and para_stripped != prev:
            result.append(para)
            prev = para_stripped
    return '\n\n'.join(result)


def truncate_after_state_ops(text: str) -> str:
    """截断state_ops之后的所有内容"""
    pattern = r'(```state_ops\s*\{[^`]*\}\s*```)'
    matches = list(re.finditer(pattern, text, re.DOTALL))
    if matches:
        last_match = matches[-1]
        truncated = text[:last_match.end()]
        if truncated != text:
            logger.info(f"[ChunkedSummarizer] 截断了state_ops之后的内容，移除了 {len(text) - len(truncated)} 个字符")
        return truncated
    return text


def truncate_discussed_topics(topics: str, max_items: int = 10) -> str:
    """只保留最近的N个标题，避免过长"""
    if not topics:
        return topics

    items = [item.strip() for item in topics.split(';') if item.strip()]
    if len(items) > max_items:
        truncated = '; '.join(items[-max_items:])
        logger.info(f"[ChunkedSummarizer] 截断已讨论主题从 {len(items)} 项到 {max_items} 项")
        return truncated
    return topics


@dataclass
class ChunkedSummaryResult:
    summary_text: str
    chunk_total: int
    chunk_done: int
    assembly_logs: list[str]
    warnings: list[str]


class ChunkedSummarizer:
    def __init__(
        self,
        llm_client: LLM,
        chunk_system_prompt: str,
        chunk_target_duration_sec: int,
        chunk_min_duration_sec: int,
        chunk_max_duration_sec: int,
        boundary_jump_sec: int,
        prev_tail_timestamp_lines_m: int,
        prev_summary_tail_chars_j: int,
        llm_call_retry_max: int,
        max_agent_value_chars: int,
        cancel_check: Callable[[], bool] | None = None,
        chunk_debug_dump_enabled: bool = False,
        chunk_debug_dump_dir: str = "temp/chunk_debug",
    ):
        self._llm_client = llm_client
        self._chunk_system_prompt = chunk_system_prompt
        self._chunk_target_duration_sec = max(60, int(chunk_target_duration_sec))
        self._chunk_min_duration_sec = max(30, int(chunk_min_duration_sec))
        self._chunk_max_duration_sec = max(self._chunk_target_duration_sec, int(chunk_max_duration_sec))
        self._boundary_jump_sec = max(1, int(boundary_jump_sec))
        self._prev_tail_timestamp_lines_m = max(0, int(prev_tail_timestamp_lines_m))
        self._prev_summary_tail_chars_j = max(0, int(prev_summary_tail_chars_j))
        self._llm_call_retry_max = max(1, int(llm_call_retry_max))
        self._max_agent_value_chars = max(100, int(max_agent_value_chars))
        self._cancel_check = cancel_check
        self._chunk_debug_dump_enabled = bool(chunk_debug_dump_enabled)
        self._chunk_debug_dump_dir = Path(chunk_debug_dump_dir)

    async def summarize(
        self,
        transcript_text: str,
        on_chunk_progress: Callable[[int, int, str], None] | None = None,
        on_chunk_stream: Callable[[int, int, str], None] | None = None,
    ) -> ChunkedSummaryResult:
        chunks = split_transcript_into_chunks(
            transcript_text=transcript_text,
            target_duration_sec=self._chunk_target_duration_sec,
            min_duration_sec=self._chunk_min_duration_sec,
            max_duration_sec=self._chunk_max_duration_sec,
            boundary_jump_sec=self._boundary_jump_sec,
        )
        state = DynamicStateManager()
        chunk_outputs: list[str] = []
        warnings: list[str] = []

        if on_chunk_progress:
            on_chunk_progress(0, len(chunks), "")

        for idx, chunk in enumerate(chunks):
            self._ensure_not_cancelled()
            self._prepare_program_state(state, chunk, idx, chunks, chunk_outputs)
            structure = generate_structure_overview(chunk_outputs)
            user_prompt = build_chunk_user_prompt(
                chunk=chunk,
                chunk_index=idx,
                total_chunks=len(chunks),
                dynamic_state=state,
                structure_overview=structure,
            )
            chunk_prefix = ""
            if on_chunk_stream and chunk_outputs:
                chunk_prefix, _ = assemble_chunk_summaries(chunk_outputs)

            def emit_chunk_stream(partial_chunk_output: str):
                if not on_chunk_stream:
                    return
                preview = partial_chunk_output
                if chunk_prefix.strip():
                    preview = f"{chunk_prefix.rstrip()}\n\n{partial_chunk_output}"
                on_chunk_stream(idx, len(chunks), preview)

            output = await self._call_llm_with_retry(
                user_prompt,
                on_partial=emit_chunk_stream if on_chunk_stream else None,
            )
            chunk_outputs.append(output)

            parsed_ops, parse_warnings = parse_state_ops(output)
            if parse_warnings:
                warnings.extend([f"chunk_{idx}: {msg}" for msg in parse_warnings])
            apply_warnings = state.apply_ops(parsed_ops, max_value_chars=self._max_agent_value_chars)
            if apply_warnings:
                warnings.extend([f"chunk_{idx}: {msg}" for msg in apply_warnings])

            if self._chunk_debug_dump_enabled:
                self._dump_chunk_debug(idx, chunk, user_prompt, output, state.snapshot())

            if on_chunk_progress:
                partial_summary, _ = assemble_chunk_summaries(chunk_outputs)
                on_chunk_progress(idx + 1, len(chunks), partial_summary)

        final_summary, assembly_logs = assemble_chunk_summaries(chunk_outputs)

        # 提取最后一块生成的文档主题（如果有）
        doc_topic = state.snapshot().get("agent", {}).get("文档主题", "").strip()
        if doc_topic:
            # 将主题添加到摘要开头
            final_summary = f"{doc_topic}\n{final_summary}"
            assembly_logs.append(f"已添加文档主题: {doc_topic}")

        return ChunkedSummaryResult(
            summary_text=final_summary,
            chunk_total=len(chunks),
            chunk_done=len(chunks),
            assembly_logs=assembly_logs,
            warnings=warnings,
        )

    def _prepare_program_state(
        self,
        state: DynamicStateManager,
        chunk: TranscriptChunk,
        idx: int,
        all_chunks: list[TranscriptChunk],
        chunk_outputs: list[str],
    ) -> None:
        state.update_program(
            {
                "当前块时间范围": chunk.time_range_hms,
                "流程标记": f"当前块索引={idx}, 总块数={len(all_chunks)}, 是否最后一块={idx == len(all_chunks) - 1}",
            }
        )

        if idx <= 0:
            return

        prev_chunk = all_chunks[idx - 1]
        prev_summary = chunk_outputs[-1] if chunk_outputs else ""
        # 清理 state_ops 块后再取末尾，避免将 state_ops 内容传递给下一块
        prev_summary_cleaned = strip_state_instruction_blocks(prev_summary)

        # 取末尾片段并去重
        prev_summary_tail = prev_summary_cleaned[-self._prev_summary_tail_chars_j:]
        prev_summary_tail = remove_duplicate_paragraphs(prev_summary_tail)

        state.set_program_value(
            "前块转录文本末尾",
            f"```plaintext\n{tail_timestamp_lines(prev_chunk, self._prev_tail_timestamp_lines_m)}\n```",
        )
        state.set_program_value(
            "前块总结片段末尾",
            f"```markdown\n{prev_summary_tail}\n```",
        )

    async def _call_llm_with_retry(
        self,
        user_prompt: str,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        attempt = 0
        last_error: Exception | None = None
        while attempt < self._llm_call_retry_max:
            attempt += 1
            try:
                return await self._call_llm_once(user_prompt, on_partial=on_partial)
            except asyncio.CancelledError:
                raise
            except TaskCancelledError:
                raise
            except Exception as e:
                last_error = e
                if attempt >= self._llm_call_retry_max:
                    break
                logger.warning(f"[ChunkedSummarizer] LLM 调用失败，准备重试 ({attempt}/{self._llm_call_retry_max}): {e}")
                await asyncio.sleep(min(2.0, 0.3 * attempt))

        raise RuntimeError(f"分块总结调用失败: {last_error}")

    async def _call_llm_once(
        self,
        user_prompt: str,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        self._ensure_not_cancelled()
        messages = [
            LLMMessage(role="system", content=self._chunk_system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        response_chunks: list[str] = []
        llm_error: LLMError | None = None
        last_emit_at = 0.0
        last_emit_len = 0

        def callback(chunk: str | LLMError):
            nonlocal llm_error, last_emit_at, last_emit_len
            if self._cancel_check and self._cancel_check():
                raise asyncio.CancelledError()
            if isinstance(chunk, LLMError):
                llm_error = chunk
                return
            response_chunks.append(chunk)
            if on_partial:
                now = asyncio.get_event_loop().time()
                if now - last_emit_at >= 0.5:
                    text = "".join(response_chunks)
                    try:
                        on_partial(text)
                    except Exception as e:
                        logger.warning(f"[ChunkedSummarizer] 分块流式回调失败: {e}")
                    last_emit_at = now
                    last_emit_len = len(text)

        await self._llm_client.response(messages=messages, resp_callback=callback)
        if llm_error:
            raise llm_error
        self._ensure_not_cancelled()
        final_text = "".join(response_chunks)

        # 截断state_ops之后的所有内容（防止思考过程泄露）
        final_text = truncate_after_state_ops(final_text)

        if on_partial and len(final_text) != last_emit_len:
            try:
                on_partial(final_text)
            except Exception as e:
                logger.warning(f"[ChunkedSummarizer] 分块最终流式回调失败: {e}")
        return final_text

    def _ensure_not_cancelled(self) -> None:
        if self._cancel_check and self._cancel_check():
            raise TaskCancelledError("任务已取消，停止分块总结。")

    def _dump_chunk_debug(
        self,
        idx: int,
        chunk: TranscriptChunk,
        input_text: str,
        output_text: str,
        state_snapshot: dict,
    ) -> None:
        try:
            chunk_dir = self._chunk_debug_dump_dir / f"chunk_{idx:02d}"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            (chunk_dir / "input.md").write_text(input_text, encoding="utf-8")
            (chunk_dir / "output.md").write_text(output_text, encoding="utf-8")
            (chunk_dir / "chunk_meta.json").write_text(
                json.dumps(
                    {
                        "index": idx,
                        "start_timestamp_sec": chunk.start_timestamp_sec,
                        "end_timestamp_sec": chunk.end_timestamp_sec,
                        "line_count": chunk.line_count,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (chunk_dir / "state_after.json").write_text(
                json.dumps(state_snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[ChunkedSummarizer] 分块调试落盘失败: {e}")
