from src.main.python.sheng_wen.transcriber.transcriber import TranscriptionResult
from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
    VibeVoiceAsrTranscriber,
)


def _build_result(
    segments: list[dict],
    transcription_time: float = 1.0,
    audio_duration: float = 0.0,
) -> TranscriptionResult:
    return TranscriptionResult(
        segments=segments,
        transcription_time=transcription_time,
        real_time_factor=0.0,
        total_time=transcription_time,
        model_load_time=0.0,
        audio_duration=audio_duration,
        language="unknown",
        language_probability=0.0,
    )


class TestMergeChunkResults:
    def test_single_chunk_no_offset(self):
        chunk_results = [
            (
                _build_result(
                    [
                        {"start": 0.0, "end": 2.0, "text": "hello", "speaker_id": "A"},
                        {"start": 5.0, "end": 8.0, "text": "world", "speaker_id": "A"},
                    ],
                    transcription_time=2.5,
                    audio_duration=8.0,
                ),
                0.0,
            )
        ]

        result = VibeVoiceAsrTranscriber._merge_chunk_results(
            chunk_results, model_load_time=1.2
        )

        assert len(result.segments) == 2
        assert result.segments[0]["start"] == 0.0
        assert result.segments[1]["start"] == 5.0
        assert result.audio_duration == 8.0

    def test_two_chunks_with_offset(self):
        chunk_results = [
            (
                _build_result(
                    [
                        {"start": 0.0, "end": 3.0, "text": "chunk1", "speaker_id": "A"}
                    ],
                    transcription_time=2.0,
                    audio_duration=3.0,
                ),
                0.0,
            ),
            (
                _build_result(
                    [
                        {"start": 1.0, "end": 4.0, "text": "chunk2", "speaker_id": "B"}
                    ],
                    transcription_time=3.0,
                    audio_duration=4.0,
                ),
                180.0,
            ),
        ]

        result = VibeVoiceAsrTranscriber._merge_chunk_results(
            chunk_results, model_load_time=1.5
        )

        assert len(result.segments) == 2
        assert result.segments[0]["start"] == 0.0
        assert result.segments[1]["start"] == 181.0
        assert result.segments[1]["end"] == 184.0
        assert result.audio_duration == 184.0
        assert result.transcription_time == 5.0

    def test_empty_chunk_segments(self):
        chunk_results = [
            (
                _build_result(
                    [],
                    transcription_time=0.5,
                    audio_duration=0.0,
                ),
                0.0,
            )
        ]

        result = VibeVoiceAsrTranscriber._merge_chunk_results(
            chunk_results, model_load_time=0.3
        )

        assert result.segments == []
        assert result.audio_duration == 0.0
        assert result.transcription_time == 0.5


class TestNeedsChunking:
    def test_short_audio(self):
        assert VibeVoiceAsrTranscriber._needs_chunking(60.0) is False

    def test_long_audio(self):
        assert VibeVoiceAsrTranscriber._needs_chunking(600.0) is True

    def test_boundary(self):
        assert VibeVoiceAsrTranscriber._needs_chunking(300.0) is False

    def test_zero(self):
        assert VibeVoiceAsrTranscriber._needs_chunking(0.0) is False
