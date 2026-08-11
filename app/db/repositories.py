"""数据访问接口（Repository Pattern）

定义订阅管理和 Chat 注册表的数据访问抽象。
遵循依赖倒置原则（DIP）：业务逻辑依赖抽象接口，而非具体 ORM 实现。

使用方式：
    from app.db.repositories import SubscriptionRepository, ChatRegistryRepository
    # 业务代码依赖这些 ABC，测试时注入 mock 实现
"""
from abc import ABC, abstractmethod
from typing import Literal

from sqlalchemy.orm import Session


class SubscriptionRepository(ABC):
    """订阅数据访问接口

    封装厂商订阅和推送偏好的 CRUD 操作。
    所有方法接受可选 db 参数以支持 session 复用，
    以及可选的 platform 参数用于多平台支持（默认 "feishu" 保持向后兼容）。
    """

    # ---- 订阅管理 ----

    @abstractmethod
    def subscribe(self, chat_id: str, vendor: str, db: Session | None = None,
                  platform: str = "feishu") -> str:
        """订阅指定厂商，返回用户可读的确认消息"""
        ...

    @abstractmethod
    def unsubscribe(self, chat_id: str, vendor: str, db: Session | None = None,
                    platform: str = "feishu") -> str:
        """退订指定厂商，返回用户可读的确认消息"""
        ...

    @abstractmethod
    def list_active(self, chat_id: str, db: Session | None = None,
                    platform: str = "feishu") -> list[str]:
        """查询当前活跃订阅的厂商名列表"""
        ...

    @abstractmethod
    def get_subscribers(self, vendor: str, platform: str = "feishu") -> list[str]:
        """查询订阅了指定厂商的所有 chat_id（用于推送过滤）"""
        ...

    @abstractmethod
    def has_any(self, chat_id: str, platform: str = "feishu") -> bool:
        """检查 chat_id 是否有任何订阅记录"""
        ...

    # ---- 推送偏好 ----

    @abstractmethod
    def get_preference(self, chat_id: str, db: Session | None = None,
                       platform: str = "feishu") -> dict:
        """获取 chat 的推送偏好，无记录时返回默认值"""
        ...

    @abstractmethod
    def set_push_time(self, chat_id: str, push_time: str, db: Session | None = None,
                      platform: str = "feishu") -> dict:
        """设置推送时间，返回更新后的偏好"""
        ...

    @abstractmethod
    def set_frequency(self, chat_id: str, frequency: str, db: Session | None = None,
                      platform: str = "feishu") -> dict:
        """设置推送频率，返回更新后的偏好"""
        ...

    # ---- 命令检测（纯逻辑，无 DB 依赖，但放在接口中便于统一 mock） ----

    @abstractmethod
    def get_all_vendors(self) -> list[str]:
        """返回所有支持的厂商名列表"""
        ...

    @abstractmethod
    def detect_command(
        self, text: str
    ) -> tuple[Literal["subscribe", "unsubscribe", "list", "settings", "set_time", "set_freq"], str | None] | None:
        """检测用户消息是否为订阅命令"""
        ...

    @abstractmethod
    def is_today_in_frequency(self, frequency: str) -> bool:
        """判断今天是否在指定频率范围内"""
        ...


class ChatRegistryRepository(ABC):
    """Chat 注册表数据访问接口

    封装多平台 chat 生命周期的 CRUD 操作，作为推送目标的唯一数据源。
    platform 参数默认 "feishu" 保持向后兼容。
    """

    @abstractmethod
    def register(self, chat_id: str, chat_type: Literal["group", "user"] = "group",
                 platform: str = "feishu") -> bool:
        """注册或重新激活 chat。返回 True 表示首次注册。"""
        ...

    @abstractmethod
    def deactivate(self, chat_id: str, platform: str = "feishu") -> None:
        """标记 chat 为 inactive（Bot 被移出群）"""
        ...

    @abstractmethod
    def is_new(self, chat_id: str, platform: str = "feishu") -> bool:
        """检查是否为首次接触的 chat"""
        ...

    @abstractmethod
    def get_active_chats(self) -> list[dict]:
        """获取所有活跃 chat 列表（含 platform 字段）"""
        ...

    @abstractmethod
    def get_active_chat_ids(self) -> list[str]:
        """获取所有活跃 chat_id 列表"""
        ...

    @abstractmethod
    def get_type(self, chat_id: str, db: Session | None = None,
                 platform: str = "feishu") -> str | None:
        """查询 chat 类型（"group" / "user" / None）"""
        ...

    @abstractmethod
    def get_owner_id(self, chat_id: str, db: Session | None = None,
                     platform: str = "feishu") -> str | None:
        """获取缓存的群主/管理员 ID"""
        ...

    @abstractmethod
    def set_owner_id(self, chat_id: str, owner_id: str,
                     platform: str = "feishu") -> None:
        """缓存群主/管理员 ID"""
        ...

    @abstractmethod
    def can_manage_subscription(
        self, chat_id: str, sender_id: str,
        db: Session | None = None,
        feishu_client=None,
        platform: str = "feishu",
        platform_adapter=None,
    ) -> bool:
        """检查操作者是否有权限管理订阅（群主/管理员 或 私聊用户）"""
        ...
