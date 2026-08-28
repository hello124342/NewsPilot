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

    # ========== Discord Bot 配置 ==========
    DISCORD_BOT_TOKEN: str = ""
    DISCORD_GUILD_ID: str = ""  # 可选：限定单服务器，便于选默认频道

    # ========== LLM 多厂商配置 ==========
    LLM_PROVIDER: Literal["openai", "anthropic", "deepseek"] = "openai"
    LLM_MODEL: str = ""  # 空字符串表示使用厂商默认模型
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    # 单次 LLM 调用超时（秒）。必须有界：查询池 worker 阻塞在无超时的调用上会永不释放，
    # 耗尽 worker 后所有用户都只能收到「系统繁忙」，而进程和 /health 仍表现正常
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_MAX_RETRIES: int = 2  # SDK 层重试次数（节点层另有自己的重试逻辑）

    # ========== 本地意图分类配置 ==========
    # 规则命中后不会调用模型；未命中时按此开关调用本地 Ollama。
    INTENT_OLLAMA_ENABLED: bool = False
    INTENT_OLLAMA_URL: str = "http://127.0.0.1:11434"
    INTENT_OLLAMA_MODEL: str = "newpilot-intent"
    INTENT_CONFIDENCE_THRESHOLD: float = 0.75
    INTENT_OLLAMA_TIMEOUT_SECONDS: float = 5.0

    # ========== 管理接口配置 ==========
    # /admin/* 端点的访问令牌。留空则管理端点整体禁用（fail-closed），
    # 因为这些端点可触发群发消息和批量 embedding 计费
    ADMIN_API_TOKEN: str = ""

    # ========== MySQL 配置 ==========
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "lark_news"

    # ========== 并发查询池配置 ==========
    QUERY_MAX_WORKERS: int = 10  # 查询池 worker 数（LLM rate limit 是实际瓶颈）
    QUERY_MAX_QUEUE: int = 50    # 有界队列容量，打满后丢弃新请求
    QUERY_QUEUE_TIMEOUT_SECONDS: float = 0.5  # 等待队列空位的超时
    QUERY_RATE_LIMIT_SECONDS: float = 2.0     # 每用户查询最小间隔（0=关闭限流；令牌桶模式下仅作总开关）
    QUERY_RATE_BURST: int = 3                  # 令牌桶容量：允许用户短时突发 N 条查询
    QUERY_RATE_REFILL: float = 0.5            # 令牌桶回填速率：每秒回填 N 个令牌（0.5=每 2 秒 1 个）

    # 查询执行器模式：thread（同步线程池，默认稳定路径）| async（asyncio 协程池）
    QUERY_EXECUTOR_MODE: str = "thread"
    QUERY_MAX_CONCURRENCY: int = 100          # async 模式并发上限（asyncio.Semaphore）
    QUERY_TASK_TIMEOUT_SECONDS: float = 120.0  # async 模式单任务超时（防协程泄漏）

    # ========== 多级缓存配置 ==========
    CACHE_L1_MAXSIZE: int = 2000        # L1 进程内缓存容量（LRU 淘汰）
    CACHE_LLM_TTL: float = 3600.0       # LLM 结果缓存 TTL（秒），1h
    CACHE_EMBED_TTL: float = 86400.0    # Embedding 缓存 TTL（秒），24h（确定性计算）
    CACHE_DB_TTL: float = 300.0         # DB 热点读缓存 TTL（秒），5min

    # ========== Redis Stream 推送队列配置 ==========
    DELIVER_QUEUE_ENABLED: bool = True        # 是否启用 Stream 队列投递（False=回退内联同步发送）
    DELIVER_CONSUMERS: int = 4                # 消费者线程数
    DELIVER_MAX_RETRY: int = 3                # 单条消息最大重试次数，超限进死信队列
    DELIVER_STREAM_MAXLEN: int = 10000        # Stream 最大长度（近似裁剪，防无界）
    DELIVER_CLAIM_IDLE_MS: int = 60000        # 消息空闲多久（ms）后被 XAUTOCLAIM 重投
    DELIVER_BLOCK_MS: int = 5000              # XREADGROUP 阻塞等待时长（ms）
    DELIVER_DEDUP_TTL: int = 86400            # 幂等去重锁 TTL（秒），防重投导致重复发送

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

    @property
    def discord_configured(self) -> bool:
        """检查 Discord 凭证是否已配置"""
        return bool(self.DISCORD_BOT_TOKEN.strip())

    @property
    def admin_configured(self) -> bool:
        """检查管理接口令牌是否已配置（未配置时 /admin/* 全部禁用）"""
        return bool(self.ADMIN_API_TOKEN.strip())
