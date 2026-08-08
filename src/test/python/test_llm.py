# ruff: noqa: E402

import unittest
import asyncio
import os
import sys
from typing import Union

# 将项目根目录添加到 Python 路径
path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, path)
print(f"已将 {path} 添加到 sys.path")

from src.main.python.sheng_wen.llm.llm import LLMConfig, LLMMessage, LLMResponseError
from src.main.python.sheng_wen.llm.mock_llm import MockLLM
from src.main.python.sheng_wen.llm.litellm_client import LiteLLMClient

# --- 测试 MockLLM ---
class TestMockLLM(unittest.TestCase):

    def setUp(self):
        """设置测试用的 LLM 配置。"""
        self.config = LLMConfig(
            base_url="http://localhost:1234/v1",
            api_key="mock-key",
            model_id="mock-model"
        )
        self.messages = [LLMMessage(role="user", content="你好")]

    def run_async_test(self, coro):
        """辅助函数，用于在同步的 unittest 中运行异步代码。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    def test_stream_response(self):
        """测试模拟LLM的流式响应。"""
        llm = MockLLM(self.config)
        received_chunks = []
        def callback(chunk: Union[str, LLMResponseError]):
            self.assertIsInstance(chunk, str)
            received_chunks.append(chunk)
            # 实时打印接收到的内容片段
            print(chunk, end='', flush=True)

        async def test_logic():
            await llm.response(self.messages, callback, stream=True)
            full_response = "".join(received_chunks)
            self.assertIn("模拟回复", full_response)
        self.run_async_test(test_logic())

    def test_failure_simulation(self):
        """测试模拟LLM的失败场景。"""
        llm = MockLLM(self.config, should_fail=True)
        received_errors = []
        def callback(error: Union[str, LLMResponseError]):
            self.assertIsInstance(error, LLMResponseError)
            received_errors.append(error)

        async def test_logic():
            await llm.response(self.messages, callback)
            self.assertEqual(len(received_errors), 1)
            self.assertIn("模拟的API错误", str(received_errors[0]))
        self.run_async_test(test_logic())

# --- 测试 LiteLLMClient ---
# 注意：这些是集成测试，需要一个正在运行的、与OpenAI兼容的服务端点。
# 在运行测试前，请确保设置了正确的环境变量或配置。
# @unittest.skipUnless(
#     os.environ.get("RUN_LITELLM_INTEGRATION_TESTS") == "true",
#     "跳过 LiteLLM 集成测试。设置 RUN_LITELLM_INTEGRATION_TESTS=true 以运行。"
# )
class TestLiteLLMClient(unittest.TestCase):
    
    def setUp(self):
        """设置集成测试的配置。"""
        # 使用你提供的 NVIDIA 端点和 Kimi 模型进行测试
        self.config = LLMConfig(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key="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",
            model_id="deepseek-ai/deepseek-r1"
        )
        self.messages = [LLMMessage(role="user", content="你好，请讲一个关于程序员的笑话。")]
        
        # if not self.config.api_key:
        #     self.fail("必须设置 LITELLM_API_KEY 环境变量以进行集成测试。")

    def run_async_test(self, coro):
        """异步测试运行器。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    def test_litellm_stream_response(self):
        """测试 LiteLLMClient 的流式响应。"""
        llm = LiteLLMClient(self.config)
        
        received_content = []
        def callback(chunk: Union[str, LLMResponseError]):
            if isinstance(chunk, LLMResponseError):
                self.fail(f"收到意外的错误: {chunk}")
            received_content.append(chunk)
            # 实时打印接收到的内容片段
            print(chunk, end='', flush=True)

        async def test_logic():
            print(f"--- 开始 LiteLLM 流式测试 (模型: {self.config.model_id}) ---")
            await llm.response(self.messages, callback, stream=True)
            
            full_response = "".join(received_content)
            self.assertGreater(len(full_response.strip()), 0, "流式响应不应为空。")
            print(f"\n--- LiteLLM 流式测试成功，收到[{full_response[:10]}...] ---")
        
        self.run_async_test(test_logic())


if __name__ == '__main__':
    unittest.main()
