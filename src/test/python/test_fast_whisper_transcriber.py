# ruff: noqa: E402

import os
import sys
import unittest
from unittest.mock import patch


path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, path)

from src.main.python.sheng_wen.transcriber.fast_whisper_transcriber import FastWhisperTranscriber


class TestFastWhisperTranscriberHelpers(unittest.TestCase):
    def test_normalize_proxy_url_upgrades_socks_scheme(self):
        normalized, changed = FastWhisperTranscriber._normalize_proxy_url("socks://127.0.0.1:7897")
        self.assertTrue(changed)
        self.assertEqual(normalized, "socks5://127.0.0.1:7897")

    def test_patched_proxy_env_for_httpx_temporarily_rewrites_socks_proxy(self):
        with patch.dict(os.environ, {"ALL_PROXY": "socks://127.0.0.1:7897"}, clear=False):
            with FastWhisperTranscriber._patched_proxy_env_for_httpx():
                self.assertEqual(os.environ["ALL_PROXY"], "socks5://127.0.0.1:7897")
            self.assertEqual(os.environ["ALL_PROXY"], "socks://127.0.0.1:7897")

    def test_build_model_load_error_message_for_proxy_scheme(self):
        message = FastWhisperTranscriber._build_model_load_error_message(
            "base",
            ValueError("Unknown scheme for proxy URL URL('socks://127.0.0.1:7897/')"),
        )
        self.assertIn("代理地址使用了不兼容的 socks:// 格式", message)
        self.assertIn("socks5://127.0.0.1:端口", message)

    def test_build_model_load_error_message_for_hub_download_failure(self):
        class LocalEntryNotFoundError(Exception):
            pass

        message = FastWhisperTranscriber._build_model_load_error_message(
            "base",
            LocalEntryNotFoundError(
                "An error happened while trying to locate the files on the Hub and "
                "we cannot find the appropriate snapshot folder for the specified revision on the local disk. "
                "Please check your internet connection and try again."
            ),
        )
        self.assertIn("本地未找到该模型缓存", message)
        self.assertIn("huggingface.co", message)


if __name__ == "__main__":
    unittest.main()
