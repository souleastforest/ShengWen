from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any

from ..utils.logger import logger
from .llm import LLMConfig


@dataclass(frozen=True)
class LLMProvider:
    """预置的 LLM 供应商定义。"""

    id: str
    label: str
    default_base_url: str
    default_model_id: str
    description: str


def _normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def _mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


class LLMProviderManager:
    """运行时 LLM 供应商配置管理器。"""

    def __init__(self, initial_config: LLMConfig, initial_provider_id: str | None = None):
        self._lock = Lock()
        self._runtime_config = initial_config
        self._providers = self._build_providers(initial_config)
        inferred_provider = self._infer_provider_id(initial_config.base_url)
        self._provider_id = (
            initial_provider_id
            if initial_provider_id in self._providers
            else inferred_provider
        )
        # 运行时配置中的 provider 需要与当前选中 provider 保持一致，避免“展示值”和“执行值”分叉。
        self._runtime_config.provider = self._provider_id
        self._llm_worker: Any = None

    def _build_providers(self, initial_config: LLMConfig) -> dict[str, LLMProvider]:
        fallback_base_url = initial_config.base_url or "https://api.openai.com/v1"
        fallback_model_id = initial_config.model_id or "gpt-4o-mini"
        providers = [
            LLMProvider(
                id="openai_compatible",
                label="OpenAI 兼容接口",
                default_base_url=fallback_base_url,
                default_model_id=fallback_model_id,
                description="支持任意 OpenAI 兼容网关/供应商",
            ),
            LLMProvider(
                id="openai",
                label="OpenAI",
                default_base_url="https://api.openai.com/v1",
                default_model_id="gpt-4o-mini",
                description="OpenAI 官方 API",
            ),
            LLMProvider(
                id="openrouter",
                label="OpenRouter",
                default_base_url="https://openrouter.ai/api/v1",
                default_model_id="openai/gpt-4o-mini",
                description="通过 OpenRouter 路由多模型",
            ),
            LLMProvider(
                id="ollama",
                label="Ollama (本地)",
                default_base_url="http://localhost:11434/v1",
                default_model_id="qwen2.5:14b",
                description="本地 Ollama OpenAI 兼容接口",
            ),
            LLMProvider(
                id="deepseek",
                label="DeepSeek",
                default_base_url="https://api.deepseek.com/v1",
                default_model_id="deepseek-chat",
                description="DeepSeek OpenAI 兼容接口",
            ),
            LLMProvider(
                id="anthropic",
                label="Anthropic",
                default_base_url="https://api.anthropic.com",
                default_model_id="claude-sonnet-4-20250514",
                description="Anthropic Claude 原生 SDK",
            ),
        ]
        return {provider.id: provider for provider in providers}

    def _infer_provider_id(self, base_url: str) -> str:
        normalized = _normalize_base_url(base_url)
        # Skip openai_compatible in exact match — it mirrors initial config and would always win.
        for provider in self._providers.values():
            if provider.id == "openai_compatible":
                continue
            if _normalize_base_url(provider.default_base_url) == normalized:
                return provider.id

        if "openrouter.ai" in normalized:
            return "openrouter"
        if "localhost:11434" in normalized:
            return "ollama"
        if "deepseek.com" in normalized:
            return "deepseek"
        if "api.openai.com" in normalized:
            return "openai"
        if "anthropic.com" in normalized:
            return "anthropic"
        return "openai_compatible"

    def bind_llm_worker(self, llm_worker: Any) -> None:
        with self._lock:
            self._llm_worker = llm_worker
            self._apply_config_to_worker_locked()

    def _apply_config_to_worker_locked(self) -> None:
        if self._llm_worker is None:
            return

        llm_client = getattr(self._llm_worker, "_llm_client", None)
        if llm_client is None:
            logger.warning("[LLMProviderManager] 未找到 llm_worker._llm_client，跳过运行时配置应用。")
            return

        if hasattr(llm_client, "update_runtime_config"):
            llm_client.update_runtime_config(self._runtime_config)
        else:
            llm_client.config = self._runtime_config

    def list_providers(self) -> list[dict[str, str]]:
        with self._lock:
            providers = [asdict(provider) for provider in self._providers.values()]
        return providers

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            config = self._runtime_config
            provider_id = self._provider_id

        return {
            "provider": provider_id,
            "base_url": config.base_url,
            "model_id": config.model_id,
            "temperature": config.temperature,
            "context_window_size": config.context_window_size,
            "has_api_key": bool(config.api_key),
            "api_key_hint": _mask_api_key(config.api_key),
            "extra_headers": config.extra_headers or {},
        }

    def get_runtime_config(self) -> LLMConfig:
        with self._lock:
            config = self._runtime_config
            return LLMConfig(
                base_url=config.base_url,
                api_key=config.api_key,
                model_id=config.model_id,
                temperature=config.temperature,
                context_window_size=config.context_window_size,
                provider=config.provider,
                extra_headers=config.extra_headers,
            )

    def export_runtime_config(self) -> dict[str, Any]:
        with self._lock:
            config = self._runtime_config
            provider_id = self._provider_id
            return {
                "provider": provider_id,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "model_id": config.model_id,
                "temperature": config.temperature,
                "context_window_size": config.context_window_size,
                "extra_headers": config.extra_headers or {},
            }

    def update_settings(
        self,
        provider: str,
        base_url: str | None = None,
        api_key: str | None = None,
        model_id: str | None = None,
        temperature: float | None = None,
        context_window_size: int | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            provider_config = self._providers.get(provider)
            if provider_config is None:
                raise ValueError(f"不支持的供应商: {provider}")

            next_base_url = (base_url or "").strip() or provider_config.default_base_url
            next_model_id = (model_id or "").strip() or provider_config.default_model_id
            next_temperature = (
                self._runtime_config.temperature if temperature is None else temperature
            )
            next_context_window_size = (
                self._runtime_config.context_window_size
                if context_window_size is None
                else context_window_size
            )
            next_api_key = (
                self._runtime_config.api_key
                if api_key is None or not api_key.strip()
                else api_key.strip()
            )
            next_extra_headers = (
                self._runtime_config.extra_headers
                if extra_headers is None
                else extra_headers
            )

            self._runtime_config = LLMConfig(
                base_url=next_base_url,
                api_key=next_api_key,
                model_id=next_model_id,
                temperature=next_temperature,
                context_window_size=next_context_window_size,
                provider=provider,
                extra_headers=next_extra_headers,
            )
            self._provider_id = provider
            self._apply_config_to_worker_locked()
            logger.info(
                f"[LLMProviderManager] 已更新 LLM 供应商配置: provider={provider}, base_url={next_base_url}, model_id={next_model_id}"
            )

        return self.get_settings()
