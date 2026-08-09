"""MySQL ORM 模型定义

NewsArticle 表存储已处理的 AI 新闻元数据。
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
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

    # ========== 审计字段 ==========
    is_processed = Column(Boolean, default=True, comment="是否已推送处理")
    created_at = Column(DateTime, default=datetime.utcnow, comment="记录创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        comment="记录更新时间")

    def __repr__(self):
        return f"<NewsArticle(id={self.id}, vendor='{self.vendor}', title='{self.title[:30]}...')>"


class Subscription(Base):
    """用户/群聊订阅 ORM 模型

    记录每个 chat_id 对厂商的订阅状态。
    """

    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String(128), nullable=False, index=True, comment="飞书 chat_id（群聊或私聊）")
    vendor = Column(String(128), nullable=False, comment="订阅的厂商名称")
    is_active = Column(Boolean, default=True, comment="是否激活（True=已订阅，False=已退订）")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        comment="更新时间")

    def __repr__(self):
        return f"<Subscription(chat_id='{self.chat_id}', vendor='{self.vendor}', active={self.is_active})>"


class ChatPreference(Base):
    """用户/群聊推送偏好 ORM 模型

    记录每个 chat_id 的推送时间和频率设置。
    """

    __tablename__ = "chat_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String(128), unique=True, nullable=False, index=True,
                     comment="飞书 chat_id（群聊或私聊）")
    push_time = Column(String(5), default="09:00", nullable=False,
                       comment="推送时间（HH:MM 格式，如 09:00）")
    frequency = Column(String(20), default="daily", nullable=False,
                       comment="推送频率：daily/weekdays/weekly_monday")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                        comment="更新时间")

    def __repr__(self):
        return f"<ChatPreference(chat_id='{self.chat_id}', time='{self.push_time}', freq='{self.frequency}')>"


class ChatRegistry(Base):
    """Bot 所在 chat 注册表

    记录 Bot 被拉入的群聊和私聊，用于推送目标发现。
    """

    __tablename__ = "chat_registry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String(128), unique=True, nullable=False, index=True,
                     comment="飞书 chat_id（群聊或私聊）")
    chat_type = Column(String(16), default="group", nullable=False,
                       comment="chat 类型：group / user")
    owner_id = Column(String(128), nullable=True,
                      comment="群主 open_id（仅 group 类型）")
    is_active = Column(Boolean, default=True, nullable=False,
                       comment="Bot 是否仍在该 chat 中")
    first_seen_at = Column(DateTime, default=datetime.utcnow, comment="首次发现时间")
    last_active_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                            comment="最后活跃时间")

    def __repr__(self):
        return f"<ChatRegistry(chat_id='{self.chat_id}', type='{self.chat_type}', active={self.is_active})>"
