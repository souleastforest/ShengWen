"""TDD: 字幕生成器纯函数（SRT/VTT 时间轴、speaker 前缀、normalize、HHMMSS 回退）。

契约（plan step 2，参考 VibeVoice 官方实现）：
- format_srt_time: HH:MM:SS,mmm，进位正确（3661.5 → "01:01:01,500"）
- segments_to_srt: 序号 + 时间轴 + 文本；speaker_id 非空字符串时前缀 "[Speaker N] "
- segments_to_vtt: "WEBVTT" 头 + HH:MM:SS.mmm 时间轴（点号分隔）
- normalize_segments: 排序(start,end)、空文本跳过、end<=start 跳过、换行→空格、
  毫秒截断（超 3 位小数的小数截断而非四舍五入）
- parse_hhmmss_transcript: HHMMSS 行解析（mm/ss<60 校验；end 用下行 start 近似、
  末行 end=start+3；无时间戳行跳过）——存量任务回退
- Segment 字典往返（白名单 + 容错缺省）+ segments_from_json 坏 JSON → []
"""

from src.main.python.sheng_wen.transcriber.subtitles import (
    format_srt_time,
    normalize_segments,
    parse_hhmmss_transcript,
    segments_to_srt,
    segments_to_vtt,
)
from src.main.python.sheng_wen.transcriber.type import (
    Segment,
    segments_from_json,
    segments_to_json,
)


# ---- format_srt_time -------------------------------------------------------


def test_format_srt_time_zero():
    assert format_srt_time(0) == "00:00:00,000"


def test_format_srt_time_hour_carry():
    assert format_srt_time(3661.5) == "01:01:01,500"


def test_format_srt_time_truncates_millis():
    assert format_srt_time(59.999) == "00:00:59,999"
    assert format_srt_time(1.23456) == "00:00:01,234"


def test_format_srt_time_second_carry():
    assert format_srt_time(60.0) == "00:01:00,000"


# ---- segments_to_srt -------------------------------------------------------


def test_segments_to_srt_structure():
    srt = segments_to_srt([Segment(1.0, 2.5, "你好")])
    assert srt == "1\n00:00:01,000 --> 00:00:02,500\n你好\n"


def test_segments_to_srt_speaker_prefix():
    srt = segments_to_srt(
        [
            Segment(1.0, 2.0, "第一句", speaker_id="A"),
            Segment(3.0, 4.0, "第二句", speaker_id="2"),
        ]
    )
    assert "[Speaker A] 第一句" in srt
    assert "[Speaker 2] 第二句" in srt


def test_segments_to_srt_no_speaker_prefix():
    srt = segments_to_srt([Segment(1.0, 2.0, "无说话人")])
    assert "[Speaker" not in srt
    assert "无说话人" in srt


def test_segments_to_srt_include_speaker_false():
    srt = segments_to_srt(
        [Segment(1.0, 2.0, "第一句", speaker_id="A")], include_speaker=False
    )
    assert "[Speaker" not in srt
    assert "第一句" in srt


def test_segments_to_srt_empty_speaker_id_no_prefix():
    srt = segments_to_srt([Segment(1.0, 2.0, "第一句", speaker_id="")])
    assert "[Speaker" not in srt


def test_segments_to_srt_sequence_numbers():
    srt = segments_to_srt(
        [Segment(1.0, 2.0, "a"), Segment(2.0, 3.0, "b"), Segment(3.0, 4.0, "c")]
    )
    assert srt.startswith("1\n")
    assert "\n2\n00:00:02,000" in srt
    assert "\n3\n00:00:03,000" in srt


# ---- segments_to_vtt -------------------------------------------------------


def test_segments_to_vtt_header_and_dot_timestamps():
    vtt = segments_to_vtt([Segment(1.0, 2.5, "你好", speaker_id="A")])
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:02.500" in vtt
    assert "[Speaker A] 你好" in vtt


def test_segments_to_vtt_include_speaker_false():
    vtt = segments_to_vtt(
        [Segment(1.0, 2.5, "你好", speaker_id="A")], include_speaker=False
    )
    assert "[Speaker" not in vtt


# ---- normalize_segments ----------------------------------------------------


def _seg_list() -> list[Segment]:
    return [
        Segment(5.0, 6.0, "第二"),
        Segment(1.0, 2.0, "第一"),
    ]


def test_normalize_segments_sorts_by_start():
    normalized = normalize_segments(_seg_list())
    assert [s.start for s in normalized] == [1.0, 5.0]


def test_normalize_segments_skips_empty_text():
    normalized = normalize_segments(
        [Segment(1.0, 2.0, "有内容"), Segment(3.0, 4.0, "  "), Segment(5.0, 6.0, "")]
    )
    assert [s.text for s in normalized] == ["有内容"]


