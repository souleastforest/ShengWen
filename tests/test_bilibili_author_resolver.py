"""bilibili_author_resolver 与共享 B 站请求头防御的单测。

背景（2026-08-16 事故）：B 站对 wbi/playurl 等 API 有 412 反爬，
author_resolver 曾裸调 yt-dlp 不带浏览器级请求头导致解析失败。
"""

import asyncio
from unittest.mock import patch

import pytest

from src.main.python.sheng_wen.downloader.bilibili_author_resolver import (
    BilibiliAuthorResolveError,
    _extract_bilibili_author,
    _normalize_author_url,
    _unwrap_info_dict,
    resolve_bilibili_author,
)
from src.main.python.sheng_wen.downloader.bilibili_headers import (
    build_bilibili_http_headers,
    sanitize_cookie_value,
)

BILI_URL = "https://b23.tv/BWZ4WKD"


# ---------- 共享请求头 ----------


def test_sanitize_cookie_value():
    assert sanitize_cookie_value(None) == ""
    assert sanitize_cookie_value("  abc  ") == "abc"
    assert sanitize_cookie_value("a\r\nb") == "ab"


def test_build_headers_without_sessdata_has_browser_level_headers():
    headers = build_bilibili_http_headers(None)
    assert "User-Agent" in headers and "Mozilla" in headers["User-Agent"]
    assert headers["Referer"] == "https://www.bilibili.com/"
    assert headers["Origin"] == "https://www.bilibili.com"
    assert "Cookie" not in headers


def test_build_headers_with_sessdata_appends_cookie():
    headers = build_bilibili_http_headers("sess123")
    assert headers["Cookie"] == "SESSDATA=sess123"
    assert headers["Referer"] == "https://www.bilibili.com/"


# ---------- yt-dlp 调用防爬（本次事故核心断言） ----------


def test_extract_passes_browser_headers_and_cookie_to_ytdlp():
    info = {
        "uploader": "来点思考",
        "uploader_id": "3546782996891861",
        "uploader_url": "https://space.bilibili.com/3546782996891861",
    }
    with patch(
        "src.main.python.sheng_wen.downloader.bilibili_author_resolver.yt_dlp.YoutubeDL"
    ) as mock_ydl:
        mock_ydl.return_value.__enter__.return_value.extract_info.return_value = info
        _extract_bilibili_author(BILI_URL, sessdata="sess123")

    opts = mock_ydl.call_args.args[0]
    headers = opts["http_headers"]
    assert headers["User-Agent"] is not None
    assert headers["Referer"] == "https://www.bilibili.com/"
    assert headers["Origin"] == "https://www.bilibili.com"
    assert headers["Cookie"] == "SESSDATA=sess123"


def test_extract_without_sessdata_still_sends_browser_headers():
    info = {
        "uploader": "up",
        "uploader_id": "1",
        "uploader_url": "https://space.bilibili.com/1",
    }
    with patch(
        "src.main.python.sheng_wen.downloader.bilibili_author_resolver.yt_dlp.YoutubeDL"
    ) as mock_ydl:
        mock_ydl.return_value.__enter__.return_value.extract_info.return_value = info
        _extract_bilibili_author(BILI_URL)

    opts = mock_ydl.call_args.args[0]
    headers = opts["http_headers"]
    assert "Cookie" not in headers
    assert headers["Referer"] == "https://www.bilibili.com/"


# ---------- 字段提取 ----------


def test_extract_author_fields():
    info = {
        "uploader": "来点思考",
        "uploader_id": "3546782996891861",
        "uploader_url": "https://space.bilibili.com/3546782996891861",
    }
    with patch(
        "src.main.python.sheng_wen.downloader.bilibili_author_resolver.yt_dlp.YoutubeDL"
    ) as mock_ydl:
        mock_ydl.return_value.__enter__.return_value.extract_info.return_value = info
        result = _extract_bilibili_author(BILI_URL)

    assert result.author_name == "来点思考"
    assert result.author_url == "https://space.bilibili.com/3546782996891861"


def test_extract_falls_back_to_channel_fields():
    info = {
        "channel": "频道名",
        "channel_id": "999",
        "channel_url": "https://space.bilibili.com/999",
    }
    with patch(
        "src.main.python.sheng_wen.downloader.bilibili_author_resolver.yt_dlp.YoutubeDL"
    ) as mock_ydl:
        mock_ydl.return_value.__enter__.return_value.extract_info.return_value = info
        result = _extract_bilibili_author(BILI_URL)

    assert result.author_name == "频道名"
    assert result.author_url == "https://space.bilibili.com/999"


def test_extract_missing_author_name_raises():
    info = {"uploader": "", "uploader_id": "1"}
    with patch(
        "src.main.python.sheng_wen.downloader.bilibili_author_resolver.yt_dlp.YoutubeDL"
    ) as mock_ydl:
        mock_ydl.return_value.__enter__.return_value.extract_info.return_value = info
        with pytest.raises(BilibiliAuthorResolveError, match="作者名称"):
            _extract_bilibili_author(BILI_URL)


def test_unwrap_playlist_info_dict():
    entry = {"uploader": "up1"}
    playlist = {"_type": "playlist", "entries": [entry]}
    assert _unwrap_info_dict(playlist) is entry
    plain = {"uploader": "up2"}
    assert _unwrap_info_dict(plain) is plain


def test_normalize_author_url():
    assert (
        _normalize_author_url("//space.bilibili.com/1", "1")
        == "https://space.bilibili.com/1"
    )
    assert _normalize_author_url("", "12345") == "https://space.bilibili.com/12345"
    assert _normalize_author_url("", "BVxx") == ""
    assert (
        _normalize_author_url("https://space.bilibili.com/1", "1")
        == "https://space.bilibili.com/1"
    )


# ---------- 错误映射 ----------


@pytest.mark.asyncio
async def test_resolve_maps_412_download_error_to_resolve_error():
    from yt_dlp.utils import DownloadError

    async def fake_raise(*args, **kwargs):
        raise DownloadError(
            "ERROR: [BiliBili] 1gjuB6EEj5: Unable to download JSON metadata: "
            "HTTP Error 412: Precondition Failed"
        )

    with patch(
        "src.main.python.sheng_wen.downloader.bilibili_author_resolver.asyncio.to_thread",
        new=fake_raise,
    ):
        with pytest.raises(BilibiliAuthorResolveError, match="412"):
            await resolve_bilibili_author(BILI_URL)


@pytest.mark.asyncio
async def test_resolve_maps_timeout_to_resolve_error():
    async def fake_timeout(*args, **kwargs):
        raise asyncio.TimeoutError

    with patch(
        "src.main.python.sheng_wen.downloader.bilibili_author_resolver.asyncio.to_thread",
        new=fake_timeout,
    ):
        with pytest.raises(BilibiliAuthorResolveError, match="超时"):
            await resolve_bilibili_author(BILI_URL)


@pytest.mark.asyncio
async def test_resolve_success_returns_dict():
    from src.main.python.sheng_wen.downloader.bilibili_author_resolver import (
        BilibiliAuthorInfo,
    )

    async def fake_ok(*args, **kwargs):
        return BilibiliAuthorInfo(
            author_name="来点思考",
            author_url="https://space.bilibili.com/3546782996891861",
        )

    with patch(
        "src.main.python.sheng_wen.downloader.bilibili_author_resolver.asyncio.to_thread",
        new=fake_ok,
    ):
        result = await resolve_bilibili_author(BILI_URL, sessdata="sess123")

    assert result == {
        "author_name": "来点思考",
        "author_url": "https://space.bilibili.com/3546782996891861",
    }
