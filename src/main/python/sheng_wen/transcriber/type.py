"""转录段级数据结构（transcript_segments 持久化契约）。

设计（plan step 1）：三种 ASR（vibe_voice/fast_whisper/api）都产出段级
segments {start,end,text,speaker_id}（float 秒），但 DB 只存 HHMMSS+text
行格式、丢失 end/speaker_id。本模块提供段级数据结构与 JSON 序列化边界：
- Segment: @dataclass，白名单 to_dict/from_dict（容错缺省、未知键忽略）；
- segments_to_json: 序列化为 JSON 字符串（ensure_ascii=False，Segment 或
  裸 dict 均接受，dict 经白名单清洗）；
- segments_from_json: None/坏 JSON → []。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

# 白名单字段（from_dict 只取这些键，其余忽略）
_SEGMENT_KEYS = ("start", "end", "text", "speaker_id")


@dataclass
class Segment:
    """一段带时间轴的转录文本。

    start/end 为浮点秒（相对音频起点，分P合并时由 finalize 累加 offset）。
    """

    start: float
    end: float
    text: str
    speaker_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """白名单导出；speaker_id 为 None 时省略（保持 JSON 紧凑）。"""
        data: dict[str, Any] = {
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }
        if self.speaker_id is not None:
            data["speaker_id"] = self.speaker_id
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "Segment":
        """白名单导入；缺省容错：start/end 缺省 0.0、text 缺省 ''、未知键忽略。

        非法输入（None/非 dict）也返回最小安全视图，保证坏数据不炸链路。
        """
        if not isinstance(data, dict):
            return cls(start=0.0, end=0.0, text="")
        try:
            start = float(data.get("start", 0.0) or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(data.get("end", 0.0) or 0.0)
        except (TypeError, ValueError):
            end = 0.0
        text = data.get("text")
        if not isinstance(text, str):
            text = ""
        speaker_id = data.get("speaker_id")
        return cls(start=start, end=end, text=text, speaker_id=speaker_id)


def segments_to_json(segments: Any) -> str:
    """将 segments（Segment 或裸 dict 均可）序列化为 JSON 字符串。

    ensure_ascii=False 保留中文可读性；裸 dict 经白名单清洗后再序列化
    （ASR 返回的 language/avg_logprob 等多余键不落库）。
    """
    items: list[dict[str, Any]] = []
    for seg in segments or []:
        if isinstance(seg, Segment):
            items.append(seg.to_dict())
        elif isinstance(seg, dict):
            items.append(Segment.from_dict(seg).to_dict())
        # 其他非法条目跳过
    return json.dumps(items, ensure_ascii=False)


def segments_from_json(raw: Optional[str]) -> list[Segment]:
    """从 DB 存储的 JSON 字符串解析 segments；None/坏 JSON/非列表 → []。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [Segment.from_dict(item) for item in data if isinstance(item, dict)]


def segments_to_api_list(raw: Any) -> Optional[list[dict[str, Any]]]:
    """DB 存储的 transcript_segments → API 线格式（列表或 None）。

    与 GET /tasks/{id}?include_content=true 的解析语义一致（线格式契约统一，
    消除"部分端点/WS 广播透传原始 JSON 字符串"导致的 Pydantic 校验失败与
    前端 segments.map() 崩溃）：
    - None / 坏 JSON / 非字符串 → None（与"无 segments"同语义）；
    - 空数组 / 全部为非法条目 → None；
    - 合法 JSON 数组 → 逐条白名单 to_dict 列表；
    - 已解析列表（幂等，防止双重包装）→ 原样白名单清洗返回。
    """
    if isinstance(raw, list):
        items = [
            Segment.from_dict(item).to_dict() for item in raw if isinstance(item, dict)
        ]
        return items or None
    if not isinstance(raw, str) or not raw:
        return None
    segments = segments_from_json(raw)
    if not segments:
        return None
    return [seg.to_dict() for seg in segments]
