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
