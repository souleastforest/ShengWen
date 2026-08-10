"""总结空结果防御测试（bugfix: agent/standard 总结空结果静默 COMPLETED）。

背景：生产环境一次 agent 增强总结中，LLM 返回了空响应（HTTP 成功但 0 个文本
delta），旧逻辑把空总结直接写入并 COMPLETED（summary=0 字符）。本测试验证
修复后的行为：

- ChunkedSummarizer._call_llm_once：空响应 / 纯指令块 → 抛 LLMError（触发重试）
- _call_llm_with_retry：空响应自动重试；重试耗尽 → RuntimeError
- summarize()：组装结果为空 → 抛 RuntimeError（终检闸门）
- summarize()：文档主题的 {{}} 包裹被清理（附带修复）
- LLMWorker.process_task：agent 分块空 → fallback standard（有内容则 COMPLETED）
- LLMWorker.process_task：agent 分块空 + standard 空 → FAILED（不再静默成功）
- LLMWorker.process_task：standard 模式空 → FAILED
"""

import uuid

import pytest

from src.main.python.sheng_wen.config.settings import config
from src.main.python.sheng_wen.db import TaskStatus, db
from src.main.python.sheng_wen.llm.llm import LLMError
from src.main.python.sheng_wen.llm.llm_worker import LLMWorker
from src.main.python.sheng_wen.summarization.chunked_summarizer import (
    ChunkedSummarizer,
)


# ---- FakeLLM：按调用次数返回预设 chunk 序列 ---------------------------------


class FakeLLM:
    """可编程 LLM：responses[i] = 第 i 次调用的 delta 序列（元素为 str）。"""

    def __init__(self, responses: list[list[str]]):
        self._responses = list(responses)
        self.call_count = 0

    async def response(self, messages, resp_callback, stream=True, timeout=60):
        idx = min(self.call_count, len(self._responses) - 1)
        self.call_count += 1
        for delta in self._responses[idx]:
            resp_callback(delta)


def _make_summarizer(fake_llm, retry_max: int | None = None) -> ChunkedSummarizer:
    return ChunkedSummarizer(
        llm_client=fake_llm,
        chunk_system_prompt="你是分块总结助手，输出正文后再用 ```state_ops``` 块维护状态。",
        chunk_target_duration_sec=2400,
        chunk_min_duration_sec=1800,
        chunk_max_duration_sec=3000,
        boundary_jump_sec=10,
        prev_tail_timestamp_lines_m=5,
        prev_summary_tail_chars_j=200,
        llm_call_retry_max=retry_max or config.summarization.llm_call_retry_max,
        max_agent_value_chars=2000,
    )


SIMPLE_TRANSCRIPT = (
    "000000第一段内容，介绍视频主题。\n"
    "000010第二段内容，深入讲解细节。\n"
    "000020第三段内容，总结要点。"
)


# ---- ChunkedSummarizer 空响应防御 ------------------------------------------


@pytest.mark.asyncio
async def test_call_llm_once_empty_raises():
    fake = FakeLLM([[]])  # 0 个 delta
    summ = _make_summarizer(fake)

    with pytest.raises(LLMError):
        await summ._call_llm_once("用户提示")


@pytest.mark.asyncio
async def test_call_llm_once_pure_instruction_block_raises():
    # 输出只有 state_ops 指令块（剥离后为空）→ 同样视为失败
    fake = FakeLLM(
        [['```state_ops\n{"action": "set", "data": {"文档主题": "X"}}\n```']]
    )
    summ = _make_summarizer(fake)

    with pytest.raises(LLMError):
        await summ._call_llm_once("用户提示")


@pytest.mark.asyncio
async def test_call_llm_with_retry_empty_then_success():
    # 前 2 次空响应，第 3 次有正文 → 自动重试成功
    fake = FakeLLM([[], [], ["最终正文内容"]])
    summ = _make_summarizer(fake, retry_max=3)

    result = await summ._call_llm_with_retry("用户提示")

    assert result == "最终正文内容"
    assert fake.call_count == 3


@pytest.mark.asyncio
async def test_call_llm_with_retry_always_empty_raises():
    fake = FakeLLM([[], [], []])
    summ = _make_summarizer(fake, retry_max=3)

    with pytest.raises(RuntimeError, match="分块总结调用失败"):
        await summ._call_llm_with_retry("用户提示")
    assert fake.call_count == 3  # 重试耗尽，不再静默返回空


