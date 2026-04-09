# ruff: noqa: E402

import os
import sys
import unittest


path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, path)

from src.main.python.sheng_wen.downloader.video_downloader_worker import VideoDownloaderWorker


class TestBilibiliSubtitleHelpers(unittest.TestCase):
    def setUp(self):
        self.worker = VideoDownloaderWorker(name="VideoDownloaderWorker")

    def test_build_transcript_from_body(self):
        subtitle_json = {
            "body": [
                {"from": 3.2, "to": 4.5, "content": "第一句"},
                {"from": 65.0, "to": 66.0, "content": "第二句"},
            ]
        }

        transcript = self.worker._build_transcript_from_subtitle_json(subtitle_json)

        self.assertEqual(transcript, "000003第一句\n000105第二句\n")

    def test_build_transcript_from_json3_events(self):
        subtitle_json = {
            "events": [
                {"tStartMs": 2000, "segs": [{"utf8": "你好"}, {"utf8": "世界"}]},
                {"tStartMs": 120000, "segs": [{"utf8": "第三句"}]},
            ]
        }

        transcript = self.worker._build_transcript_from_subtitle_json(subtitle_json)

        self.assertEqual(transcript, "000002你好世界\n000200第三句\n")

    def test_is_bilibili_url(self):
        self.assertTrue(self.worker._is_bilibili_url("https://www.bilibili.com/video/BV1xxxx"))
        self.assertTrue(self.worker._is_bilibili_url("https://b23.tv/xxxx"))
        self.assertFalse(self.worker._is_bilibili_url("https://www.youtube.com/watch?v=abc"))


if __name__ == "__main__":
    unittest.main()
