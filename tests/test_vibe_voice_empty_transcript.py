"""
VibeVoice 转录静默空结果事故回归测试（bugfix: vibevoice-empty-transcript）。

事故背景（生产 21010 任务 89e565dd，2026-08-10/11）：
_generation_budget 仅按语音 token 速率（24000Hz/3200=7.5 tokens/s）封顶
max_new_tokens，未预留文本 JSON 分段开销 → 372.4s 音频预算仅 2980 →
模型输出被截断 → JSON 数组无闭合括号 → vibevoice 库括号匹配逻辑把内容
截成空串 → json.loads("") 失败 → 静默返回 [] → 空转录照样 COMPLETED。

修复三层防御：
1. P0 核心：预算公式预留文本空间（speech + text，min 全局上限）
2. P0 核心：截断时自动放大预算重试（同前缀确定性续写可补全），
   达到全局上限仍截断 → 显式 TranscriptionError（禁止静默空结果）
3. P0 防御：worker 层空 segments 校验 → 任务显式 FAILED
   （覆盖所有 transcriber 类型与 multipart 分片路径）
附带：generate_topic_for_task 空转录守卫（不浪费 LLM 调用）
"""

import asyncio
from types import SimpleNamespace

import pytest
import torch

from src.main.python.sheng_wen.llm.llm_worker import generate_topic_for_task
from src.main.python.sheng_wen.transcriber.transcriber import (
    TranscriptionError,
    TranscriptionResult,
)
from src.main.python.sheng_wen.transcriber.transcriber_worker import TranscriberWorker
from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
    VibeVoiceAsrTranscriber,
)


def _empty_result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[],
        transcription_time=1.0,
        real_time_factor=0.0,
        total_time=1.0,
        model_load_time=0.0,
        audio_duration=0.0,
        language="unknown",
        language_probability=0.0,
    )


class _FakeProcessor:
    """最小可用的 processor 替身：不加载真实模型与分词器。"""

    pad_id = 0

    class _Tok:
        eos_token_id = 1

    tokenizer = _Tok()

    def __call__(self, **kwargs):
        return {
            "input_ids": torch.zeros((1, 10), dtype=torch.long),
            "speech_tensors": torch.zeros(
                (1, 24000)
            ),  # 1 秒音频，budget 被 monkeypatch 固定
        }

    def decode(self, ids, skip_special_tokens=True):
        return '[{"Start time": "0:00.000", "End time": "0:00.002", "Content": "你好"}]'

    def post_process_transcription(self, text):
        return [
            {
                "start_time": "0:00.000",
                "end_time": "0:00.002",
                "text": "你好",
                "speaker_id": "",
            }
        ]


def _make_transcriber(max_new_tokens: int = 8192) -> VibeVoiceAsrTranscriber:
    transcriber = VibeVoiceAsrTranscriber(
        model_path="/tmp/model",
        max_new_tokens=max_new_tokens,
    )
    transcriber.processor = _FakeProcessor()  # pyright: ignore[reportAttributeAccessIssue]
    return transcriber


# ---------------------------------------------------------------- 预算公式


class TestGenerationBudget:
    """预算必须为文本 JSON 分段预留空间（事故根因）。"""

    def test_short_audio_keeps_fast_path(self):
        t = _make_transcriber()
        assert t._generation_budget(60.0) == 2048

    def test_medium_audio_has_text_headroom(self):
        t = _make_transcriber()
        assert t._generation_budget(180.0) == 2886

    def test_chunk_duration_budget(self):
        t = _make_transcriber()
        assert t._generation_budget(360.0) == 4500

    def test_incident_audio_budget_exceeds_old_2980(self):
        # 事故音频 372.4s：旧公式 2980（截断），新公式必须显著更高且不顶全局上限
        t = _make_transcriber()
        budget = t._generation_budget(372.4)
        assert budget > 2980
        assert budget < 8192

    def test_very_long_audio_capped_at_global_max(self):
        t = _make_transcriber()
        assert t._generation_budget(2000.0) == 8192

    def test_zero_duration_floor(self):
        t = _make_transcriber()
        assert t._generation_budget(0.0) == 2048


# ---------------------------------------------------------------- 截断重试


