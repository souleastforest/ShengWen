from __future__ import annotations

import re
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException
from loguru import logger

from src.main.python.sheng_wen.infra.api.routes import deps
from src.main.python.sheng_wen.infra.api.routes.schemas import (
    BilibiliVideoInfo,
    BilibiliVideoInfoRequest,
    BilibiliVideoPartInfo,
)


router = APIRouter(prefix="/bilibili")


@router.post("/video-info", response_model=BilibiliVideoInfo)
async def get_bilibili_video_info(payload: BilibiliVideoInfoRequest):
    video_url = str(payload.url or "").strip()
    if not video_url:
        raise HTTPException(status_code=400, detail="URL 不能为空")

    if not deps._is_bilibili_video_url(video_url):
        raise HTTPException(status_code=400, detail="不是有效的 B 站视频链接")

    try:
        from bilibili_api import sync, video

        candidate = video_url
        if "b23.tv" in video_url:
            try:
                request = UrlRequest(video_url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(request, timeout=15) as response:
                    candidate = response.geturl()
            except Exception:
                pass

        match = re.search(r"/video/(BV[0-9A-Za-z]+)", candidate)
        if not match:
            fallback = re.search(r"(BV[0-9A-Za-z]+)", candidate)
            if fallback:
                bvid = fallback.group(1)
            else:
                raise HTTPException(status_code=400, detail="无法从链接中提取 BV 号")
        else:
            bvid = match.group(1)

        video_obj = video.Video(bvid=bvid)
        info = sync(video_obj.get_info())

        title = str(info.get("title") or "")
        duration = int(info.get("duration") or 0)
        pages = info.get("pages")
        if isinstance(pages, list) and len(pages) > 1:
            parts = []
            for page in pages:
                if not isinstance(page, dict):
                    continue
                part_index = int(page.get("page", 0))
                cid = int(page.get("cid", 0))
                part_title = str(page.get("part") or f"第{part_index}P")
                part_duration = int(page.get("duration") or 0)
                parts.append(
                    BilibiliVideoPartInfo(
                        index=part_index - 1,
                        cid=cid,
                        title=part_title,
                        duration=part_duration,
                    )
                )

            return BilibiliVideoInfo(
                is_multi_part=True,
                title=title,
                bvid=bvid,
                duration=duration,
                parts=parts,
            )

        return BilibiliVideoInfo(
            is_multi_part=False,
            title=title,
            bvid=bvid,
            duration=duration,
            parts=None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取 B 站视频信息失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取视频信息失败: {str(e)}")
