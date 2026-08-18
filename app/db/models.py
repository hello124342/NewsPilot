"""MySQL ORM 模型定义

NewsArticle 表存储已处理的 AI 新闻元数据。
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Index
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


class NewsArticle(Base):
    """AI 新闻文章 ORM 模型"""

    __tablename__ = "news_articles"

    # ========== 主键与标识 ==========
    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String(2048), nullable=False, comment="文章原始链接")
    url_hash = Column(String(64), unique=True, nullable=False, comment="文章 URL 的 SHA-256 哈希")

    # ========== 核心字段 ==========
    title = Column(String(512), nullable=False, comment="新闻标题")
    vendor = Column(String(128), nullable=False, index=True, comment="来源厂商")
    published_at = Column(DateTime, nullable=True, comment="文章发布时间")
    summary_points = Column(Text, nullable=True, comment="LLM 总结要点（换行分隔）")
    raw_content = Column(Text, nullable=True, comment="文章原始全文（供 RAG 检索和引用）")

    # ========== 审计字段 ==========
    is_processed = Column(Boolean, default=True, comment="是否已推送处理")
    created_at = Column(DateTime, default=datetime.utcnow, comment="记录创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        comment="记录更新时间")

    # deliver_job 与 search_db 都按 published_at 过滤+倒序；(vendor, published_at)
    # 复合索引同时覆盖「按厂商查最近文章」和单独按厂商查询（最左前缀）
    __table_args__ = (
        Index("ix_news_articles_published_at", "published_at"),
        Index("ix_news_articles_vendor_published", "vendor", "published_at"),
    )

    def __repr__(self):
        return f"<NewsArticle(id={self.id}, vendor='{self.vendor}', title='{self.title[:30]}...')>"


class Subscription(Base):
    """用户/群聊订阅 ORM 模型

    记录每个 chat_id 对厂商的订阅状态。
    """

    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), default="feishu", nullable=False,
                      comment="平台标识：feishu / telegram")
    conversation_id = Column(String(128), nullable=False, comment="平台原生的会话ID")
    chat_id = Column(String(128), nullable=True, index=True,
                     comment="[过渡期兼容] 飞书 chat_id，等同于 conversation_id")
    vendor = Column(String(128), nullable=False, comment="订阅的厂商名称")
    is_active = Column(Boolean, default=True, comment="是否激活（True=已订阅，False=已退订）")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        comment="更新时间")

    # (platform, conversation_id) 是多平台改造后的主查询键；复合索引的最左前缀
    # 同时覆盖单独按 platform 查询，因此 platform 列不再单独建索引
    __table_args__ = (
        Index("ix_subscriptions_platform_conv", "platform", "conversation_id"),
    )

    def __repr__(self):
        return f"<Subscription(platform='{self.platform}', conv_id='{self.conversation_id}', vendor='{self.vendor}', active={self.is_active})>"


class ChatPreference(Base):
    """用户/群聊推送偏好 ORM 模型

    记录每个 chat_id 的推送时间和频率设置。
    """

    __tablename__ = "chat_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), default="feishu", nullable=False,
                      comment="平台标识：feishu / telegram")
    conversation_id = Column(String(128), nullable=False, comment="平台原生的会话ID")
    chat_id = Column(String(128), nullable=True, index=True,
                     comment="[过渡期兼容] 飞书 chat_id，等同于 conversation_id")
    push_time = Column(String(5), default="09:00", nullable=False,
                       comment="推送时间（HH:MM 格式，如 09:00）")
    frequency = Column(String(20), default="daily", nullable=False,
                       comment="推送频率：daily/weekdays/weekly_monday")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        comment="更新时间")

    __table_args__ = (
        Index("ix_chat_preferences_platform_conv", "platform", "conversation_id"),
    )

    def __repr__(self):
        return f"<ChatPreference(platform='{self.platform}', conv_id='{self.conversation_id}', time='{self.push_time}', freq='{self.frequency}')>"


class ChatRegistry(Base):
    """Bot 所在 chat 注册表

    记录 Bot 被拉入的群聊和私聊，用于推送目标发现。
    """

    __tablename__ = "chat_registry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), default="feishu", nullable=False,
                      comment="平台标识：feishu / telegram")
    conversation_id = Column(String(128), nullable=False, comment="平台原生的会话ID")
    chat_id = Column(String(128), nullable=True, index=True,
                     comment="[过渡期兼容] 飞书 chat_id，等同于 conversation_id")
    chat_type = Column(String(16), default="group", nullable=False,
                       comment="chat 类型：group / user")
    owner_id = Column(String(128), nullable=True,
                      comment="群主/管理员 ID（仅 group 类型）")
    is_active = Column(Boolean, default=True, nullable=False,
                       comment="Bot 是否仍在该 chat 中")
    first_seen_at = Column(DateTime, default=datetime.utcnow, comment="首次发现时间")
    last_active_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                            comment="最后活跃时间")

    # 推送时按 (platform, is_active) 扫描全部活跃会话，按 (platform, conversation_id) 查单个
    __table_args__ = (
        Index("ix_chat_registry_platform_conv", "platform", "conversation_id"),
        Index("ix_chat_registry_platform_active", "platform", "is_active"),
    )

    def __repr__(self):
        return f"<ChatRegistry(platform='{self.platform}', conv_id='{self.conversation_id}', type='{self.chat_type}', active={self.is_active})>"
