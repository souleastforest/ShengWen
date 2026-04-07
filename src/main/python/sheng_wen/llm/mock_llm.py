from .llm import LLM, LLMConfig, LLMMessage, LLMResponseError
from typing import List, Callable, Union
import asyncio

class MockLLM(LLM):
    """
    一个用于测试和开发的模拟 LLM 实现。
    它不会进行任何真实的网络调用。
    """
    def __init__(self, config: LLMConfig, **kwargs):
        super().__init__(config, **kwargs)
        self.processing_delay = kwargs.get("processing_delay", 0.1) # 模拟每个 token 的处理延迟
        self.should_fail = kwargs.get("should_fail", False) # 控制是否模拟失败场景

    async def response(
        self,
        messages: List[LLMMessage],
        resp_callback: Callable[[Union[str, LLMResponseError]], None],
        stream: bool = True,
        timeout: int = 60
    ):
        """
        模拟 LLM 的响应。

        它会根据 `stream` 参数模拟流式或非流式响应，并可以模拟失败情况。
        """
        print(f"--- MockLLM 收到请求 (stream={stream}) ---")
        last_message = messages[-1].content if messages else ""
        response_text = f"这是对'{last_message}'的模拟回复。"

        try:
            if self.should_fail:
                await asyncio.sleep(1) # 模拟网络延迟
                raise LLMResponseError("模拟的API错误：无效的API密钥。")

            if stream:
                # 模拟流式响应，一个字一个字地输出
                for char in response_text:
                    await asyncio.sleep(self.processing_delay)
                    resp_callback(char)
            else:
                # 模拟非流式响应
                await asyncio.sleep(self.processing_delay * len(response_text.split()))
                resp_callback(response_text)
            
            print("--- MockLLM 请求处理完毕 ---")

        except asyncio.CancelledError:
            # 与真实 LLM 客户端保持一致：取消时立即中止，不转为业务错误。
            raise
        except Exception as e:
            error = LLMResponseError(f"MockLLM 内部错误: {e}")
            resp_callback(error)
            print(f"--- MockLLM 请求处理失败: {error} ---")