class TestTruncationRetry:
    """截断时自动放大预算重试，禁止静默空结果。"""

    def test_truncation_retries_with_larger_budget(self, monkeypatch):
        transcriber = _make_transcriber()
        monkeypatch.setattr(transcriber, "_ensure_loaded", lambda: None)
        monkeypatch.setattr(transcriber, "_generation_budget", lambda _d: 2980)

        generated = []

        def fake_generate(**kwargs):
            generated.append(kwargs)
            budget = kwargs["max_new_tokens"]
            n_tokens = 10 + (2980 if budget == 2980 else 500)
            return SimpleNamespace(
                sequences=torch.zeros((1, n_tokens), dtype=torch.long)
            )

        transcriber.model = SimpleNamespace(generate=fake_generate)  # pyright: ignore[reportAttributeAccessIssue]

        result = transcriber.transcribe("audio.mp3")

        assert len(generated) == 2, "截断后应放大预算重试一次"
        assert generated[1]["max_new_tokens"] == 5960
        assert len(result.segments) == 1
        assert result.segments[0]["text"] == "你好"

    def test_truncation_retries_until_global_max(self, monkeypatch):
        transcriber = _make_transcriber()
        monkeypatch.setattr(transcriber, "_ensure_loaded", lambda: None)
        monkeypatch.setattr(transcriber, "_generation_budget", lambda _d: 2980)

        generated = []

        def fake_generate(**kwargs):
            generated.append(kwargs)
            budget = kwargs["max_new_tokens"]
            n_tokens = 10 + budget  # 始终截断
            return SimpleNamespace(
                sequences=torch.zeros((1, n_tokens), dtype=torch.long)
            )

        transcriber.model = SimpleNamespace(generate=fake_generate)  # pyright: ignore[reportAttributeAccessIssue]

        with pytest.raises(TranscriptionError, match="仍被截断"):
            transcriber.transcribe("audio.mp3")

        assert len(generated) == 3, "2980 → 5960 → 8192 共三次生成后显式失败"

    def test_no_truncation_single_generation(self, monkeypatch):
        transcriber = _make_transcriber()
        monkeypatch.setattr(transcriber, "_ensure_loaded", lambda: None)
        monkeypatch.setattr(transcriber, "_generation_budget", lambda _d: 2980)

        generated = []

        def fake_generate(**kwargs):
            generated.append(kwargs)
            return SimpleNamespace(
                sequences=torch.zeros((1, 10 + 100), dtype=torch.long)
            )

        transcriber.model = SimpleNamespace(generate=fake_generate)  # pyright: ignore[reportAttributeAccessIssue]

        result = transcriber.transcribe("audio.mp3")

        assert len(generated) == 1
        assert result.segments[0]["text"] == "你好"


# ---------------------------------------------------------------- worker 空结果


