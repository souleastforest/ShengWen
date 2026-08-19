import asyncio
from dataclasses import asdict, dataclass
from typing import Any, Dict

import yt_dlp

from src.main.python.sheng_wen.downloader.bilibili_headers import (
    build_bilibili_http_headers,
)


class BilibiliAuthorResolveError(RuntimeError):
    """Bilibili 作者信息解析失败。"""


@dataclass(slots=True)
class BilibiliAuthorInfo:
    author_name: str
    author_url: str


def _unwrap_info_dict(info_dict: Dict[str, Any]) -> Dict[str, Any]:
    if info_dict.get("_type") == "playlist" and info_dict.get("entries"):
        for entry in info_dict["entries"]:
            if isinstance(entry, dict):
                return entry
    return info_dict


def _normalize_author_url(raw_author_url: str, author_id: str) -> str:
    author_url = (raw_author_url or "").strip()
    if author_url.startswith("//"):
        author_url = f"https:{author_url}"
    if author_url:
        return author_url
    if author_id.isdigit():
        return f"https://space.bilibili.com/{author_id}"
    return ""


def _extract_bilibili_author(
    video_url: str, sessdata: str | None = None
) -> BilibiliAuthorInfo:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "http_headers": build_bilibili_http_headers(sessdata),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(video_url, download=False)

    info = _unwrap_info_dict(info_dict)
    author_name = str(info.get("uploader") or info.get("channel") or "").strip()
    author_id = str(info.get("uploader_id") or info.get("channel_id") or "").strip()
    author_url = _normalize_author_url(
        str(info.get("uploader_url") or info.get("channel_url") or ""),
        author_id,
    )

    if not author_name:
        raise BilibiliAuthorResolveError("未解析到作者名称")
    if not author_url:
        raise BilibiliAuthorResolveError("未解析到作者主页链接")

    return BilibiliAuthorInfo(
        author_name=author_name,
        author_url=author_url,
    )


async def resolve_bilibili_author(
    video_url: str,
    timeout_sec: float = 20.0,
    sessdata: str | None = None,
) -> Dict[str, str]:
    """异步解析 B 站视频作者信息。"""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_extract_bilibili_author, video_url, sessdata),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError as exc:
        raise BilibiliAuthorResolveError(f"解析超时（>{timeout_sec:.1f}s）") from exc
    except BilibiliAuthorResolveError:
        raise
    except Exception as exc:
        raise BilibiliAuthorResolveError(str(exc)) from exc

    return asdict(result)
