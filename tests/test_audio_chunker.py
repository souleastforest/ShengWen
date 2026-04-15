import os
import math
import struct
import wave

from src.main.python.sheng_wen.transcriber.audio_chunker import (
    cleanup_chunks,
    get_audio_duration,
    split_audio_into_chunks,
)


def _create_sine_wav(
    path, duration_sec=5.0, sample_rate=24000, frequency=440.0
) -> None:
    amplitude = 32767 * 0.3
    frame_count = int(duration_sec * sample_rate)

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        for frame_index in range(frame_count):
            sample = int(
                amplitude
                * math.sin(2.0 * math.pi * frequency * frame_index / sample_rate)
            )
            wav_file.writeframes(struct.pack("<h", sample))


class TestGetAudioDuration:
    def test_returns_positive_duration_for_valid_wav(self, tmp_path):
        wav_path = tmp_path / "valid.wav"
        _create_sine_wav(wav_path, duration_sec=5.0)

        duration = get_audio_duration(str(wav_path))

        assert abs(duration - 5.0) <= 0.5

    def test_returns_zero_for_nonexistent_file(self):
        assert get_audio_duration("/nonexistent/audio.wav") == 0.0

    def test_long_wav_duration(self, tmp_path):
        wav_path = tmp_path / "long.wav"
        _create_sine_wav(wav_path, duration_sec=12.0)

        duration = get_audio_duration(str(wav_path))

        assert abs(duration - 12.0) <= 0.5


class TestSplitAudioIntoChunks:
    def test_short_audio_returns_original_file(self, tmp_path):
        wav_path = tmp_path / "short.wav"
        _create_sine_wav(wav_path, duration_sec=5.0)

        chunks = split_audio_into_chunks(str(wav_path), chunk_duration=180.0)

        assert chunks == [(str(wav_path), 0.0)]

    def test_long_audio_produces_multiple_chunks(self, tmp_path):
        wav_path = tmp_path / "long.wav"
        _create_sine_wav(wav_path, duration_sec=12.0)

        chunks = split_audio_into_chunks(str(wav_path), chunk_duration=5.0)

        assert len(chunks) >= 2
        assert all(chunk_path for chunk_path, _ in chunks)
        assert all(os.path.exists(chunk_path) for chunk_path, _ in chunks)
        cleanup_chunks(chunks)

    def test_chunk_offsets_are_monotonic(self, tmp_path):
        wav_path = tmp_path / "offsets.wav"
        _create_sine_wav(wav_path, duration_sec=12.0)

        chunks = split_audio_into_chunks(str(wav_path), chunk_duration=5.0)

        offsets = [offset for _, offset in chunks]
        assert offsets == sorted(offsets)
        assert offsets == [0.0, 5.0, 10.0]
        cleanup_chunks(chunks)

    def test_custom_output_dir(self, tmp_path):
        wav_path = tmp_path / "custom.wav"
        output_dir = tmp_path / "chunks"
        _create_sine_wav(wav_path, duration_sec=12.0)

        chunks = split_audio_into_chunks(
            str(wav_path), chunk_duration=5.0, output_dir=str(output_dir)
        )

        assert len(chunks) >= 2
        assert all(str(output_dir) in chunk_path for chunk_path, _ in chunks)
        assert all(os.path.exists(chunk_path) for chunk_path, _ in chunks)


class TestCleanupChunks:
    def test_cleanup_removes_chunk_files(self, tmp_path):
        wav_path = tmp_path / "cleanup.wav"
        _create_sine_wav(wav_path, duration_sec=12.0)

        chunks = split_audio_into_chunks(str(wav_path), chunk_duration=5.0)
        chunk_paths = [chunk_path for chunk_path, _ in chunks]

        cleanup_chunks(chunks)

        assert all(not os.path.exists(chunk_path) for chunk_path in chunk_paths)

    def test_cleanup_does_not_remove_original(self, tmp_path):
        wav_path = tmp_path / "original.wav"
        _create_sine_wav(wav_path, duration_sec=5.0)

        chunks = split_audio_into_chunks(str(wav_path), chunk_duration=180.0)
        cleanup_chunks(chunks)

        assert wav_path.exists()
