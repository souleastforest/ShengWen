from src.main.python.sheng_wen.infra.api.routes.schemas import (
    BilibiliVideoInfo,
    BilibiliVideoInfoRequest,
    LocalPathTaskCreate,
    SummarizationSettingsUpdate,
    TaskCreate,
    VersionInfo,
)


def test_task_create_defaults():
    schema = TaskCreate(video_url="https://example.com/video")

    assert str(schema.video_url) == "https://example.com/video"
    assert schema.quality == "best"
    assert schema.summary_mode is None


def test_local_path_task_create_accepts_file_path():
    schema = LocalPathTaskCreate(file_path="/tmp/input.mp3")

    assert schema.file_path == "/tmp/input.mp3"
    assert schema.summary_mode is None


def test_version_info_contains_version():
    schema = VersionInfo(version="1.2.3")

    assert schema.version == "1.2.3"


def test_bilibili_video_info_request_accepts_url():
    schema = BilibiliVideoInfoRequest(url="https://www.bilibili.com/video/BV1xx411c7mD")

    assert schema.url == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_bilibili_video_info_status_roundtrip():
    """⑥ 三态字段 status：ok/degraded 可往返序列化；缺省为 None（向后兼容）。"""
    for status in ("ok", "degraded"):
        schema = BilibiliVideoInfo(
            is_multi_part=False,
            title="",
            bvid="BV1xx411c7mD",
            duration=0,
            parts=None,
            status=status,
        )
        dumped = schema.model_dump()
        assert dumped["status"] == status
        reparsed = BilibiliVideoInfo(**dumped)
        assert reparsed.status == status


def test_bilibili_video_info_status_defaults_none():
    """⑥ 旧字段缺省（未声明 status）→ None，旧客户端/序列化兼容。"""
    schema = BilibiliVideoInfo(
        is_multi_part=False, title="t", bvid="BV1xx411c7mD", duration=10
    )
    assert schema.status is None
    assert "status" in schema.model_dump()


def test_summarization_settings_update_supports_partial_update():
    schema = SummarizationSettingsUpdate(mode="agent")

    assert schema.mode == "agent"
    assert schema.chunk_target_duration_sec is None
