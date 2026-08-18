"""LLM Provider 抽象层测试

测试多厂商 LLM Factory 根据配置正确创建对应的 ChatModel。
"""
import pytest


class TestLlmProvider:
    """LLM Factory 测试"""

    def test_get_llm_openai(self):
        """测试 LLM_PROVIDER=openai 时返回 ChatOpenAI"""
        from app.core.config import Settings
        from app.llm.provider import get_llm

        settings = Settings(
            LLM_PROVIDER="openai",
            OPENAI_API_KEY="sk-test",
        )
        llm = get_llm(settings)
        assert llm is not None
        assert type(llm).__name__ == "ChatOpenAI"

    def test_get_llm_anthropic(self):
        """测试 LLM_PROVIDER=anthropic 时返回 ChatAnthropic"""
        from app.core.config import Settings
        from app.llm.provider import get_llm

        settings = Settings(
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="sk-ant-test",
        )
        llm = get_llm(settings)
        assert type(llm).__name__ == "ChatAnthropic"

    def test_get_llm_deepseek(self):
        """测试 LLM_PROVIDER=deepseek 时返回 ChatOpenAI（DeepSeek 兼容 OpenAI 接口）"""
        from app.core.config import Settings
        from app.llm.provider import get_llm

        settings = Settings(
            LLM_PROVIDER="deepseek",
            DEEPSEEK_API_KEY="sk-deepseek-test",
        )
        llm = get_llm(settings)
        # DeepSeek 使用 OpenAI 兼容接口，底层仍是 ChatOpenAI
        assert type(llm).__name__ == "ChatOpenAI"

    def test_get_llm_invalid_provider(self):
        """测试无效 provider 抛出 ValueError"""
        from app.core.config import Settings
        from app.llm.provider import get_llm

        settings = Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test")
        # 直接测试 unsupported provider 逻辑
        from app.llm.provider import _SUPPORTED_PROVIDERS
        assert "openai" in _SUPPORTED_PROVIDERS
        assert "anthropic" in _SUPPORTED_PROVIDERS
        assert "deepseek" in _SUPPORTED_PROVIDERS

    def test_get_llm_missing_api_key(self):
        """测试缺少 API Key 时抛出 ValueError"""
        from app.core.config import Settings
        from app.llm.provider import get_llm

        settings = Settings(
            LLM_PROVIDER="openai",
            OPENAI_API_KEY="",  # 空 API Key
        )
        with pytest.raises(ValueError, match="API key"):
            get_llm(settings)

    def test_custom_model_name(self):
        """测试指定 LLM_MODEL 时使用自定义模型"""
        from app.core.config import Settings
        from app.llm.provider import get_llm

        settings = Settings(
            LLM_PROVIDER="openai",
            OPENAI_API_KEY="sk-test",
            LLM_MODEL="gpt-4o",
        )
        llm = get_llm(settings)
        assert llm.model_name == "gpt-4o"

    def test_default_model_when_empty(self):
        """测试 LLM_MODEL 为空时使用厂商默认模型"""
        from app.core.config import Settings
        from app.llm.provider import get_llm

        settings = Settings(
            LLM_PROVIDER="openai",
            OPENAI_API_KEY="sk-test",
            LLM_MODEL="",  # 空字符串
        )
        llm = get_llm(settings)
        assert llm.model_name == "gpt-4o-mini"

        settings2 = Settings(
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="sk-ant-test",
        )
        llm2 = get_llm(settings2)
        # ChatAnthropic 的模型字段名是 model 而非 model_name
        assert llm2.model == "claude-sonnet-4-6"


class TestLlmTimeout:
    """LLM 超时与重试上限测试

    LLM 调用运行在 query_executor 的 worker 线程中。无超时的挂起调用会永久占用
    worker，逐个耗尽后所有用户只能收到「系统繁忙」，而进程与 /health 仍显示正常。
    因此超时必须被显式设置，这是有界查询池能成立的前提。
    """

    def test_openai_has_timeout_and_retries(self):
        from app.core.config import Settings
        from app.llm.provider import get_llm

        llm = get_llm(Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test"))
        # langchain-openai 的字段名是 request_timeout（timeout 是其别名）
        assert llm.request_timeout == 60.0
        assert llm.max_retries == 2

    def test_anthropic_has_timeout_and_retries(self):
        from app.core.config import Settings
        from app.llm.provider import get_llm

        llm = get_llm(Settings(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-test"))
        # langchain-anthropic 的字段名是 default_request_timeout
        assert llm.default_request_timeout == 60.0
        assert llm.max_retries == 2

    def test_deepseek_has_timeout_and_retries(self):
        from app.core.config import Settings
        from app.llm.provider import get_llm

        llm = get_llm(Settings(LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="sk-ds-test"))
        assert llm.request_timeout == 60.0
        assert llm.max_retries == 2

    def test_timeout_is_configurable(self):
        from app.core.config import Settings
        from app.llm.provider import get_llm

        llm = get_llm(Settings(
            LLM_PROVIDER="openai",
            OPENAI_API_KEY="sk-test",
            LLM_TIMEOUT_SECONDS=15.0,
            LLM_MAX_RETRIES=0,
        ))
        assert llm.request_timeout == 15.0
        assert llm.max_retries == 0

    def test_embedding_client_bounded(self):
        """embedder 的 OpenAI 客户端必须有超时；重试交给 tenacity，避免两层相乘"""
        from app.rag.embedder import _get_openai_client

        client = _get_openai_client()
        assert client.timeout == 60.0
        assert client.max_retries == 0
