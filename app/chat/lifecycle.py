"""Chat 生命周期管理（Facade + Service）

CRUD 操作委托给 ChatRegistryRepository 接口实现。
can_manage_subscription 是 service 层逻辑（DB + 外部 API 协调），直接实现。

多平台支持：platform 参数默认 "feishu" 保持向后兼容。
"""

import logging
from typing import Literal

from sqlalchemy.orm import Session

from app.db.sql_repositories import get_chat_repo

logger = logging.getLogger(__name__)


# ========== Facade：CRUD 委托给 Repository ==========

def register_chat(chat_id: str, chat_type: Literal["group", "user"] = "group",
                  platform: str = "feishu") -> bool:
    return get_chat_repo().register(chat_id, chat_type, platform=platform)


def deactivate_chat(chat_id: str, platform: str = "feishu") -> None:
    get_chat_repo().deactivate(chat_id, platform=platform)


def is_new_chat(chat_id: str, platform: str = "feishu") -> bool:
    return get_chat_repo().is_new(chat_id, platform=platform)


def get_active_chats() -> list[dict]:
    return get_chat_repo().get_active_chats()


def get_active_chat_ids() -> list[str]:
    return get_chat_repo().get_active_chat_ids()


def get_chat_type(chat_id: str, db: Session | None = None,
                  platform: str = "feishu") -> str | None:
    return get_chat_repo().get_type(chat_id, db, platform=platform)


def get_owner_id(chat_id: str, db: Session | None = None,
                 platform: str = "feishu") -> str | None:
    return get_chat_repo().get_owner_id(chat_id, db, platform=platform)


def set_owner_id(chat_id: str, owner_id: str, platform: str = "feishu") -> None:
    get_chat_repo().set_owner_id(chat_id, owner_id, platform=platform)


# ========== Service 层：权限判断（DB + 外部 API 协调）==========

def can_manage_subscription(
    chat_id: str, sender_id: str,
    db: Session | None = None,
    platform: str = "feishu",
    platform_adapter=None,
) -> bool:
    """检查操作者是否有权限管理订阅（多平台）

    - 私聊：sender 即为 chat 所有者 → True
    - 群聊：sender == owner_id（飞书）或 sender is admin（Telegram）→ True
    - 群聊且无法确认 → True（fail open）

    平台适配：
      - 飞书：通过 FeishuClient.get_chat_info() 查询群主 open_id
      - Telegram：通过 platform_adapter.is_admin() 检查管理员身份

    Args:
        chat_id: 平台原生的会话 ID
        sender_id: 操作者 ID
        db: 可选的 SQLAlchemy session
        platform: 平台标识 "feishu" / "telegram"
        platform_adapter: 平台适配器实例（用于 Telegram 权限查询等）
    """
    chat_type = get_chat_type(chat_id, db=db, platform=platform)

    if chat_type == "user":
        return True
    if chat_type is None:
        return True

    # Telegram: 使用 platform_adapter.is_admin()
    if platform == "telegram" and platform_adapter:
        try:
            return platform_adapter.is_admin(chat_id, sender_id)
        except Exception:
            return True  # fail open

    # 飞书: 缓存 owner_id 对比 + API fallback
    owner_id = get_owner_id(chat_id, db=db, platform=platform)
    if not owner_id:
        from app.feishu.client import FeishuClient
        from app.core.config import Settings
        try:
            settings = Settings()  # type: ignore[call-arg]
            feishu = FeishuClient(settings)
            info = feishu.get_chat_info(chat_id)
            if info and info.get("owner_id"):
                set_owner_id(chat_id, info["owner_id"], platform=platform)
                return sender_id == info["owner_id"]
        except Exception:
            pass
        return True  # fail open

    return sender_id == owner_id
