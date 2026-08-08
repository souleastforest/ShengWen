# ruff: noqa: E402

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add project root to path
path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, path)

from src.main.python.sheng_wen.transcriber.transcriber import (
    Transcriber,
    ModelLoadError,
)


class MockTensor:
    """Mock tensor object with .to() method"""

    def __init__(self, device="cpu"):
        self.device = device

    def to(self, device):
        self.device = device
        return self


class MockModelOutput:
    """Mock model output with sequences attribute"""

    def __init__(self, sequences):
        self.sequences = sequences


class TestVibeVoiceAsrTranscriberInitialization(unittest.TestCase):
    """Test VibeVoiceAsrTranscriber initialization"""

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_constructor_with_valid_parameters(self):
        """Test constructor with valid parameters"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        transcriber = VibeVoiceAsrTranscriber(
            model_path="/path/to/model",
            device="cuda:0",
            max_new_tokens=4096,
        )

        self.assertEqual(transcriber.model_path, "/path/to/model")
        self.assertEqual(transcriber.device, "cuda:0")
        self.assertEqual(transcriber.max_new_tokens, 4096)
        self.assertEqual(transcriber.language_model_pretrained_name, "Qwen/Qwen2.5-7B")
        self.assertEqual(transcriber.dtype, "bfloat16")
        self.assertIsNone(transcriber.processor)
        self.assertIsNone(transcriber.model)
        self.assertEqual(transcriber.model_load_time, 0.0)

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_constructor_default_values(self):
        """Test constructor default values"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        transcriber = VibeVoiceAsrTranscriber(model_path="/path/to/model")

        self.assertEqual(transcriber.model_path, "/path/to/model")
        self.assertEqual(transcriber.device, "cuda")  # default device
        self.assertEqual(transcriber.max_new_tokens, 8192)  # default max_new_tokens
        self.assertIsNone(transcriber.processor)
        self.assertIsNone(transcriber.model)


class TestVibeVoiceAsrTranscriberTimestampParsing(unittest.TestCase):
    """Test _parse_timestamp static method"""

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_parse_timestamp_hms_format(self):
        """Test H:M:S format parsing (e.g., '1:23:45.000' -> 5025.0)"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        result = VibeVoiceAsrTranscriber._parse_timestamp("1:23:45.000")
        self.assertEqual(result, 5025.0)

        result = VibeVoiceAsrTranscriber._parse_timestamp("2:30:15.500")
        self.assertEqual(result, 9015.5)

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_parse_timestamp_ms_format(self):
        """Test M:S format parsing (e.g., '12:34.567' -> 754.567)"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        result = VibeVoiceAsrTranscriber._parse_timestamp("12:34.567")
        self.assertEqual(result, 754.567)

        result = VibeVoiceAsrTranscriber._parse_timestamp("5:30.000")
        self.assertEqual(result, 330.0)

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_parse_timestamp_empty_or_invalid(self):
        """Test empty or invalid strings return 0.0"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        result = VibeVoiceAsrTranscriber._parse_timestamp("")
        self.assertEqual(result, 0.0)

        result = VibeVoiceAsrTranscriber._parse_timestamp("invalid")
        self.assertEqual(result, 0.0)

        # Test that invalid format with colon returns 0.0 (graceful error handling)
        result = VibeVoiceAsrTranscriber._parse_timestamp("abc:def")
        self.assertEqual(result, 0.0)

        # Test other invalid numeric formats
        result = VibeVoiceAsrTranscriber._parse_timestamp("abc:def:ghi")
        self.assertEqual(result, 0.0)

        result = VibeVoiceAsrTranscriber._parse_timestamp(12.5)
        self.assertEqual(result, 12.5)

        result = VibeVoiceAsrTranscriber._parse_timestamp(42)
        self.assertEqual(result, 42.0)


class TestVibeVoiceAsrTranscriberInputMovement(unittest.TestCase):
    """Test _move_inputs_to_device static method"""

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_move_tensor_like_object(self):
        """Test tensor-like objects with .to() method"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        tensor = MockTensor(device="cpu")
        result = VibeVoiceAsrTranscriber._move_inputs_to_device(tensor, "cuda:0")

        self.assertEqual(result.device, "cuda:0")

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_move_dictionary_of_tensors(self):
        """Test dictionary of tensors"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        tensor1 = MockTensor(device="cpu")
        tensor2 = MockTensor(device="cpu")
        inputs = {"input_ids": tensor1, "attention_mask": tensor2}

        result = VibeVoiceAsrTranscriber._move_inputs_to_device(inputs, "cuda:0")

        self.assertEqual(result["input_ids"].device, "cuda:0")
        self.assertEqual(result["attention_mask"].device, "cuda:0")

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_move_non_tensor_inputs(self):
        """Test non-tensor inputs are returned as-is"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        # Test with string
        result = VibeVoiceAsrTranscriber._move_inputs_to_device(
            "string_input", "cuda:0"
        )
        self.assertEqual(result, "string_input")

        # Test with list
        result = VibeVoiceAsrTranscriber._move_inputs_to_device([1, 2, 3], "cuda:0")
        self.assertEqual(result, [1, 2, 3])

        # Test with dict containing non-tensor values
        result = VibeVoiceAsrTranscriber._move_inputs_to_device(
            {"key": "value"}, "cuda:0"
        )
        self.assertEqual(result, {"key": "value"})