class TestWorkerEmptyResult:
    """空转录必须显式 FAILED，禁止静默 COMPLETED（最后防线，覆盖所有 transcriber）。"""

    @pytest.mark.asyncio
    async def test_empty_segments_marks_task_failed(self, monkeypatch, tmp_path):
        import src.main.python.sheng_wen.transcriber.transcriber_worker as worker_module
        from src.main.python.sheng_wen import task_updater as updater_module

        audio_file = tmp_path / "audio.mp3"
        audio_file.write_bytes(b"fake-audio")

        calls = []

        async def fake_update_and_notify(task_id, updates, **kwargs):
            calls.append((task_id, updates))

        monkeypatch.setattr(updater_module, "update_and_notify", fake_update_and_notify)
        monkeypatch.setattr(worker_module, "get_audio_duration", lambda _: 10.0)

        class EmptyTranscriber:
            def transcribe(self, path, progress_callback=None, cancel_check=None):
                if progress_callback:
                    progress_callback(1.0)
                return _empty_result()

        worker = TranscriberWorker("test", EmptyTranscriber(), None)  # pyright: ignore[reportArgumentType]
        # 虚构 task_id 不在 DB → _is_task_deleted 视为已删除 → cancel_check 恒 True，
        # 走取消分支测不到 FAILED；monkeypatch 掉让流程走通
        monkeypatch.setattr(worker, "_is_task_deleted", lambda _: False)

        submitted = []

        def sync_submit(self, coro):
            submitted.append(coro)

        monkeypatch.setattr(worker, "_submit_coro", sync_submit.__get__(worker))

        worker.process_task(
            {
                "audio_file": str(audio_file),
                "output_file": str(tmp_path / "out.txt"),
                "task_id": "task-empty",
                "summary_mode": "none",
            }
        )
        await asyncio.gather(*submitted)

        failed_updates = [
            u for tid, u in calls if tid == "task-empty" and u.get("status") == "FAILED"
        ]
        completed_updates = [
            u
            for tid, u in calls
            if tid == "task-empty" and u.get("status") == "COMPLETED"
        ]
        assert failed_updates, "空转录必须标记 FAILED"
        assert failed_updates[0]["error_message"], "FAILED 必须带明确错误信息"
        assert not completed_updates, "禁止静默写空 transcript 并 COMPLETED"

    @pytest.mark.asyncio
    async def test_whitespace_only_segments_marks_task_failed(
        self, monkeypatch, tmp_path
    ):
        """segments 非空但 text 全空（静音音频输出合法 JSON）同样必须 FAILED。"""
        import src.main.python.sheng_wen.transcriber.transcriber_worker as worker_module
        from src.main.python.sheng_wen import task_updater as updater_module

        audio_file = tmp_path / "audio.mp3"
        audio_file.write_bytes(b"fake-audio")

        calls = []

        async def fake_update_and_notify(task_id, updates, **kwargs):
            calls.append((task_id, updates))

        monkeypatch.setattr(updater_module, "update_and_notify", fake_update_and_notify)
        monkeypatch.setattr(worker_module, "get_audio_duration", lambda _: 10.0)

        class BlankTextTranscriber:
            def transcribe(self, path, progress_callback=None, cancel_check=None):
                if progress_callback:
                    progress_callback(1.0)
                return TranscriptionResult(
                    segments=[
                        {"start": 0.0, "end": 1.0, "text": "  ", "speaker_id": ""}
                    ],
                    transcription_time=1.0,
                    real_time_factor=0.0,
                    total_time=1.0,
                    model_load_time=0.0,
                    audio_duration=1.0,
                    language="unknown",
                    language_probability=0.0,
                )

        worker = TranscriberWorker("test", BlankTextTranscriber(), None)  # pyright: ignore[reportArgumentType]
        monkeypatch.setattr(worker, "_is_task_deleted", lambda _: False)

        submitted = []

        def sync_submit(self, coro):
            submitted.append(coro)

        monkeypatch.setattr(worker, "_submit_coro", sync_submit.__get__(worker))

        worker.process_task(
            {
                "audio_file": str(audio_file),
                "output_file": str(tmp_path / "out.txt"),
                "task_id": "task-blank",
                "summary_mode": "none",
            }
        )
        await asyncio.gather(*submitted)

        failed_updates = [
            u for tid, u in calls if tid == "task-blank" and u.get("status") == "FAILED"
        ]
        completed_updates = [
            u
            for tid, u in calls
            if tid == "task-blank" and u.get("status") == "COMPLETED"
        ]
        assert failed_updates, "空文本 segments 必须标记 FAILED"
        assert not completed_updates, "空文本 segments 禁止 COMPLETED"

    @pytest.mark.asyncio
    async def test_empty_segments_multipart_part_failed(self, monkeypatch, tmp_path):
        import src.main.python.sheng_wen.transcriber.transcriber_worker as worker_module
        from src.main.python.sheng_wen import task_parts as parts_module
        from src.main.python.sheng_wen import task_updater as updater_module

        audio_file = tmp_path / "audio.mp3"
        audio_file.write_bytes(b"fake-audio")

        part_calls = []
        task_calls = []

        async def fake_update_and_notify(task_id, updates, **kwargs):
            task_calls.append((task_id, updates))

        def fake_update_task_part(task_id, index, updates):
            part_calls.append((task_id, index, updates))

        monkeypatch.setattr(updater_module, "update_and_notify", fake_update_and_notify)
        monkeypatch.setattr(parts_module, "update_task_part", fake_update_task_part)
        monkeypatch.setattr(worker_module, "get_audio_duration", lambda _: 10.0)

        class EmptyTranscriber:
            def transcribe(self, path, progress_callback=None, cancel_check=None):
                if progress_callback:
                    progress_callback(1.0)
                return _empty_result()

        worker = TranscriberWorker("test", EmptyTranscriber(), None)  # pyright: ignore[reportArgumentType]
        monkeypatch.setattr(worker, "_is_task_deleted", lambda _: False)

        submitted = []

        def sync_submit(self, coro):
            submitted.append(coro)

        monkeypatch.setattr(worker, "_submit_coro", sync_submit.__get__(worker))

        worker.process_task(
            {
                "audio_file": str(audio_file),
                "output_file": str(tmp_path / "out.txt"),
                "task_id": "task-multipart",
                "multipart_part": {"index": 2, "duration": 60.0},
                "summary_mode": "none",
            }
        )
        await asyncio.gather(*submitted)

        failed_parts = [p for p in part_calls if p[2].get("status") == "FAILED"]
        assert failed_parts, "multipart 分片空转录必须标记 FAILED"
        assert failed_parts[0][1] == 2
        # P1-2（fix: multipart-main-summary-leak 评审修订）：分P子任务失败只写
        # task_parts，不写主行 FAILED——父任务终态由 merge finalize 汇总收敛
        failed_tasks = [
            u
            for tid, u in task_calls
            if tid == "task-multipart" and u.get("status") == "FAILED"
        ]
        assert not failed_tasks, "分P子任务空转录不得写主行 FAILED"


# ---------------------------------------------------------------- 附带：topic 空守卫


class TestTopicEmptyTranscript:
    @pytest.mark.asyncio
    async def test_empty_transcript_skips_llm(self):
        calls = []

        class FakeLLMWorker:
            async def generate_topic(self, transcript):
                calls.append(transcript)
                return "标题"

        await generate_topic_for_task(FakeLLMWorker(), "task-1", "")
        assert calls == [], "空转录不应发起 LLM 标题生成"

    @pytest.mark.asyncio
    async def test_whitespace_transcript_skips_llm(self):
        calls = []

        class FakeLLMWorker:
            async def generate_topic(self, transcript):
                calls.append(transcript)
                return "标题"

        await generate_topic_for_task(FakeLLMWorker(), "task-1", "   \n  ")
        assert calls == []
