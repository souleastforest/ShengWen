"""字幕生成器纯函数（SRT/VTT 打轴 + 存量 HHMMSS 回退解析）。

设计（plan step 2，参考 VibeVoice 官方实现 gradio_asr_demo_api_video.py）：
- format_srt_time: 秒 → "HH:MM:SS,mmm"（毫秒截断不四舍五入）；
- segments_to_srt / segments_to_vtt: 序号 + 时间轴 + 文本；speaker_id 非空
  字符串时前缀 "[Speaker N] "；
- normalize_segments: 排序、空文本跳过、end<=start 跳过、换行→空格、毫秒截断；
- parse_hhmmss_transcript: 存量任务（无 segments）从 HHMMSS 行格式回退解析
  （行首 6 位数字 + mm/ss<60 校验；end 用下行 start 近似、末行 end=start+3；
  无时间戳行跳过）。transcript 文本格式本身不得变更（summarization/chunker.py
  依赖）。
"""

from __future__ import annotations

import math
import re
from typing import Iterable

from .type import Segment

_HHMMSS_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})(.*)$")


def format_srt_time(seconds: float) -> str:
    """将秒数格式化为 SRT 时间轴 "HH:MM:SS,mmm"（毫秒截断）。"""
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _speaker_prefix(segment: Segment) -> str:
    """speaker_id 非空字符串时返回 "[Speaker N] " 前缀，否则空串。"""
    speaker_id = segment.speaker_id
    if isinstance(speaker_id, str) and speaker_id.strip():
        return f"[Speaker {speaker_id}] "
    return ""


def segments_to_srt(segments: Iterable[Segment], include_speaker: bool = True) -> str:
    """将 segments 渲染为 SRT 文本（序号、时间轴、文本；条目间空行）。"""
    lines: list[str] = []
    for index, seg in enumerate(segments, 1):
        prefix = _speaker_prefix(seg) if include_speaker else ""
        lines.append(f"{index}")
        lines.append(f"{format_srt_time(seg.start)} --> {format_srt_time(seg.end)}")
        lines.append(f"{prefix}{seg.text}")
        lines.append("")
    return "\n".join(lines)


def segments_to_vtt(segments: Iterable[Segment], include_speaker: bool = True) -> str:
    """将 segments 渲染为 WebVTT 文本（WEBVTT 头 + "HH:MM:SS.mmm" 时间轴）。"""
    lines: list[str] = ["WEBVTT", ""]
    for index, seg in enumerate(segments, 1):
        prefix = _speaker_prefix(seg) if include_speaker else ""
        lines.append(f"{index}")
        lines.append(
            f"{format_srt_time(seg.start).replace(',', '.')} --> "
            f"{format_srt_time(seg.end).replace(',', '.')}"
        )
        lines.append(f"{prefix}{seg.text}")
        lines.append("")
    return "\n".join(lines)


def _truncate_millis(value: float) -> float:
    """毫秒截断：保留 3 位小数（截断而非四舍五入），避免浮点尾差。"""
    return math.floor(float(value) * 1000.0) / 1000.0


def normalize_segments(segments: Iterable[Segment]) -> list[Segment]:
    """规范化 segments：排序(start,end)、空文本跳过、end<=start 跳过、
    换行→空格、毫秒截断。"""
    normalized: list[Segment] = []
    for seg in segments:
        text = (seg.text or "").replace("\n", " ").replace("\r", "").strip()
        if not text:
            continue
        start = _truncate_millis(seg.start)
        end = _truncate_millis(seg.end)
        if end <= start:
            continue
        normalized.append(
            Segment(start=start, end=end, text=text, speaker_id=seg.speaker_id)
        )
    normalized.sort(key=lambda item: (item.start, item.end))
    return normalized


def parse_hhmmss_transcript(text: str) -> list[Segment]:
    """从 HHMMSS 行格式转录文本解析 segments（存量任务回退）。

    规则：
    - 行首 6 位数字时间戳（HHMMSS），mm<60 且 ss<60 才合法；
    - end 用下行 start 近似；末行 end = start + 3；
    - 无时间戳行（或非法时间戳行）跳过，不参与近似。
    """
    parsed: list[tuple[float, str]] = []
    for line in (text or "").split("\n"):
        match = _HHMMSS_RE.match(line)
        if not match:
            continue
        hours, minutes, secs = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        if minutes >= 60 or secs >= 60:
            continue
        start = float(hours * 3600 + minutes * 60 + secs)
        content = match.group(4).strip()
        if not content:
            continue
        parsed.append((start, content))

    segments: list[Segment] = []
    for index, (start, content) in enumerate(parsed):
        end = parsed[index + 1][0] if index + 1 < len(parsed) else start + 3.0
        segments.append(Segment(start=start, end=end, text=content))
    return segments
