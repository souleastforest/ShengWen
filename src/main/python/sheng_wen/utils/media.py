import os

VIDEO_MEDIA_EXTENSIONS = {
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".flv",
    ".wmv",
    ".webm",
    ".m4v",
}
AUDIO_MEDIA_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".aac",
    ".ogg",
    ".m4a",
    ".wma",
    ".opus",
}
SUPPORTED_MEDIA_EXTENSIONS = VIDEO_MEDIA_EXTENSIONS | AUDIO_MEDIA_EXTENSIONS


def get_media_extension(file_path_or_name: str) -> str:
    return os.path.splitext(file_path_or_name)[1].lower()


def is_audio_media(file_path_or_name: str) -> bool:
    return get_media_extension(file_path_or_name) in AUDIO_MEDIA_EXTENSIONS


def unsupported_format_message(ext: str) -> str:
    """统一的不支持格式错误文案（端点到 worker 共用，避免格式漂移）。"""
    return (
        f"不支持的文件格式: {ext}。支持的格式: "
        f"{', '.join(sorted(SUPPORTED_MEDIA_EXTENSIONS))}"
    )


def build_transcriber_payload(
    task_id: str,
    media_path: str,
    output_dir: str | None = None,
    summary_mode: str | None = None,
    generate_topic: bool | None = None,
) -> dict:
    if output_dir is None:
        from ..config.settings import config

        output_dir = config.storage.resolved_base_dir

    file_ext = get_media_extension(media_path)
    if file_ext not in SUPPORTED_MEDIA_EXTENSIONS:
        raise ValueError(unsupported_format_message(file_ext))

    payload = {
        "task_id": task_id,
        "output_file": os.path.join(output_dir, f"{task_id}_summary.md"),
    }
    if summary_mode:
        payload["summary_mode"] = str(summary_mode)
    # 仅转录模式的"总结标题"开关：显式传入时透传；缺省（None）时由
    # TranscriberWorker 按默认开启（True）处理，与 API 层默认一致。
    if generate_topic is not None:
        payload["generate_topic"] = bool(generate_topic)

    # 音频文件可直接转录；视频文件需先提取音频。
    if is_audio_media(media_path):
        payload["video_file"] = None
        payload["audio_file"] = media_path
    else:
        payload["video_file"] = media_path
        payload["audio_file"] = os.path.join(output_dir, f"{task_id}.mp3")

    return payload
