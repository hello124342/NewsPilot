"""Chat 生命周期管理（Facade + Service）

CRUD 操作委托给 ChatRegistryRepository 接口实现。
can_manage_subscription 是 service 层逻辑（DB + 外部 API 协调），直接实现。
"""
import logging
from typing import Literal

from sqlalchemy.orm import Session

from app.db.sql_repositories import get_chat_repo

logger = logging.getLogger(__name__)


# ========== Facade：CRUD 委托给 Repository ==========

def register_chat(chat_id: str, chat_type: Literal["group", "user"] = "group") -> bool:
    return get_chat_repo().register(chat_id, chat_type)


def deactivate_chat(chat_id: str) -> None:
    get_chat_repo().deactivate(chat_id)


def is_new_chat(chat_id: str) -> bool:
    return get_chat_repo().is_new(chat_id)


def get_active_chats() -> list[dict]:
    return get_chat_repo().get_active_chats()


def get_active_chat_ids() -> list[str]:
    return get_chat_repo().get_active_chat_ids()


def get_chat_type(chat_id: str, db: Session | None = None) -> str | None:
    return get_chat_repo().get_type(chat_id, db)


def get_owner_id(chat_id: str, db: Session | None = None) -> str | None:
    return get_chat_repo().get_owner_id(chat_id, db)


def set_owner_id(chat_id: str, owner_id: str) -> None:
    get_chat_repo().set_owner_id(chat_id, owner_id)


# ========== Service 层：权限判断（DB + 外部 API 协调）==========

def can_manage_subscription(
    chat_id: str, sender_id: str, db: Session | None = None
) -> bool:
    """检查操作者是否有权限管理订阅

    - 私聊：sender 即为 chat 所有者 → True
    - 群聊：sender == owner_id → True
    - 群聊且无法确认 owner（API 失败等）→ True（fail open）

    此函数是 Service 层逻辑，协调 Repository（DB 查询）和 Feishu API。
    """
    chat_type = get_chat_type(chat_id, db=db)

    if chat_type == "user":
        return True
    if chat_type is None:
        return True

    owner_id = get_owner_id(chat_id, db=db)
    if not owner_id:
        # 没有缓存的 owner_id，尝试从飞书 API 获取
        from app.feishu.client import FeishuClient
        from app.core.config import Settings
        try:
            settings = Settings()  # type: ignore[call-arg]
            feishu = FeishuClient(settings)
            info = feishu.get_chat_info(chat_id)
            if info and info.get("owner_id"):
                set_owner_id(chat_id, info["owner_id"])
                return sender_id == info["owner_id"]
        except Exception:
            pass
        return True  # fail open

    return sender_id == owner_id
