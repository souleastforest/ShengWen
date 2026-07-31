from types import SimpleNamespace

from src.main.python.sheng_wen.transcriber import transcriber_worker as worker_module
from src.main.python.sheng_wen.transcriber.transcriber import TranscriptionResult
from src.main.python.sheng_wen.transcriber.transcriber_worker import TranscriberWorker


def _result(text: str, duration: float = 5.0) -> TranscriptionResult:
    return TranscriptionResult(
        segments=[{"start": 1.0, "end": 2.0, "text": text}],
        transcription_time=0.5,
        real_time_factor=0.1,
        total_time=0.6,
        model_load_time=0.2,
        audio_duration=duration,
        language="zh",
        language_probability=0.9,
    )


def _config(threshold=10, chunk_duration=5, fallback=True):
    return SimpleNamespace(
        whisper=SimpleNamespace(
            asr_chunk_threshold_sec=threshold,
            asr_chunk_duration_sec=chunk_duration,
            asr_chunk_oom_fallback=fallback,
        )
    )


def test_long_audio_chunks_are_serial_and_offsets_are_merged(monkeypatch):
    calls = []
    cleanup_calls = []
    monkeypatch.setattr(worker_module, "config", _config())
    monkeypatch.setattr(worker_module, "get_audio_duration", lambda _: 15.0)
    monkeypatch.setattr(
        worker_module,
        "split_audio_into_chunks",
        lambda *_args, **_kwargs: [("chunk-0.wav", 0.0), ("chunk-1.wav", 5.0), ("chunk-2.wav", 10.0)],
    )
    monkeypatch.setattr(worker_module, "cleanup_chunks", lambda chunks: cleanup_calls.append(chunks))

    class FakeTranscriber:
        def transcribe(self, path, progress_callback=None, cancel_check=None):
            calls.append(path)
            if progress_callback:
                progress_callback(1.0)
            return _result(path)

    worker = TranscriberWorker("test", FakeTranscriber(), None)
    progress = []
    result = worker._transcribe_audio_with_chunking("audio.mp3", progress.append, lambda: False)

    assert calls == ["chunk-0.wav", "chunk-1.wav", "chunk-2.wav"]
    assert [segment["start"] for segment in result.segments] == [1.0, 6.0, 11.0]
    assert progress == sorted(progress)
    assert progress[-1] == 1.0
    assert cleanup_calls


def test_cuda_oom_falls_back_to_chunks(monkeypatch):
    calls = []
    monkeypatch.setattr(worker_module, "config", _config(threshold=720, chunk_duration=5, fallback=True))
    monkeypatch.setattr(worker_module, "get_audio_duration", lambda _: 10.0)
    monkeypatch.setattr(
        worker_module,
        "split_audio_into_chunks",
        lambda *_args, **_kwargs: [("chunk-0.wav", 0.0), ("chunk-1.wav", 5.0)],
    )
    monkeypatch.setattr(worker_module, "cleanup_chunks", lambda _chunks: None)
    monkeypatch.setattr(TranscriberWorker, "_clear_cuda_cache", staticmethod(lambda: None))

    class FakeTranscriber:
        def transcribe(self, path, progress_callback=None, cancel_check=None):
            calls.append(path)
            if path == "audio.mp3":
                raise RuntimeError("CUDA out of memory")
            return _result(path)

    worker = TranscriberWorker("test", FakeTranscriber(), None)
    result = worker._transcribe_audio_with_chunking("audio.mp3", lambda _: None, lambda: False)

    assert calls == ["audio.mp3", "chunk-0.wav", "chunk-1.wav"]
    assert result.audio_duration == 10.0
    assert result.segments[-1]["start"] == 6.0


def test_short_audio_keeps_single_transcriber_call(monkeypatch):
    calls = []
    monkeypatch.setattr(worker_module, "config", _config())
    monkeypatch.setattr(worker_module, "get_audio_duration", lambda _: 8.0)

    class FakeTranscriber:
        def transcribe(self, path, progress_callback=None, cancel_check=None):
            calls.append(path)
            return _result(path, duration=8.0)

    worker = TranscriberWorker("test", FakeTranscriber(), None)
    result = worker._transcribe_audio_with_chunking("audio.mp3", lambda _: None, lambda: False)

    assert calls == ["audio.mp3"]
    assert result.audio_duration == 8.0
