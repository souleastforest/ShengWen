# ruff: noqa: E402

import os
import sys
import tempfile
import unittest


path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, path)

from src.main.python.sheng_wen.config.settings import JSONConfigManager


class TestJSONConfigManager(unittest.TestCase):
    def test_switch_to_auto_download_clears_manual_model_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "settings.json")
            manager = JSONConfigManager(config_path=config_path)

            manager.update_section(
                "whisper",
                {
                    "model_source": "manual_path",
                    "model_path": "E:/models/faster-whisper/tiny",
                    "faster_whisper_model_path": "E:/legacy/model",
                },
            )
            manager.save_transcription_config({"model_source": "auto_download"})

            whisper = manager.get_whisper_config()
            self.assertEqual(whisper.model_source, "auto_download")
            self.assertIsNone(whisper.model_path)
            self.assertIsNone(whisper.faster_whisper_model_path)


if __name__ == "__main__":
    unittest.main()
