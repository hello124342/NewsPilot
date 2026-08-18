"""LLM Provider Factory

根据 Settings 配置返回对应厂商的 LangChain BaseChatModel 实例。
支持 OpenAI、Anthropic Claude、DeepSeek（OpenAI 兼容接口）。

所有实例均设置 timeout / max_retries：LLM 调用运行在 query_executor 的 worker
线程中，无超时的挂起调用会永久占用 worker，最终耗尽整个有界查询池。
"""
import logging
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from app.core.config import Settings

logger = logging.getLogger(__name__)

# 支持的厂商及对应的 API Key 字段名
_SUPPORTED_PROVIDERS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


# 各厂商默认模型
_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
    "deepseek": "deepseek-chat",
}


def get_llm(settings: Settings | None = None) -> BaseChatModel:
    """根据配置创建 LLM 实例

    Args:
        settings: 应用配置。为 None 时自动创建默认 Settings。

    Returns:
        BaseChatModel 实例（ChatOpenAI / ChatAnthropic）

    Raises:
        ValueError: API Key 为空或 provider 不支持
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    provider = settings.LLM_PROVIDER
    if provider not in _SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported LLM provider: {provider}. "
            f"Supported: {list(_SUPPORTED_PROVIDERS.keys())}"
        )

    api_key = settings.llm_api_key
    if not api_key:
        raise ValueError(
            f"API key for {provider} is empty. "
            f"Please set {_SUPPORTED_PROVIDERS[provider]} environment variable."
        )

    # 优先使用用户指定的模型，未指定则用厂商默认
    model = settings.LLM_MODEL or _DEFAULT_MODELS[provider]
    timeout = settings.LLM_TIMEOUT_SECONDS
    max_retries = settings.LLM_MAX_RETRIES
    logger.info(
        f"LLM initialized: provider={provider}, model={model}, "
        f"timeout={timeout}s, max_retries={max_retries}"
    )

    if provider == "openai":
        return ChatOpenAI(
            model=model,
            api_key=api_key,
            temperature=0.5,
            timeout=timeout,
            max_retries=max_retries,
        )

    if provider == "anthropic":
        return ChatAnthropic(
            model=model,
            api_key=api_key,
            temperature=0.5,
            timeout=timeout,
            max_retries=max_retries,
        )

    if provider == "deepseek":
        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
            temperature=0.5,
            timeout=timeout,
            max_retries=max_retries,
        )

    raise ValueError(f"Unhandled provider: {provider}")
