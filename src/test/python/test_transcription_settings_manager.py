# ruff: noqa: E402

import os
import sys
import tempfile
import unittest
from unittest.mock import patch


path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, path)

from src.main.python.sheng_wen.transcriber.settings_manager import TranscriptionSettingsManager


class _DummyTranscriberWorker:
    def __init__(self):
        self.updated_transcriber = None

    def update_transcriber(self, transcriber):
        self.updated_transcriber = transcriber


class TestTranscriptionSettingsManager(unittest.TestCase):
    def test_toggle_bilibili_subtitle_fetch(self):
        manager = TranscriptionSettingsManager(initial_device="cpu", model_size="tiny")

        original = manager.get_settings()
        self.assertTrue(original["enable_bilibili_subtitle_fetch"])

        updated = manager.update_settings(enable_bilibili_subtitle_fetch=False)
        self.assertFalse(updated["enable_bilibili_subtitle_fetch"])

        reverted = manager.update_settings(enable_bilibili_subtitle_fetch=True)
        self.assertTrue(reverted["enable_bilibili_subtitle_fetch"])

    def test_reject_empty_update(self):
        manager = TranscriptionSettingsManager(initial_device="cpu", model_size="tiny")
        with self.assertRaises(ValueError):
            manager.update_settings()

    def test_bilibili_cookie_priority(self):
        with patch.dict(os.environ, {"BILIBILI_SESSDATA": "env_cookie_123456"}, clear=False):
            manager = TranscriptionSettingsManager(initial_device="cpu", model_size="tiny")

            settings = manager.get_settings()
            self.assertTrue(settings["has_bilibili_sessdata"])
            self.assertEqual(settings["bilibili_cookie_source"], "env")
            self.assertIn("****", settings["bilibili_sessdata_masked"])

            manager.update_settings(bilibili_sessdata="global_cookie_abcdef")
            settings = manager.get_settings()
            self.assertEqual(settings["bilibili_cookie_source"], "global")

            value, source = manager.resolve_bilibili_sessdata("task_cookie_xyz")
            self.assertEqual(source, "task")
            self.assertEqual(value, "task_cookie_xyz")

    def test_update_device_without_worker_should_not_rebuild_transcriber(self):
        manager = TranscriptionSettingsManager(initial_device="cuda", model_size="tiny")

        with patch("src.main.python.sheng_wen.transcriber.settings_manager.get_transcriber") as mocked_get_transcriber:
            settings = manager.update_settings(device="cpu")

        mocked_get_transcriber.assert_not_called()
        self.assertEqual(settings["device"], "cpu")
        self.assertEqual(manager.get_runtime_state()["device"], "cpu")

    def test_update_device_with_worker_should_rebuild_and_apply(self):
        manager = TranscriptionSettingsManager(initial_device="cuda", model_size="tiny")
        worker = _DummyTranscriberWorker()
        manager.bind_transcriber_worker(worker)

        sentinel_transcriber = object()
        with patch(
            "src.main.python.sheng_wen.transcriber.settings_manager.get_transcriber",
            return_value=sentinel_transcriber,
        ) as mocked_get_transcriber:
            settings = manager.update_settings(device="cpu")

        mocked_get_transcriber.assert_called_once()
        self.assertIs(worker.updated_transcriber, sentinel_transcriber)
        self.assertEqual(settings["device"], "cpu")
        self.assertEqual(manager.get_runtime_state()["device"], "cpu")

    def test_update_device_same_value_should_not_rebuild(self):
        manager = TranscriptionSettingsManager(initial_device="cpu", model_size="tiny")
        worker = _DummyTranscriberWorker()
        manager.bind_transcriber_worker(worker)

        with patch("src.main.python.sheng_wen.transcriber.settings_manager.get_transcriber") as mocked_get_transcriber:
            settings = manager.update_settings(device="cpu")

        mocked_get_transcriber.assert_not_called()
        self.assertIsNone(worker.updated_transcriber)
        self.assertEqual(settings["device"], "cpu")

    def test_manual_model_path_requires_four_files(self):
        manager = TranscriptionSettingsManager(initial_device="cpu", model_size="tiny")
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "config.json"), "w", encoding="utf-8") as f:
                f.write("{}")
            with open(os.path.join(temp_dir, "model.bin"), "wb") as f:
                f.write(b"fake")

            with self.assertRaises(ValueError) as ctx:
                manager.update_settings(model_source="manual_path", model_path=temp_dir)
            self.assertIn("缺少必要文件", str(ctx.exception))

    def test_manual_model_path_accepts_complete_directory(self):
        manager = TranscriptionSettingsManager(initial_device="cpu", model_size="tiny")
        with tempfile.TemporaryDirectory() as temp_dir:
            required_files = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
            for name in required_files:
                file_path = os.path.join(temp_dir, name)
                if name.endswith(".bin"):
                    with open(file_path, "wb") as f:
                        f.write(b"fake")
                else:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("{}")

            settings = manager.update_settings(
                model_source="manual_path",
                model_path=temp_dir,
            )
            self.assertEqual(settings["model_source"], "manual_path")
            self.assertTrue(settings["model_path_valid"])
            self.assertEqual(manager.get_runtime_state()["model_source"], "manual_path")


if __name__ == "__main__":
    unittest.main()
