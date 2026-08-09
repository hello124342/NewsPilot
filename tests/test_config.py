"""配置模块单元测试

测试 pydantic-settings 配置类，确保所有环境变量被正确读取和校验。
"""
import os
import pytest
from pydantic import ValidationError


class TestSettings:
    """Settings 配置类测试"""

    def test_default_values(self):
        """测试默认配置值"""
        from app.core.config import Settings

        settings = Settings()
        assert settings.MYSQL_HOST == "127.0.0.1"
        assert settings.MYSQL_PORT == 3306
        assert settings.REDIS_HOST == "127.0.0.1"
        assert settings.REDIS_PORT == 6379
        # LLM_PROVIDER 默认值可能被 .env 覆盖，只校验是有效值即可
        assert settings.LLM_PROVIDER in ("openai", "anthropic", "deepseek")

    def test_chat_ids_parsed_as_list(self):
        """测试 FEISHU_CHAT_IDS 被解析为列表"""
        from app.core.config import Settings

        settings = Settings(FEISHU_CHAT_IDS="oc_xxx,oc_yyy,oc_zzz")
        assert settings.chat_ids == ["oc_xxx", "oc_yyy", "oc_zzz"]

    def test_single_chat_id(self):
        """测试单个 chat_id 也能被解析为列表"""
        from app.core.config import Settings

        settings = Settings(FEISHU_CHAT_IDS="oc_xxx")
        assert settings.chat_ids == ["oc_xxx"]

    def test_empty_chat_ids(self):
        """测试空字符串返回空列表"""
        from app.core.config import Settings

        settings = Settings(FEISHU_CHAT_IDS="")
        assert settings.chat_ids == []

    def test_feishu_credentials_required(self):
        """测试 feishu_configured 在凭证未配置时返回 False"""
        from app.core.config import Settings

        settings = Settings(FEISHU_APP_ID="", FEISHU_APP_SECRET="")
        assert settings.feishu_configured is False

        settings2 = Settings(FEISHU_APP_ID="cli_xxx", FEISHU_APP_SECRET="secret")
        assert settings2.feishu_configured is True

    def test_llm_provider_invalid_value(self):
        """测试 LLM_PROVIDER 非法值抛出校验错误"""
        from app.core.config import Settings
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(LLM_PROVIDER="invalid_provider")

    def test_mysql_database_url(self):
        """测试 MySQL 连接 URL 拼接"""
        from app.core.config import Settings

        settings = Settings(
            MYSQL_HOST="db.example.com",
            MYSQL_PORT=3307,
            MYSQL_USER="admin",
            MYSQL_PASSWORD="secret",
            MYSQL_DATABASE="lark_news",
        )
        expected = "mysql+pymysql://admin:secret@db.example.com:3307/lark_news"
        assert settings.mysql_url == expected

    def test_redis_url(self):
        """测试 Redis 连接 URL 拼接"""
        from app.core.config import Settings

        settings = Settings(REDIS_HOST="redis.example.com", REDIS_PORT=6380)
        assert settings.redis_url == "redis://redis.example.com:6380"

    def test_llm_api_key_selection(self):
        """测试根据 LLM_PROVIDER 获取对应的 API Key"""
        from app.core.config import Settings

        settings = Settings(
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="sk-ant-xxx",
        )
        assert settings.llm_api_key == "sk-ant-xxx"

        settings2 = Settings(
            LLM_PROVIDER="openai",
            OPENAI_API_KEY="sk-openai-xxx",
        )
        assert settings2.llm_api_key == "sk-openai-xxx"

        settings3 = Settings(
            LLM_PROVIDER="deepseek",
            DEEPSEEK_API_KEY="sk-deepseek-xxx",
        )
        assert settings3.llm_api_key == "sk-deepseek-xxx"
