from src.main.python.sheng_wen.infra.api.routes.schemas import (
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


def test_summarization_settings_update_supports_partial_update():
    schema = SummarizationSettingsUpdate(mode="agent")

    assert schema.mode == "agent"
    assert schema.chunk_target_duration_sec is None
