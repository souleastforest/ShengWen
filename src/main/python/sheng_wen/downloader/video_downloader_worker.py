import asyncio
import json
import os
import re
import threading
from queue import Empty, Queue
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import Any, Dict, List, Tuple

import yt_dlp
from loguru import logger

from ..worker import Worker, TaskCancelledError
from ..utils.ffmpeg_helper import FFmpegHelper
from .bilibili_author_resolver import (
    resolve_bilibili_author,
    BilibiliAuthorResolveError,
)


class VideoDownloaderWorker(Worker):
    """
    一个工作单元，用于从给定的 URL 下载视频。
    """

    BILIBILI_SUBTITLE_TIMEOUT_SEC = 20.0

    def __init__(
        self,
        name: str,
        next_worker: Worker = None,
        summary_worker: Worker = None,
        transcription_settings_manager: Any = None,
    ):
        super().__init__(name)
        self.next_worker = next_worker
        self.summary_worker = summary_worker
        self.transcription_settings_manager = transcription_settings_manager
        self.output_dir = "temp"
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def _is_bilibili_url(video_url: str) -> bool:
        try:
            netloc = (urlparse(video_url).netloc or "").lower()
        except Exception:
            return False
        return "bilibili.com" in netloc or "b23.tv" in netloc

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """将秒数格式化为 HHMMSS 字符串。"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        sec = int(seconds % 60)
        return f"{hours:02d}{minutes:02d}{sec:02d}"

    @staticmethod
    def _sanitize_cookie_value(value: str | None) -> str:
        return (value or "").strip().replace("\r", "").replace("\n", "")

    @staticmethod
    def _resolve_final_url(video_url: str) -> str:
        request = Request(video_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=15) as response:
            return response.geturl()

    @classmethod
    def _extract_bvid_from_url(cls, video_url: str) -> str:
        candidate = video_url
        if "b23.tv" in video_url:
            try:
                candidate = cls._resolve_final_url(video_url)
            except Exception:
                candidate = video_url

        match = re.search(r"/video/(BV[0-9A-Za-z]+)", candidate)
        if match:
            return match.group(1)

        fallback = re.search(r"(BV[0-9A-Za-z]+)", candidate)
        if fallback:
            return fallback.group(1)

        raise ValueError("无法从链接中提取 BV 号")

    @staticmethod
    def _normalize_subtitle_url(url: str) -> str:
        if not url:
            return ""
        if url.startswith("//"):
            return f"https:{url}"
        return url

    @staticmethod
    def _score_bilibili_subtitle_item(item: Dict[str, Any]) -> int:
        lan = (
            str(item.get("lan") or item.get("lang") or "")
            .strip()
            .lower()
            .replace("_", "-")
        )
        url = str(item.get("subtitle_url") or item.get("url") or "")
        ext = os.path.splitext(url.split("?", 1)[0])[1].lstrip(".").lower()

        score = 0
        if lan == "ai-zh":
            score += 1000
        elif "zh" in lan and "ai" in lan:
            score += 900
        elif lan.startswith("zh"):
            score += 700
        elif "zh" in lan:
            score += 600
        elif lan:
            score += 100

        if ext == "json":
            score += 30
        elif ext:
            score += 10

        return score

    @classmethod
    def _select_bilibili_subtitle_item(
        cls, subtitle_items: List[Dict[str, Any]]
    ) -> Dict[str, Any] | None:
        best_item = None
        best_score = -1
        for item in subtitle_items:
            if not isinstance(item, dict):
                continue
            sub_url = cls._normalize_subtitle_url(
                str(item.get("subtitle_url") or item.get("url") or "")
            )
            if not sub_url:
                continue
            score = cls._score_bilibili_subtitle_item(item)
            if score > best_score:
                best_score = score
                best_item = item
        return best_item

    def _resolve_bilibili_sessdata(self, payload: Dict[str, Any]) -> Tuple[str, str]:
        task_override = self._sanitize_cookie_value(
            str(payload.get("bilibili_sessdata") or "")
        )
        manager = self.transcription_settings_manager
        if manager is not None and hasattr(manager, "resolve_bilibili_sessdata"):
            try:
                value, source = manager.resolve_bilibili_sessdata(task_override)
                return self._sanitize_cookie_value(value), str(source or "none")
            except Exception as e:
                logger.warning(
                    f"[{self.name}] 读取 B 站 Cookie 设置失败，将回退环境变量: {e}"
                )

        if task_override:
            return task_override, "task"

        env_cookie = self._sanitize_cookie_value(
            os.getenv("BILIBILI_SESSDATA") or os.getenv("SESSDATA")
        )
        if env_cookie:
            return env_cookie, "env"
        return "", "none"

    @staticmethod
    def _download_text(url: str, extra_headers: Dict[str, Any] | None = None) -> str:
        target = url
        if target.startswith("//"):
            target = f"https:{target}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        }
        if isinstance(extra_headers, dict):
            for k, v in extra_headers.items():
                if isinstance(k, str) and isinstance(v, str):
                    headers[k] = v
        request = Request(target, headers=headers)
        with urlopen(request, timeout=15) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")

    def _build_transcript_from_subtitle_json(
        self, subtitle_json: Dict[str, Any]
    ) -> str:
        lines: List[str] = []

        body = subtitle_json.get("body")
        if isinstance(body, list):
            for segment in body:
                if not isinstance(segment, dict):
                    continue
                content = str(
                    segment.get("content") or segment.get("text") or ""
                ).strip()
                if not content:
                    continue
                start_raw = segment.get("from", segment.get("start", 0.0))
                try:
                    start = float(start_raw)
                except (TypeError, ValueError):
                    start = 0.0
                content = content.replace("\n", " ").strip()
                if content:
                    lines.append(f"{self._format_duration(start)}{content}\n")

            return "".join(lines)

        # 兼容 json3 类格式
        events = subtitle_json.get("events")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                start_ms = event.get("tStartMs", event.get("t_start_ms", 0))
                try:
                    start = float(start_ms) / 1000.0
                except (TypeError, ValueError):
                    start = 0.0

                segments = event.get("segs")
                if isinstance(segments, list):
                    text_parts = []
                    for seg in segments:
                        if isinstance(seg, dict):
                            seg_text = str(seg.get("utf8") or "").strip()
                            if seg_text:
                                text_parts.append(seg_text)
                    content = "".join(text_parts).replace("\n", " ").strip()
                else:
                    content = str(event.get("content") or "").replace("\n", " ").strip()

                if content:
                    lines.append(f"{self._format_duration(start)}{content}\n")

            return "".join(lines)

        return ""

    async def _extract_bilibili_subtitle_via_api(
        self, video_url: str, sessdata: str, part_index: int = 0
    ) -> Dict[str, Any] | None:
        """
        提取 B 站视频字幕。

        Args:
            video_url: 视频链接
            sessdata: B 站 Cookie
            part_index: 分P索引（0-based），默认为0（第一个分P或单P视频）

        Returns:
            包含字幕信息的字典，或 None 如果获取失败
        """
        try:
            from bilibili_api import Credential, video
        except Exception as exc:
            raise RuntimeError(
                "未安装 bilibili-api-python，无法进行 B 站字幕直取"
            ) from exc

        bvid = self._extract_bvid_from_url(video_url)
        credential = Credential(sessdata=sessdata or None) if sessdata else None

        video_obj = video.Video(bvid=bvid, credential=credential)
        info = await video_obj.get_info()
        cid = await video_obj.get_cid(part_index)
        player_info = await video_obj.get_player_info(cid=cid)

        subtitle_block = (
            player_info.get("subtitle") if isinstance(player_info, dict) else None
        )
        subtitle_items = (
            subtitle_block.get("subtitles") if isinstance(subtitle_block, dict) else []
        )
        if not isinstance(subtitle_items, list) or not subtitle_items:
            return None

        selected = self._select_bilibili_subtitle_item(subtitle_items)
        if not selected:
            return None

        subtitle_url = self._normalize_subtitle_url(
            str(selected.get("subtitle_url") or selected.get("url") or "")
        )
        if not subtitle_url:
            return None

        raw_subtitle = self._download_text(subtitle_url)
        subtitle_json = json.loads(raw_subtitle)
        transcript = self._build_transcript_from_subtitle_json(subtitle_json)
        if not transcript.strip():
            return None

        language = str(selected.get("lan") or selected.get("lang") or "")

        # 获取该分P的具体信息
        part_title = None
        part_duration = None
        pages = info.get("pages")
        if isinstance(pages, list) and len(pages) > part_index:
            page_info = pages[part_index]
            if isinstance(page_info, dict):
                part_title = page_info.get("part")
                part_duration = page_info.get("duration")

        return {
            "title": info.get("title"),
            "duration": part_duration or info.get("duration"),
            "transcript": transcript,
            "language": language,
            "is_auto": "ai" in language.lower(),
            "subtitle_url": subtitle_url,
            "part_index": part_index,
            "part_title": part_title,
        }

    async def _extract_bilibili_multi_part_subtitles(
        self, video_url: str, sessdata: str, part_indices: List[int]
    ) -> List[Dict[str, Any]]:
        """
        提取多个分P的字幕。

        Args:
            video_url: 视频链接
            sessdata: B 站 Cookie
            part_indices: 要提取的分P索引列表（0-based）

        Returns:
            成功提取的字幕信息列表
        """
        results = []
        for idx in part_indices:
            try:
                result = await self._extract_bilibili_subtitle_via_api(
                    video_url, sessdata, idx
                )
                if result:
                    results.append(result)
                    logger.info(
                        f"[{self.name}] 成功提取分P {idx + 1} 字幕: "
                        f"{result.get('part_title') or f'第{idx + 1}P'}"
                    )
                else:
                    logger.warning(f"[{self.name}] 分P {idx + 1} 未找到字幕")
            except Exception as e:
                logger.warning(f"[{self.name}] 提取分P {idx + 1} 字幕失败: {e}")
        return results

    def _merge_transcripts_with_offset(
        self, subtitle_results: List[Dict[str, Any]], video_info: Dict[str, Any]
    ) -> Tuple[str, int]:
        """
        合并多个分P的字幕，计算时间偏移。

        Args:
            subtitle_results: 多个分P的字幕结果列表
            video_info: 视频信息，包含分P时长

        Returns:
            (合并后的转录文本, 总时长)
        """
        if not subtitle_results:
            return "", 0

        # 按分P索引排序
        sorted_results = sorted(subtitle_results, key=lambda x: x.get("part_index", 0))

        # 获取每个分P的时长用于计算偏移
        pages = video_info.get("pages", [])
        duration_map = {}
        for page in pages:
            if isinstance(page, dict):
                page_idx = page.get("page", 1) - 1  # page 是 1-based
                duration_map[page_idx] = page.get("duration", 0)

        merged_lines = []
        total_duration = 0
        time_offset = 0.0

        for result in sorted_results:
            part_idx = result.get("part_index", 0)
            transcript = result.get("transcript", "")

            # 获取该分P时长
            part_duration = duration_map.get(part_idx, result.get("duration", 0))
            total_duration += part_duration

            if time_offset > 0 and transcript:
                # 需要调整时间戳
                for line in transcript.split("\n"):
                    if not line.strip():
                        continue
                    # 解析 HHMMSS 格式的时间戳
                    match = re.match(r"^(\d{2})(\d{2})(\d{2})(.+)$", line)
                    if match:
                        h, m, s, text = (
                            int(match.group(1)),
                            int(match.group(2)),
                            int(match.group(3)),
                            match.group(4),
                        )
                        original_seconds = h * 3600 + m * 60 + s
                        new_seconds = original_seconds + time_offset

                        new_h = int(new_seconds // 3600)
                        new_m = int((new_seconds % 3600) // 60)
                        new_s = int(new_seconds % 60)
                        merged_lines.append(
                            f"{new_h:02d}{new_m:02d}{new_s:02d}{text}\n"
                        )
                    else:
                        merged_lines.append(line + "\n")
            else:
                merged_lines.append(transcript)
                if not transcript.endswith("\n"):
                    merged_lines.append("\n")

            # 更新时间偏移
            time_offset += part_duration

        return "".join(merged_lines), total_duration

    def _try_extract_bilibili_subtitle(
        self, video_url: str, sessdata: str
    ) -> Dict[str, Any] | None:
        result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

        def runner() -> None:
            try:
                result = asyncio.run(
                    asyncio.wait_for(
                        self._extract_bilibili_subtitle_via_api(video_url, sessdata, 0),
                        timeout=self.BILIBILI_SUBTITLE_TIMEOUT_SEC,
                    )
                )
                result_queue.put(("result", result))
            except Exception as exc:
                result_queue.put(("error", exc))

        thread = threading.Thread(
            target=runner,
            name="bilibili-subtitle-fetch",
            daemon=True,
        )
        thread.start()

        try:
            kind, payload = result_queue.get(timeout=self.BILIBILI_SUBTITLE_TIMEOUT_SEC)
        except Empty as exc:
            raise TimeoutError(
                f"B 站字幕获取超时（>{self.BILIBILI_SUBTITLE_TIMEOUT_SEC:.0f}s）"
            ) from exc

        if kind == "error":
            raise payload
        return payload

    def _try_process_with_bilibili_subtitle(self, payload: Dict[str, Any]) -> bool:
        video_url = str(payload.get("video_url") or "")
        task_id = payload.get("task_id")

        if not video_url or not task_id:
            return False
        if self.is_task_cancelled(task_id):
            raise TaskCancelledError(f"任务已取消，跳过字幕直取: {task_id}")
        if not self._is_bilibili_url(video_url):
            return False
        if self.summary_worker is None:
            return False

        if self.transcription_settings_manager is not None:
            try:
                settings = self.transcription_settings_manager.get_settings()
                if not bool(settings.get("enable_bilibili_subtitle_fetch", True)):
                    return False
            except Exception as e:
                logger.warning(f"[{self.name}] 读取转录设置失败，继续回退 ASR: {e}")
                return False

        sessdata, cookie_source = self._resolve_bilibili_sessdata(payload)

        # 检查是否有分P配置
        bilibili_parts = payload.get("bilibili_parts")
        if bilibili_parts and isinstance(bilibili_parts, dict):
            mode = bilibili_parts.get("mode")
            indices = bilibili_parts.get("indices")

            if mode == "merge" and isinstance(indices, list) and len(indices) > 0:
                # 合并模式：提取多个分P的字幕并合并
                return self._try_process_bilibili_multi_part_merge(
                    video_url, sessdata, task_id, indices, payload
                )
            # separate 模式由 API 层处理，这里不应该到达
            # 如果到达这里，说明配置有问题，回退到普通处理
            logger.warning(
                f"[{self.name}] 未知的分P处理模式或无效配置: {bilibili_parts}"
            )

        logger.info(
            f"[{self.name}] 检测到 B 站 URL，尝试使用 bilibili-api 直取字幕: {video_url}"
            f" (cookie_source={cookie_source}, has_cookie={bool(sessdata)})"
        )

        try:
            subtitle_result = self._try_extract_bilibili_subtitle(video_url, sessdata)
            if not subtitle_result:
                logger.info(f"[{self.name}] 未获取到可用字幕，回退到下载+ASR流程。")
                return False

            transcript = subtitle_result["transcript"]
            intermediate_file_path = os.path.join(
                self.output_dir, f"{task_id}_subtitle.txt"
            )
            output_file = os.path.join(self.output_dir, f"{task_id}_summary.md")

            with open(
                intermediate_file_path, "w", encoding="utf-8", errors="replace"
            ) as f:
                f.write(transcript)

            from ..db import TaskStatus
            from ..task_updater import update_and_notify

            self._submit_coro(
                update_and_notify(task_id, {"status": TaskStatus.TRANSCRIBING})
            )

            if self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止字幕分支: {task_id}")

            update_data = {
                "title": subtitle_result.get("title"),
                "status": TaskStatus.SUMMARIZING,
                "progress": 0.0,
                "transcript": transcript,
                "transcription_time": 0.0,
                "audio_duration": subtitle_result.get("duration"),
                "summary_chunk_total": None,
                "summary_chunk_done": None,
                "summary_meta": None,
                "audio_downloaded": False,
                "audio_missing_reason": "subtitle_only",
            }
            summary_mode = str(payload.get("summary_mode") or "").strip().lower()
            skip_summarization = summary_mode == "none"
            if summary_mode in {"auto", "standard", "agent", "none"}:
                update_data["summary_mode"] = summary_mode
            if skip_summarization:
                update_data["status"] = TaskStatus.COMPLETED
                update_data["progress"] = 100
                logger.info(
                    f"[{self.name}] 仅转录模式，跳过 AI 总结: task_id={task_id}"
                )
            self._submit_coro(update_and_notify(task_id, update_data))

            # “总结标题”开关（默认开启）：字幕直取同样在置终态后异步生成标题，
            # 失败静默降级；multipart 分P子任务跳过，由 merge 父任务统一生成一次。
            # self._loop 守卫：未启动的事件循环（如同步单测）下不创建未 await 的协程。
            if (
                self._loop
                and skip_summarization
                and bool(payload.get("generate_topic", True))
                and not payload.get("multipart_part")
            ):
                from ..llm.llm_worker import generate_topic_for_task
                self._submit_coro(
                    generate_topic_for_task(self.summary_worker, task_id, transcript)
                )

            next_payload = payload.copy()
            next_payload.update(
                {
                    "intermediate_file_path": intermediate_file_path,
                    "output_file": output_file,
                }
            )
            if self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止派发总结: {task_id}")
            if not skip_summarization:
                self._submit_coro(self.summary_worker.add_task(next_payload))

            logger.info(
                f"[{self.name}] 已使用B站字幕（{subtitle_result.get('language')}），跳过音频转录。"
            )
            return True
        except Exception as e:
            logger.warning(f"[{self.name}] B 站字幕直取失败，将回退 ASR: {e}")
            return False

    def _try_process_bilibili_multi_part_merge(
        self,
        video_url: str,
        sessdata: str,
        task_id: str,
        part_indices: List[int],
        payload: Dict[str, Any],
    ) -> bool:
        """
        处理多P视频合并模式：提取所有选中分P的字幕并合并为一个转录。
        """
        logger.info(
            f"[{self.name}] 处理多P视频合并模式: {video_url}, 分P: {[i + 1 for i in part_indices]}"
        )

        try:
            # 获取视频信息和所有分P字幕
            subtitle_results = asyncio.run(
                asyncio.wait_for(
                    self._extract_bilibili_multi_part_subtitles(
                        video_url, sessdata, part_indices
                    ),
                    timeout=self.BILIBILI_SUBTITLE_TIMEOUT_SEC,
                )
            )

            if not subtitle_results:
                logger.warning(f"[{self.name}] 未能获取任何分P字幕，回退到下载+ASR流程")
                return False

            # 获取视频信息用于时长计算
            from bilibili_api import Credential, video

            bvid = self._extract_bvid_from_url(video_url)
            credential = Credential(sessdata=sessdata or None) if sessdata else None
            video_obj = video.Video(bvid=bvid, credential=credential)
            video_info = asyncio.run(
                asyncio.wait_for(
                    video_obj.get_info(),
                    timeout=self.BILIBILI_SUBTITLE_TIMEOUT_SEC,
                )
            )

            # 合并字幕
            merged_transcript, total_duration = self._merge_transcripts_with_offset(
                subtitle_results, video_info
            )

            if not merged_transcript.strip():
                logger.warning(f"[{self.name}] 合并后的字幕为空，回退到下载+ASR流程")
                return False

            # 构建标题（包含分P信息）
            title = video_info.get("title", "")
            if len(subtitle_results) < len(part_indices):
                title_suffix = (
                    f" (已合并 {len(subtitle_results)}/{len(part_indices)} 个分P)"
                )
            else:
                title_suffix = f" (已合并 {len(part_indices)} 个分P)"

            intermediate_file_path = os.path.join(
                self.output_dir, f"{task_id}_subtitle.txt"
            )
            output_file = os.path.join(self.output_dir, f"{task_id}_summary.md")

            with open(
                intermediate_file_path, "w", encoding="utf-8", errors="replace"
            ) as f:
                f.write(merged_transcript)

            from ..db import TaskStatus
            from ..task_updater import update_and_notify

            self._submit_coro(
                update_and_notify(task_id, {"status": TaskStatus.TRANSCRIBING})
            )

            if self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止字幕分支: {task_id}")

            update_data = {
                "title": title + title_suffix,
                "status": TaskStatus.SUMMARIZING,
                "progress": 0.0,
                "transcript": merged_transcript,
                "transcription_time": 0.0,
                "audio_duration": total_duration,
                "summary_chunk_total": None,
                "summary_chunk_done": None,
                "summary_meta": None,
                "audio_downloaded": False,
                "audio_missing_reason": "subtitle_only",
            }
            summary_mode = str(payload.get("summary_mode") or "").strip().lower()
            skip_summarization = summary_mode == "none"
            if summary_mode in {"auto", "standard", "agent", "none"}:
                update_data["summary_mode"] = summary_mode
            if skip_summarization:
                update_data["status"] = TaskStatus.COMPLETED
                update_data["progress"] = 100
                logger.info(
                    f"[{self.name}] 仅转录模式，跳过 AI 总结: task_id={task_id}"
                )
            self._submit_coro(update_and_notify(task_id, update_data))

            next_payload = payload.copy()
            next_payload.update(
                {
                    "intermediate_file_path": intermediate_file_path,
                    "output_file": output_file,
                }
            )
            if self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止派发总结: {task_id}")
            if payload.get("multipart_part"):
                from ..task_parts import update_task_part

                part_index = int(payload["multipart_part"]["index"])
                next_payload["output_file"] = os.path.join(
                    self.output_dir, f"{task_id}_p{part_index + 1}_summary.md"
                )
                update_task_part(
                    task_id,
                    part_index,
                    {
                        "status": "COMPLETED" if skip_summarization else "SUMMARIZING",
                        "progress": 100,
                        "transcript": merged_transcript,
                        "audio_duration": total_duration,
                    },
                )
                if not skip_summarization:
                    asyncio.run(self.summary_worker.process_task(next_payload))
            elif not skip_summarization:
                self._submit_coro(self.summary_worker.add_task(next_payload))

            logger.info(
                f"[{self.name}] 已合并 {len(subtitle_results)} 个分P的字幕，"
                f"总时长 {total_duration} 秒，跳过音频转录。"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.name}] 处理多P视频合并失败: {e}", exc_info=True)
            return False

    async def _resolve_and_save_bilibili_author(self, task_id: str, video_url: str):
        if not task_id or not self._is_bilibili_url(video_url):
            return

        try:
            author_info = await resolve_bilibili_author(video_url)
            from ..task_updater import update_and_notify

            await update_and_notify(
                task_id,
                {
                    "author_name": author_info.get("author_name"),
                    "author_url": author_info.get("author_url"),
                },
            )
            logger.info(
                f"[{self.name}] 已解析 B 站作者信息: "
                f"{author_info.get('author_name')} ({author_info.get('author_url')})"
            )
        except BilibiliAuthorResolveError as e:
            logger.info(
                f"[{self.name}] B 站作者信息解析失败（仅提示，不影响流程）: {e}"
            )
        except Exception as e:
            logger.info(
                f"[{self.name}] B 站作者信息写入失败（仅提示，不影响流程）: {e}"
            )

    @staticmethod
    def _selected_bilibili_playlist_items(payload: Dict[str, Any]) -> str | None:
        parts_config = payload.get("bilibili_parts")
        if not isinstance(parts_config, dict):
            return None

        indices = parts_config.get("indices")
        if not isinstance(indices, list):
            return None

        part_numbers = []
        for raw_index in indices:
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if index >= 0:
                part_numbers.append(str(index + 1))

        return ",".join(part_numbers) or None

    @staticmethod
    def _resolve_downloaded_video_paths(
        ydl: Any, info_dict: Dict[str, Any]
    ) -> List[str]:
        entries = info_dict.get("entries") if isinstance(info_dict, dict) else None
        candidates = entries if isinstance(entries, list) else [info_dict]
        paths = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            path = ydl.prepare_filename(candidate)
            if os.path.exists(path):
                paths.append(path)
        return paths

    def _process_bilibili_multipart(self, payload: Dict[str, Any]) -> None:
        task_id = str(payload.get("task_id") or "")
        config = payload.get("bilibili_parts") or {}
        indices = sorted({int(index) for index in config.get("indices") or []})
        if not task_id or not indices:
            return

        from ..db import TaskStatus, db
        from ..task_parts import get_task_parts

        parts = {part["part_index"]: part for part in get_task_parts(task_id)}
        for index in indices:
            part = parts.get(index)
            if part and part.get("status") == "COMPLETED":
                continue
            part = part or {
                "part_index": index,
                "title": f"P{index + 1}",
                "duration": 0,
            }
            child_payload = dict(payload)
            child_payload.update(
                {
                    "multipart_part": {
                        "index": index,
                        "title": part.get("title") or f"P{index + 1}",
                        "duration": part.get("duration") or 0,
                    },
                    "bilibili_batch_child": True,
                    "bilibili_parts": {"mode": "merge", "indices": [index]},
                }
            )
            logger.info(
                f"[{self.name}] 开始处理多P任务 {task_id}: P{index + 1}/{len(indices)}"
            )
            self.process_task(child_payload)
            updated = next(
                (
                    item
                    for item in get_task_parts(task_id)
                    if item["part_index"] == index
                ),
                None,
            )
            if updated and updated.get("status") != "COMPLETED":
                from ..task_parts import update_task_part

                parent = db.get_task(task_id) or {}
                update_task_part(
                    task_id,
                    index,
                    {
                        "status": "FAILED",
                        "error_message": parent.get("error_message") or "该分P处理失败",
                    },
                )

        parts = get_task_parts(task_id)
        completed = [part for part in parts if part["status"] == "COMPLETED"]
        failed = [part for part in parts if part["status"] == "FAILED"]
        if not completed:
            db.update_task(
                task_id,
                {
                    "status": TaskStatus.FAILED,
                    "progress": 100.0,
                    "error_message": "所有选中的分P均处理失败。",
                },
            )
            return

        transcript_blocks = []
        summary_blocks = []
        total_duration = 0.0
        for part in completed:
            title = part.get("title") or f"P{part['part_index'] + 1}"
            transcript_blocks.append(
                f"## P{part['part_index'] + 1}：{title}\n\n{part.get('transcript') or ''}"
            )
            if part.get("summary"):
                summary_blocks.append(
                    f"## P{part['part_index'] + 1}：{title}\n\n{part['summary']}"
                )
            total_duration += float(
                part.get("audio_duration") or part.get("duration") or 0
            )

        failed_labels = ", ".join(f"P{part['part_index'] + 1}" for part in failed)
        transcript_path = os.path.join(self.output_dir, f"{task_id}_multipart.txt")
        with open(transcript_path, "w", encoding="utf-8") as file:
            file.write("\n\n".join(transcript_blocks))
        db.update_task(
            task_id,
            {
                "transcript": "\n\n".join(transcript_blocks),
                "audio_duration": total_duration,
                "progress": 95.0,
                "status": TaskStatus.SUMMARIZING,
                "error_message": f"部分分P处理失败：{failed_labels}"
                if failed
                else None,
            },
        )
        if self.summary_worker is None:
            db.update_task(
                task_id,
                {
                    "status": TaskStatus.PARTIAL if failed else TaskStatus.COMPLETED,
                    "progress": 100.0,
                },
            )
            return

        summary_mode = str(payload.get("summary_mode") or "").strip().lower()
        if summary_mode == "none":
            # 仅转录模式：分P转录已完成且合并完成，跳过总览 AI 总结，直接终态
            logger.info(
                f"[{self.name}] 仅转录模式，跳过 AI 总结: task_id={task_id}, "
                f"completed={len(completed)}, failed={len(failed)}"
            )
            db.update_task(
                task_id,
                {
                    "status": TaskStatus.PARTIAL if failed else TaskStatus.COMPLETED,
                    "progress": 100.0,
                },
            )
            # “总结标题”开关（默认开启）：对合并后的完整转录生成一次标题
            # （各分P子任务已跳过，避免重复调用），失败静默降级。
            # self._loop 守卫：未启动的事件循环（如同步单测）下不创建未 await 的协程。
            if self._loop and bool(payload.get("generate_topic", True)):
                from ..llm.llm_worker import generate_topic_for_task
                self._submit_coro(
                    generate_topic_for_task(
                        self.summary_worker,
                        task_id,
                        "\n\n".join(transcript_blocks),
                    )
                )
            return

        overview_path = os.path.join(
            self.output_dir, f"{task_id}_multipart_overview.txt"
        )
        with open(overview_path, "w", encoding="utf-8") as file:
            file.write(
                "请先给出整套视频的总体概览，再按分P分别总结。\n\n"
                + "\n\n".join(summary_blocks)
            )
        asyncio.run(
            self.summary_worker.process_task(
                {
                    "task_id": task_id,
                    "intermediate_file_path": overview_path,
                    "output_file": os.path.join(
                        self.output_dir, f"{task_id}_summary.md"
                    ),
                    "summary_mode": payload.get("summary_mode") or "auto",
                    "multipart_overview": True,
                    "multipart_failed": [part["part_index"] for part in failed],
                }
            )
        )

    def process_task(self, payload: Any):
        """
        下载视频并将其传递给下一个工作单元。

        :param payload: 包含 'video_url' 的字典。
        """
        video_url = payload.get("video_url")
        quality = payload.get("quality", "best")
        task_id = payload.get("task_id")  # 用于更新进度

        if payload.get("multipart_batch") and not payload.get("bilibili_batch_child"):
            self._process_bilibili_multipart(payload)
            return
        if payload.get("multipart_part"):
            from ..task_parts import update_task_part

            update_task_part(
                str(task_id),
                int(payload["multipart_part"]["index"]),
                {"status": "DOWNLOADING", "progress": 0, "error_message": None},
            )

        if not video_url:
            error_msg = "任务负载中缺少 'video_url'"
            logger.error(f"[{self.name}] 错误: {error_msg}")
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify

                self._submit_coro(
                    update_and_notify(
                        task_id,
                        {"status": TaskStatus.FAILED, "error_message": error_msg},
                    )
                )
            return

        try:
            if task_id and self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，跳过下载: {task_id}")

            if not payload.get(
                "re_download_only"
            ) and self._try_process_with_bilibili_subtitle(payload):
                return
        except TaskCancelledError as e:
            logger.info(f"[{self.name}] {e}")
            return

        logger.info(f"[{self.name}] 开始下载视频: {video_url} (质量: {quality})")

        if task_id:
            from ..db import TaskStatus
            from ..task_updater import update_and_notify

            self._submit_coro(
                update_and_notify(task_id, {"status": TaskStatus.DOWNLOADING})
            )

        def progress_hook(d):
            if task_id and self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止下载: {task_id}")

            if d["status"] == "error":
                error_msg = f"yt-dlp 下载时报告错误: {d.get('error', '未知错误')}"
                logger.error(f"[{self.name}] {error_msg}")
                raise yt_dlp.utils.DownloadError(error_msg)

            if d["status"] == "downloading":
                raw_p = d.get("_percent_str", "0%")
                # 首先清理 ANSI 颜色代码，然后移除百分号和空格
                clean_p = re.sub(r"\x1B(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])", "", raw_p)
                p = clean_p.replace("%", "").strip()
                try:
                    progress = float(p)
                    if task_id:
                        from ..api import notify_progress_update

                        # 直接广播进度，不再写入数据库
                        self._submit_coro(notify_progress_update(task_id, progress))
                except ValueError:
                    logger.warning(
                        f"[{self.name}] 无法从 yt-dlp 解析进度: '{p}' (原始值: '{raw_p}')"
                    )

        try:
            # 配置 ffmpeg 路径（使用 FFmpegHelper）
            ffmpeg_location = FFmpegHelper.get_yt_dlp_ffmpeg_location()

            if quality == "audio_only":
                ydl_opts = {
                    "outtmpl": os.path.join(self.output_dir, "%(id)s.%(ext)s"),
                    "format": "worstvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]/worst[ext=mp4]/best",
                    "progress_hooks": [progress_hook],
                    "writethumbnail": False,
                    "writesubtitles": False,
                }
            else:
                ydl_opts = {
                    "outtmpl": os.path.join(self.output_dir, "%(id)s.%(ext)s"),
                    "format": "worstvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]/worst[ext=mp4]/best",
                    "merge_output_format": "mp4",
                    "progress_hooks": [progress_hook],
                }

            multipart_part = payload.get("multipart_part")
            if multipart_part and task_id:
                part_index = int(multipart_part.get("index", 0)) + 1
                ydl_opts["outtmpl"] = os.path.join(
                    self.output_dir, f"{task_id}_p{part_index}.%(ext)s"
                )
            selected_playlist_items = self._selected_bilibili_playlist_items(payload)
            parts_config = payload.get("bilibili_parts")
            if (
                self._is_bilibili_url(str(video_url))
                and isinstance(parts_config, dict)
                and parts_config.get("mode") == "merge"
                and selected_playlist_items
                and len(selected_playlist_items.split(",")) > 1
            ):
                raise RuntimeError(
                    "所选多个分P没有可用字幕，当前不能合并为一个本地 ASR 任务。"
                    "请改用拆分为多个任务。"
                )
            if selected_playlist_items:
                ydl_opts["playlist_items"] = selected_playlist_items

            # 如果有 ffmpeg 路径，添加到配置中
            if ffmpeg_location:
                ydl_opts["ffmpeg_location"] = ffmpeg_location

            # B 站需要浏览器级别的请求头，否则触发 412 反爬
            if self._is_bilibili_url(str(video_url)):
                bilibili_headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.bilibili.com/",
                    "Origin": "https://www.bilibili.com",
                }
                sessdata, cookie_source = self._resolve_bilibili_sessdata(payload)
                if sessdata:
                    bilibili_headers["Cookie"] = f"SESSDATA={sessdata}"
                    logger.info(
                        f"[{self.name}] B 站下载使用 Cookie (source={cookie_source})"
                    )
                else:
                    logger.warning(
                        f"[{self.name}] B 站下载无可用 Cookie，仅使用浏览器请求头"
                    )
                ydl_opts["http_headers"] = bilibili_headers

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(video_url, download=True)
                video_paths = self._resolve_downloaded_video_paths(ydl, info_dict)

            if len(video_paths) != 1:
                raise RuntimeError(
                    f"下载结果包含 {len(video_paths)} 个分P，无法作为单一转录任务处理。"
                    "请在分P选择器中选择拆分为多个任务，或仅选择一个分P。"
                )
            video_path = video_paths[0]

            logger.info(f"[{self.name}] 视频下载成功: {video_path}")

            if task_id and self.is_task_cancelled(task_id):
                raise TaskCancelledError(f"任务已取消，停止后续处理: {task_id}")

            # re-download 专用分支：下载成功即恢复原状态，不派发后续 worker
            if payload.get("re_download_only"):
                if task_id:
                    from ..db import TaskStatus
                    from ..task_updater import update_and_notify

                    updates = {
                        "audio_downloaded": True,
                        "audio_missing_reason": None,
                        "error_message": None,
                    }
                    restore_status = payload.get("restore_status")
                    if restore_status:
                        try:
                            updates["status"] = TaskStatus(str(restore_status).upper())
                        except ValueError:
                            logger.warning(
                                f"[{self.name}] 无效的恢复状态 '{restore_status}'，"
                                f"保持当前状态"
                            )
                    self._submit_coro(update_and_notify(task_id, updates))
                logger.info(
                    f"[{self.name}] re-download 完成: task_id={task_id}, "
                    f"恢复状态: {payload.get('restore_status')}, video={video_path}"
                )
                return

            if task_id and self._is_bilibili_url(str(video_url)):
                self._submit_coro(
                    self._resolve_and_save_bilibili_author(task_id, str(video_url))
                )

            if task_id:
                logger.info(
                    f"[VideoDownloader] Download completed: task_id={task_id}, "
                    f"status: DOWNLOADING→TRANSCRIBING, video={video_path}"
                )

                from ..db import TaskStatus
                from ..task_updater import update_and_notify

                self._submit_coro(
                    update_and_notify(
                        task_id,
                        {
                            "status": TaskStatus.TRANSCRIBING,
                            "audio_downloaded": True,
                            "audio_missing_reason": None,
                        },
                    )
                )

            if self.next_worker:
                next_payload = payload.copy()
                next_payload["video_file"] = video_path
                base_name = os.path.splitext(os.path.basename(video_path))[0]
                next_payload["audio_file"] = os.path.join(
                    self.output_dir, f"{base_name}.mp3"
                )
                next_payload["output_file"] = os.path.join(
                    self.output_dir, f"{base_name}_summary.md"
                )

                if payload.get("bilibili_batch_child"):
                    intermediate_file_path = self.next_worker.process_task(next_payload)
                    summary_mode = str(
                        next_payload.get("summary_mode") or ""
                    ).strip().lower()
                    if summary_mode == "none":
                        # 仅转录模式：TranscriberWorker 已将分P置为 COMPLETED，跳过 AI 总结
                        logger.info(
                            f"[{self.name}] 仅转录模式，跳过 AI 总结: task_id={task_id}, "
                            f"part={int(payload.get('multipart_part', {}).get('index', 0)) + 1}"
                        )
                    elif intermediate_file_path and self.summary_worker:
                        asyncio.run(
                            self.summary_worker.process_task(
                                {
                                    **next_payload,
                                    "intermediate_file_path": intermediate_file_path,
                                    "multipart_part": payload.get("multipart_part"),
                                }
                            )
                        )
                else:
                    self._submit_coro(self.next_worker.add_task(next_payload))

        except TaskCancelledError as e:
            logger.info(f"[{self.name}] {e}")
        except Exception as e:
            logger.error(f"[{self.name}] 下载视频时出错: {e}", exc_info=True)
            if payload.get("multipart_part"):
                from ..task_parts import update_task_part

                update_task_part(
                    str(task_id),
                    int(payload["multipart_part"]["index"]),
                    {"status": "FAILED", "error_message": str(e)},
                )
                return
            if task_id:
                from ..db import TaskStatus
                from ..task_updater import update_and_notify

                # 清理错误信息中的 ANSI 转义序列
                clean_error = re.sub(
                    r"\x1B(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])", "", str(e)
                )
                self._submit_coro(
                    update_and_notify(
                        task_id,
                        {"status": TaskStatus.FAILED, "error_message": clean_error},
                    )
                )