@pytest.mark.asyncio
async def test_summarize_all_empty_raises():
    # 逐块校验通过（{{chunk_0_ended}} 非指令块），但组装去标记后为空 → 终检闸门
    fake = FakeLLM([["{{chunk_0_ended}}"]])
    summ = _make_summarizer(fake, retry_max=1)

    with pytest.raises(RuntimeError, match="分块总结结果为空"):
        await summ.summarize(transcript_text=SIMPLE_TRANSCRIPT)


@pytest.mark.asyncio
async def test_summarize_normal_pass():
    fake = FakeLLM(
        [
            [
                "正文：这是正常的总结内容。",
                '```state_ops\n{"action": "set", "data": {"文档主题": "测试主题"}}\n```',
            ]
        ]
    )
    summ = _make_summarizer(fake)

    result = await summ.summarize(transcript_text=SIMPLE_TRANSCRIPT)

    assert "正文：这是正常的总结内容。" in result.summary_text
    assert result.chunk_total == 1
    assert result.chunk_done == 1


@pytest.mark.asyncio
async def test_summarize_topic_marker_cleaned():
    # 文档主题带 {{}} 包裹时（附带修复：拼接前剥除包裹）
    fake = FakeLLM(
        [
            [
                "正文内容。",
                '```state_ops\n{"action": "set", "data": {"文档主题": "{{主题A}}"}}\n```',
            ]
        ]
    )
    summ = _make_summarizer(fake)

    result = await summ.summarize(transcript_text=SIMPLE_TRANSCRIPT)

    assert result.summary_text.startswith("主题A\n")  # {{}} 被清理
    assert "{{" not in result.summary_text


# ---- LLMWorker.process_task 集成（隔离 db） ---------------------------------


def _save_task(status: TaskStatus = TaskStatus.SUMMARIZING) -> str:
    tid = str(uuid.uuid4())
    db.save_task(
        tid,
        {
            "id": tid,
            "video_url": "https://www.bilibili.com/video/BV1TESTEMPTY",
            "status": status.value,
            "summary_mode": "agent",
            "created_at": "2026-08-10 00:00:00",
            "progress": 0.0,
            "title": "空总结防御测试",
        },
    )
    return tid


async def _run_process_task(
    fake: FakeLLM,
    summary_mode: str = "agent",
) -> tuple[str, dict]:
    tid = _save_task()
    worker = LLMWorker(name="LLMWorker", llm_client=fake)
    worker.system_prompt = "你是总结助手，直接输出总结正文。"

    in_file = f"temp/{tid}_re.txt"
    out_file = f"temp/{tid}_re_summary.md"
    import os

    os.makedirs("temp", exist_ok=True)
    with open(in_file, "w", encoding="utf-8") as f:
        f.write(SIMPLE_TRANSCRIPT)

    try:
        await worker.process_task(
            {
                "task_id": tid,
                "intermediate_file_path": in_file,
                "output_file": out_file,
                "summary_mode": summary_mode,
            }
        )
    finally:
        for path in (in_file, out_file):
            if os.path.exists(path):
                os.remove(path)

    return tid, db.get_task(tid)


@pytest.mark.asyncio
async def test_process_task_agent_empty_falls_back_to_standard():
    # chunked 重试 3 次全空 → fallback standard（第 4 次调用返回正文）
    retry_max = config.summarization.llm_call_retry_max
    fake = FakeLLM([[] for _ in range(retry_max)] + [["标准模式总结正文"]])
    tid, task = await _run_process_task(fake, summary_mode="agent")

    assert task["status"] == TaskStatus.COMPLETED.value
    assert (task["summary"] or "").strip() == "标准模式总结正文"
    assert "fallback_triggered" in (task.get("summary_meta") or "")
    assert fake.call_count == retry_max + 1


@pytest.mark.asyncio
async def test_process_task_agent_empty_and_standard_empty_fails():
    retry_max = config.summarization.llm_call_retry_max
    fake = FakeLLM([[] for _ in range(retry_max + 1)])  # chunked 空 + standard 也空
    tid, task = await _run_process_task(fake, summary_mode="agent")

    assert task["status"] == TaskStatus.FAILED.value
    assert "回退" in (task.get("error_message") or "")


@pytest.mark.asyncio
async def test_process_task_standard_empty_fails():
    fake = FakeLLM([[]])
    tid, task = await _run_process_task(fake, summary_mode="standard")

    assert task["status"] == TaskStatus.FAILED.value
    assert (task.get("error_message") or "") != ""


@pytest.mark.asyncio
async def test_process_task_agent_normal_completes():
    fake = FakeLLM([["正常分块总结正文"]])
    tid, task = await _run_process_task(fake, summary_mode="agent")

    assert task["status"] == TaskStatus.COMPLETED.value
    assert (task["summary"] or "").strip() == "正常分块总结正文"
