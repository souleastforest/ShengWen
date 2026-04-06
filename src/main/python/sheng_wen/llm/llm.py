from abc import ABC, abstractmethod
from typing import List, Dict, Any, Callable, Union
from dataclasses import dataclass
import asyncio

# --- 自定义异常 ---

class LLMError(Exception):
    """LLM 模块的通用基础异常。"""
    pass

class LLMConnectionError(LLMError):
    """与 LLM 服务连接时发生错误的异常。"""
    pass

class LLMResponseError(LLMError):
    """LLM 服务返回错误或无效响应的异常。"""
    pass

# --- 数据类 ---

@dataclass
class LLMMessage:
    """表示一次对话中的单条消息。"""
    role: str  # 角色 (例如, "user", "assistant", "system")
    content: str # 消息内容

@dataclass
class LLMConfig:
    """LLM 配置的数据类。"""
    base_url: str
    api_key: str
    model_id: str
    temperature: float = 0.7
    context_window_size: int = 1000000
    provider: str = "openai_compatible"
    extra_headers: dict[str, str] | None = None

# --- 抽象基类 ---

class LLM(ABC):
    """
    大语言模型 (LLM) 交互的抽象基类。
    """
    def __init__(self, config: LLMConfig, **kwargs):
        """
        初始化 LLM。
        
        参数:
            config: LLM 的配置对象。
            **kwargs: 其他可选参数。
        """
        self.config = config

    @abstractmethod
    async def response(
        self,
        messages: List[LLMMessage],
        resp_callback: Callable[[Union[str, LLMResponseError]], None],
        stream: bool = True,
        timeout: int = 60
    ):
        """
        异步地发送请求给 LLM 并获取响应。

        参数:
            messages: 发送给模型的历史消息列表。
            resp_callback: 用于接收数据块（流式）或完整响应的回调函数。
                           如果发生错误，回调函数将被调用并传入一个 LLMResponseError 实例。
            stream: 是否开启流式传输。
            timeout: 请求超时时间（秒）。
        """
        pass

# --- 工厂函数 ---

def get_llm(config: LLMConfig, llm_type: str | None = None, **kwargs) -> LLM:
    """
    LLM 客户端工厂函数。

    根据指定的类型和配置返回一个 LLM 客户端实例。
    当 llm_type 未指定时，根据 config.provider 自动路由。

    参数:
        config: LLM 的配置对象。
        llm_type: 要创建的 LLM 客户端类型。若为 None 则根据 provider 自动推断。
        **kwargs: 传递给 LLM 客户端构造函数的其他参数。

    返回:
        一个 LLM 类的实例。

    异常:
        ValueError: 如果指定的 llm_type 无效。
    """
    # 延迟导入以避免循环依赖
    from .litellm_client import LiteLLMClient
    from .anthropic_client import AnthropicClient

    llm_clients = {
        "litellm": LiteLLMClient,
        "anthropic": AnthropicClient,
    }

    # 自动路由：provider 字段决定使用哪个客户端
    if llm_type is None:
        provider = (config.provider or "").strip().lower()
        provider_to_type = {
            "anthropic": "anthropic",
        }
        llm_type = provider_to_type.get(provider, "litellm")

    client_class = llm_clients.get(llm_type)
    if not client_class:
        raise ValueError(f"未知的 LLM 类型: {llm_type}")
    
    return client_class(config, **kwargs)