class TestVibeVoiceAsrTranscriberExtractGeneratedIds(unittest.TestCase):
    """Test _extract_generated_ids static method"""

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_extract_from_object_with_sequences(self):
        """Test objects with .sequences attribute"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        sequences = [[1, 2, 3, 4, 5]]
        output = MockModelOutput(sequences=sequences)
        result = VibeVoiceAsrTranscriber._extract_generated_ids(output)

        self.assertEqual(result, sequences)

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_extract_from_raw_tensor(self):
        """Test raw tensor inputs (no .sequences attribute)"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        raw_tensor = [[1, 2, 3]]
        result = VibeVoiceAsrTranscriber._extract_generated_ids(raw_tensor)

        self.assertEqual(result, raw_tensor)


class TestVibeVoiceAsrTranscriberModelLoading(unittest.TestCase):
    """Test model loading and error handling"""

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_non_cuda_device_raises_model_load_error(self):
        """Test non-CUDA device raises ModelLoadError"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        transcriber = VibeVoiceAsrTranscriber(
            model_path="/path/to/model",
            device="cpu",  # Invalid device
        )

        with self.assertRaises(ModelLoadError) as cm:
            transcriber._ensure_loaded()

        self.assertIn("CUDA", str(cm.exception))

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    @patch(
        "src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber.VibeVoiceASRProcessor"
    )
    @patch(
        "src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber.VibeVoiceASRForConditionalGeneration"
    )
    @patch("src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber.torch")
    def test_missing_model_path_handling(
        self, mock_torch, mock_model_class, mock_processor_class
    ):
        """Test missing model path handling during model loading"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        # Simulate exception during processor loading
        mock_processor_class.from_pretrained.side_effect = Exception("Model not found")

        transcriber = VibeVoiceAsrTranscriber(
            model_path="/nonexistent/path",
            device="cuda:0",
        )

        with self.assertRaises(ModelLoadError) as cm:
            transcriber._ensure_loaded()

        self.assertIn("加载 VibeVoice-ASR 模型失败", str(cm.exception))


class TestVibeVoiceAsrTranscriberIntegration(unittest.TestCase):
    """Test integration with base class"""

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_inheritance_from_transcriber(self):
        """Test that VibeVoiceAsrTranscriber inherits from Transcriber"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )

        self.assertTrue(issubclass(VibeVoiceAsrTranscriber, Transcriber))

    @patch.dict(
        "sys.modules",
        {
            "vibevoice": MagicMock(),
            "vibevoice.modular": MagicMock(),
            "vibevoice.modular.modeling_vibevoice_asr": MagicMock(),
            "vibevoice.processor": MagicMock(),
            "vibevoice.processor.vibevoice_asr_processor": MagicMock(),
            "torch": MagicMock(),
        },
    )
    def test_transcribe_method_signature(self):
        """Test that transcribe method signature matches base class"""
        from src.main.python.sheng_wen.transcriber.vibe_voice_asr_transcriber import (
            VibeVoiceAsrTranscriber,
        )
        import inspect

        # Get method signatures
        base_transcribe = Transcriber.transcribe
        impl_transcribe = VibeVoiceAsrTranscriber.transcribe

        base_sig = inspect.signature(base_transcribe)
        impl_sig = inspect.signature(impl_transcribe)

        # Check parameter names match
        base_params = list(base_sig.parameters.keys())
        impl_params = list(impl_sig.parameters.keys())

        self.assertEqual(base_params, impl_params)


if __name__ == "__main__":
    unittest.main()