def test_normalize_segments_skips_invalid_ranges():
    normalized = normalize_segments(
        [
            Segment(1.0, 2.0, "合法"),
            Segment(3.0, 3.0, "零时长"),
            Segment(4.0, 3.0, "倒挂"),
        ]
    )
    assert [s.text for s in normalized] == ["合法"]


def test_normalize_segments_replaces_newlines():
    normalized = normalize_segments([Segment(1.0, 2.0, "第一行\n第二行\r\n第三行")])
    assert normalized[0].text == "第一行 第二行 第三行"


def test_normalize_segments_truncates_millis():
    normalized = normalize_segments([Segment(1.23456, 2.98765, "文本")])
    assert normalized[0].start == 1.234
    assert normalized[0].end == 2.987


def test_normalize_segments_returns_empty_for_empty_input():
    assert normalize_segments([]) == []


# ---- parse_hhmmss_transcript（存量任务回退） --------------------------------


def test_parse_hhmmss_basic():
    segments = parse_hhmmss_transcript("000000第一段\n000010第二段\n000020第三段")
    assert segments == [
        Segment(0.0, 10.0, "第一段"),
        Segment(10.0, 20.0, "第二段"),
        Segment(20.0, 23.0, "第三段"),
    ]


def test_parse_hhmmss_end_uses_next_line_start():
    segments = parse_hhmmss_transcript("000005第一段\n000015第二段")
    # end 用下行 start 近似
    assert segments[0].end == 15.0


def test_parse_hhmmss_last_line_end_plus_3():
    segments = parse_hhmmss_transcript("000005第一段\n000015第二段")
    assert segments[-1].end == 18.0


def test_parse_hhmmss_skips_invalid_timestamps():
    segments = parse_hhmmss_transcript(
        "000000第一段\n"
        "006000分钟超界\n"  # mm=60 非法
        "000060秒超界\n"  # ss=60 非法
        "普通文本行\n"  # 无时间戳
        "000030第三段"
    )
    assert [s.text for s in segments] == ["第一段", "第三段"]
    assert segments[0].end == 30.0  # 跳过行不参与近似


def test_parse_hhmmss_strips_timestamp_and_whitespace():
    segments = parse_hhmmss_transcript("000000  带空格的文本  \n")
    assert segments[0].text == "带空格的文本"


def test_parse_hhmmss_hour_carry():
    segments = parse_hhmmss_transcript("010101小时进位")
    assert segments[0].start == 3661.0


def test_parse_hhmmss_empty_text_returns_empty():
    assert parse_hhmmss_transcript("") == []
    assert parse_hhmmss_transcript("\n\n") == []


# ---- Segment 往返 + JSON 序列化 --------------------------------------------


def test_segment_round_trip():
    seg = Segment(1.5, 2.5, "你好", speaker_id="A")
    assert Segment.from_dict(seg.to_dict()) == seg


def test_segment_from_dict_defaults_and_whitelist():
    seg = Segment.from_dict({"end": 5.0, "unknown_key": 1, "extra": {"x": 1}})
    assert seg.start == 0.0  # 缺省
    assert seg.end == 5.0
    assert seg.text == ""  # 缺省
    assert seg.speaker_id is None


def test_segment_from_dict_non_dict_input():
    assert Segment.from_dict(None) == Segment(0.0, 0.0, "")
    assert Segment.from_dict("junk") == Segment(0.0, 0.0, "")


def test_segments_to_json_ensure_ascii_false():
    raw = segments_to_json([Segment(1.0, 2.0, "你好", speaker_id="A")])
    assert "你好" in raw  # 不转义为 \uXXXX
    assert '"speaker_id": "A"' in raw


def test_segments_to_json_filters_unknown_keys():
    raw = segments_to_json([{"start": 1.0, "end": 2.0, "text": "x", "language": "zh"}])
    assert '"language"' not in raw
    parsed = segments_from_json(raw)
    assert parsed == [Segment(1.0, 2.0, "x")]


def test_segments_from_json_none_and_empty():
    assert segments_from_json(None) == []
    assert segments_from_json("") == []
    assert segments_from_json("[]") == []


def test_segments_from_json_bad_json():
    assert segments_from_json("{not valid json") == []
    assert segments_from_json("hello") == []


def test_segments_from_json_not_a_list():
    assert segments_from_json('{"start": 1}') == []


def test_segments_json_round_trip():
    segments = [Segment(1.0, 2.0, "你好"), Segment(3.5, 4.5, "世界", speaker_id="B")]
    assert segments_from_json(segments_to_json(segments)) == segments


def test_segments_from_json_skips_non_dict_items():
    parsed = segments_from_json('[{"start": 1, "end": 2, "text": "ok"}, "junk", 42]')
    assert parsed == [Segment(1.0, 2.0, "ok")]
