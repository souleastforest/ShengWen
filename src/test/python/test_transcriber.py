# ruff: noqa: E402

import unittest
import os
import sys

# 将项目根目录添加到 Python 路径
path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, path)
print(f"已将 {path} 添加到 sys.path")

from src.main.python.sheng_wen.transcriber.transcriber import get_transcriber, TranscriptionResult, ModelLoadError, TranscriptionError

def format_duration(seconds):
    """格式化时间戳为HHMMSS"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours:02d}{minutes:02d}{seconds:02d}"

def save_results(segments, output_path):
    """保存转录结果到文件"""
    with open(output_path, "w", encoding="utf-8", errors='replace') as f:
        for seg in segments:
            clean_text = seg['text'].encode('utf-8', 'replace').decode('utf-8')
            line = f"{format_duration(seg['start'])}{clean_text.strip()}"
            print(line)
            f.write(line)

class TestTranscriber(unittest.TestCase):

    def setUp(self):
        """设置测试音频文件"""
        self.temp_dir = "temp"
        self.test_file_path = os.path.join(self.temp_dir, "test.mp3")
        self.non_existent_file = os.path.join(self.temp_dir, "non_existent.mp3")
        
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
            
        if not os.path.exists(self.test_file_path):
            self.fail(f"测试音频文件未找到: {self.test_file_path}")

    def test_transcribe_with_factory_success(self):
        """
        (成功场景) 使用工厂函数测试转录，并验证结果。
        """
        try:
            transcriber = get_transcriber("fast_whisper", model_size="123")
            
            print(f"--- 正在转录 {self.test_file_path} ---")
            result = transcriber.transcribe(self.test_file_path)
            
            self.assertIsInstance(result, TranscriptionResult)
            print("--- 性能指标 ---")
            print(f"模型加载耗时: {result.model_load_time:.2f}s")
            print(f"音频时长: {result.audio_duration:.2f}s")
            print(f"转录耗时: {result.transcription_time:.2f}s")
            
            output_filename = os.path.splitext(os.path.basename(self.test_file_path))[0] + ".txt"
            output_path = os.path.join(self.temp_dir, output_filename)
            save_results(result.segments, output_path)
            print(f"--- 结果已保存至 {output_path} ---")

            self.assertTrue(os.path.exists(output_path))
            print("--- 测试通过 (成功场景) ---")
        except Exception as e:
            self.fail(f"测试失败，出现异常: {e}")

    def test_invalid_model_name_raises_exception(self):
        """
        (失败场景) 测试使用无效的模型名称时是否抛出 ModelLoadError。
        """
        print("--- 测试: 无效的模型名称 ---")
        with self.assertRaises(ModelLoadError) as cm:
            get_transcriber("fast_whisper", model_size="invalid-model-name")
        
        print(f"成功捕获到预期的异常: {cm.exception}")
        self.assertIn("加载模型 'invalid-model-name' 失败", str(cm.exception))
        print("--- 测试通过 (无效模型) ---")

    def test_non_existent_file_raises_exception(self):
        """
        (失败场景) 测试转录不存在的文件时是否抛出 TranscriptionError。
        """
        print("--- 测试: 转录不存在的文件 ---")
        transcriber = get_transcriber("fast_whisper", model_size="tiny")
        
        with self.assertRaises(TranscriptionError) as cm:
            transcriber.transcribe(self.non_existent_file)
            
        print(f"成功捕获到预期的异常: {cm.exception}")
        self.assertIn(f"转录文件 '{self.non_existent_file}' 时发生错误", str(cm.exception))
        print("--- 测试通过 (文件不存在) ---")
        
    def test_invalid_transcriber_name_raises_exception(self):
        """
        (失败场景) 测试使用无效的转录器名称时是否抛出 ValueError。
        """
        print("--- 测试: 无效的转录器名称 ---")
        with self.assertRaises(ValueError) as cm:
            get_transcriber("invalid_transcriber_name")
        
        print(f"成功捕获到预期的异常: {cm.exception}")
        self.assertIn("找不到名为 'invalid_transcriber_name' 的转录器", str(cm.exception))
        print("--- 测试通过 (无效转录器) ---")


if __name__ == '__main__':
    unittest.main()
