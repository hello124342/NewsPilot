"""应用核心配置模块

通过 pydantic-settings 统一管理所有环境变量与运行参数。
配置项包括飞书凭证、LLM 多厂商、数据库连接等。
"""
from typing import Literal
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置，自动从 .env 文件和环境变量加载"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # ========== 飞书应用配置 ==========
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_CHAT_IDS: str = ""

    # ========== Telegram Bot 配置 ==========
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_WEBHOOK_PATH: str = "/webhook/telegram"
    TELEGRAM_WEBHOOK_SECRET: str = ""  # 可选，webhook 安全校验

    # ========== LLM 多厂商配置 ==========
    LLM_PROVIDER: Literal["openai", "anthropic", "deepseek"] = "openai"
    LLM_MODEL: str = ""  # 空字符串表示使用厂商默认模型
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""

    # ========== MySQL 配置 ==========
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "lark_news"

    # ========== 可观测性配置 ==========
    LOG_LEVEL: str = "INFO"  # DEBUG | INFO | WARNING | ERROR

    # ========== Redis 配置 ==========
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379

    # ========== 计算属性 ==========

    @property
    def chat_ids(self) -> list[str]:
        """将逗号分隔的 chat_id 字符串解析为列表"""
        if not self.FEISHU_CHAT_IDS.strip():
            return []
        return [cid.strip() for cid in self.FEISHU_CHAT_IDS.split(",") if cid.strip()]

    @property
    def mysql_url(self) -> str:
        """拼接 MySQL 连接 URL（使用 pymysql 驱动）"""
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )

    @property
    def redis_url(self) -> str:
        """拼接 Redis 连接 URL"""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"

    @property
    def llm_api_key(self) -> str:
        """根据 LLM_PROVIDER 返回对应的 API Key"""
        key_map = {
            "openai": self.OPENAI_API_KEY,
            "anthropic": self.ANTHROPIC_API_KEY,
            "deepseek": self.DEEPSEEK_API_KEY,
        }
        return key_map[self.LLM_PROVIDER]

    @property
    def feishu_configured(self) -> bool:
        """检查飞书凭证是否已配置"""
        return bool(self.FEISHU_APP_ID.strip() and self.FEISHU_APP_SECRET.strip())

    @property
    def telegram_configured(self) -> bool:
        """检查 Telegram 凭证是否已配置"""
        return bool(self.TELEGRAM_BOT_TOKEN.strip())
