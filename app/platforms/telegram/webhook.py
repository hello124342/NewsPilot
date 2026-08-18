"""Telegram Webhook 事件接收

FastAPI route 接收 Telegram webhook 推送的 Update 对象。
通过 TelegramAdapter 处理 incoming messages/callback queries。
"""

import json
import logging
from collections import OrderedDict
from typing import Optional

from fastapi import APIRouter, Request, Response, HTTPException

from app.core.config import Settings
from app.core.query_executor import submit as query_submit, QuerySubmitStatus
from app.platforms.adapter import MessageCallback, CallbackActionHandler
from app.platforms.message_model import IncomingMessage, CallbackData

logger = logging.getLogger(__name__)

# Telegram webhook router，由 main.py 在 startup 时注册
telegram_router = APIRouter(prefix="/webhook", tags=["telegram"])


# ========== 全局回调处理器 ==========
# 由 main.py 在 startup 时设置，将 Telegram 事件转发给业务逻辑。

_on_message: Optional[MessageCallback] = None
_on_callback: Optional[CallbackActionHandler] = None

# 配置引用（用于 webhook secret 校验）
_settings: Optional[Settings] = None


# ========== 更新去重 ==========
# Telegram 网络重试可能重复推送同一条 update，按 update_id 去重。

_seen_updates: OrderedDict[int, bool] = OrderedDict()
_MAX_DEDUP = 2000


def _dedup_update(update_id) -> bool:
    """检查 update_id 是否已处理过。返回 True 表示重复。"""
    if not update_id:
        return False
    if update_id in _seen_updates:
        return True
    _seen_updates[update_id] = True
    if len(_seen_updates) > _MAX_DEDUP:
        _seen_updates.popitem(last=False)
    return False


def _notify_dropped(chat_id: str, status: QuerySubmitStatus) -> None:
    """查询被丢弃时给用户回一条提示（队列满 / 限流）

    同步发送单条消息（快，不涉及 LLM），确保用户有反馈而非无声消失。
    """
    if not chat_id:
        return
    try:
        from app.platforms.registry import get_platform_adapter
        from app.platforms.message_model import RichMessage
        from app.core.config import Settings as AppSettings

        settings = AppSettings()  # type: ignore[call-arg]
        adapter = get_platform_adapter("telegram", settings)
        if adapter is None:
            return
        body = "🐌 操作太频繁，请稍后再试。" if status is QuerySubmitStatus.RATE_LIMITED else "⛔ 系统繁忙，请稍后再试。"
        adapter.send_message(chat_id, RichMessage(body=body, color_hint="warning"))
    except Exception as e:
        logger.warning(f"Failed to send busy message to {chat_id}: {e}")


def configure_webhook(
    settings: Settings,
    on_message: MessageCallback,
    on_callback: CallbackActionHandler,
) -> None:
    """配置 webhook 回调处理器

    在 main.py startup 时调用，注册消息和按钮回调的处理函数。

    Args:
        settings: 应用配置
        on_message: 收到文本消息时的处理回调
        on_callback: 收到按钮回调时的处理回调
    """
    global _on_message, _on_callback, _settings
    _settings = settings
    _on_message = on_message
    _on_callback = on_callback
    logger.info("Telegram webhook callbacks configured")


# ========== Webhook Endpoint ==========


@telegram_router.post("/telegram")
async def handle_telegram_webhook(request: Request) -> Response:
    """接收 Telegram Bot API 的 webhook 推送

    Telegram 推送的 Update JSON：
      {"update_id": 123,
       "message": {"message_id": 1, "chat": {...}, "text": "/subscribe OpenAI", ...},
       "callback_query": {"id": "...", "from": {...}, "data": "...", ...}}

    安全校验：
      - X-Telegram-Bot-Api-Secret-Token header（如果 TELEGRAM_WEBHOOK_SECRET 已设置）
    """
    # Secret token 校验
    if _settings and _settings.TELEGRAM_WEBHOOK_SECRET:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret != _settings.TELEGRAM_WEBHOOK_SECRET:
            logger.warning("Telegram webhook rejected: invalid secret token")
            raise HTTPException(status_code=403, detail="Forbidden")

    try:
        body = await request.json()
    except json.JSONDecodeError:
        logger.warning("Telegram webhook received invalid JSON")
        return Response(status_code=400, content="Invalid JSON")

    logger.debug(f"Telegram webhook received: {json.dumps(body, ensure_ascii=False)[:500]}")

    # 去重：Telegram 网络重试可能重复推送同一条 update
    if _dedup_update(body.get("update_id")):
        return Response(status_code=200, content="OK")

    # 处理 message（提交到查询池，不阻塞事件循环）
    if "message" in body:
        _handle_message(body["message"])

    # 处理 callback_query
    elif "callback_query" in body:
        _handle_callback(body["callback_query"])

    # 处理 my_chat_member（Bot 被添加/移除/权限变更）
    elif "my_chat_member" in body:
        _handle_my_chat_member(body["my_chat_member"])

    # Telegram 要求返回 200（已入队异步处理，立即返回）
    return Response(status_code=200, content="OK")


def _handle_message(msg: dict) -> None:
    """处理 Telegram 消息事件

    解析后提交到共享查询池（fire-and-forget），不阻塞 webhook 事件循环。
    """
    if _on_message is None:
        logger.warning("Telegram message handler not configured, ignoring message")
        return

    chat = msg.get("chat", {})
    from_user = msg.get("from", {})

    chat_id = str(chat.get("id", ""))
    sender_id = str(from_user.get("id", ""))
    text = msg.get("text", "")

    # 去除 /command@botname 中的 @botname 部分
    if text and "@" in text:
        # 格式：/command@MyBot → /command
        import re
        text = re.sub(r'(@\w+)\s*', '', text, count=1)

    if not chat_id or not text:
        return

    incoming = IncomingMessage(
        platform="telegram",
        chat_id=chat_id,
        sender_id=sender_id,
        text=text.strip(),
        raw_payload=msg,
    )

    status = query_submit(_on_message, incoming, user_id=sender_id)
    if status is not QuerySubmitStatus.ACCEPTED:
        _notify_dropped(chat_id, status)


def _handle_callback(cb: dict) -> None:
    """处理 Telegram 按钮回调

    解析后提交到共享查询池（fire-and-forget），不阻塞 webhook 事件循环。
    """
    if _on_callback is None:
        logger.warning("Telegram callback handler not configured, ignoring callback")
        return

    from_user = cb.get("from", {})
    message = cb.get("message", {})
    chat = message.get("chat", {})

    chat_id = str(chat.get("id", ""))
    sender_id = str(from_user.get("id", ""))
    data = cb.get("data", "")

    if not chat_id or not data:
        return

    callback_data = CallbackData.from_json(data)

    # Telegram 需要 acknowledge callback query，否则按钮一直 loading
    # 这里只记录并转发，ack 由上层或外部处理
    logger.debug(f"Telegram callback: action={callback_data.action}, chat={chat_id}")

    status = query_submit(_on_callback, callback_data, chat_id, sender_id, user_id=sender_id)
    if status is not QuerySubmitStatus.ACCEPTED:
        _notify_dropped(chat_id, status)


def _handle_my_chat_member(update: dict) -> None:
    """处理 Bot 自身在群聊中的成员状态变更

    my_chat_member 事件结构：
      {
        "chat": {"id": -123456, "type": "group", "title": "..."},
        "from": {"id": 789, ...},  // 操作者（拉 bot 入群的人）
        "old_chat_member": {"status": "left", ...},
        "new_chat_member": {"status": "member" | "administrator" | "left" | "kicked", ...},
      }

    解析后提交到共享查询池（fire-and-forget），不阻塞 webhook 事件循环。
    当 Bot 被添加到群聊 → 注册 + 自动订阅 + 欢迎消息
    当 Bot 被移出群聊 → 标记 inactive
    """
    chat = update.get("chat", {})
    chat_id = str(chat.get("id", ""))
    from_user = update.get("from", {})
    inviter_id = str(from_user.get("id", ""))
    old_status = (update.get("old_chat_member") or {}).get("status", "")
    new_status = (update.get("new_chat_member") or {}).get("status", "")

    if not chat_id:
        return

    logger.info(
        f"Telegram my_chat_member: chat_id={chat_id}, "
        f"old={old_status}, new={new_status}, from={inviter_id}"
    )

    status = query_submit(_process_my_chat_member, chat_id, old_status, new_status)
    if status is not QuerySubmitStatus.ACCEPTED:
        logger.warning(f"Telegram my_chat_member dropped for chat {chat_id} (pool busy)")


def _process_my_chat_member(chat_id: str, old_status: str, new_status: str) -> None:
    """Bot 成员状态变更的实际处理（在查询池 worker 中执行）"""
    # Bot 被添加到群聊（从非成员 → 成员/管理员）
    if old_status in ("left", "kicked", "") and new_status in ("member", "administrator"):
        try:
            from app.chat.lifecycle import register_chat, is_new_chat
            from app.subscription.handler import subscribe, ALL_VENDORS

            is_new = is_new_chat(chat_id, platform="telegram")
            register_chat(chat_id, chat_type="group", platform="telegram")

            if is_new:
                for vendor in ALL_VENDORS:
                    subscribe(chat_id, vendor, platform="telegram")
                logger.info(f"Telegram group onboarded: {chat_id}, auto-subscribed all vendors")

            # 发送欢迎消息
            try:
                from app.platforms.registry import get_platform_adapter
                from app.platforms.message_model import RichMessage

                settings = Settings()  # type: ignore[call-arg]
                adapter = get_platform_adapter("telegram", settings)
                if adapter:
                    vendors_str = "、".join(ALL_VENDORS)
                    adapter.send_message(chat_id, RichMessage(
                        title="🤖 已就位",
                        body=(
                            "👋 **我开始追踪 6 家 AI 厂商动态！**\n\n"
                            "每天早上 **9:00** 自动推送最新新闻摘要。\n\n"
                            f"📌 **已默认订阅：** {vendors_str}\n\n"
                            "**群管理命令：**\n"
                            "  /subscribe OpenAI — 只看某个厂商\n"
                            "  /unsubscribe DeepSeek — 取消某个厂商\n"
                            "  /list — 查看当前订阅\n"
                            "  /settings — 修改推送时间或频率\n\n"
                            "**所有成员：**\n"
                            "  直接发消息问我问题，如「GPT-5 什么时候发布？」"
                        ),
                        color_hint="success",
                        footer="💡 仅群管理员可修改订阅和推送设置。",
                    ))
                    logger.info(f"Telegram group welcome sent: {chat_id}")
            except Exception as e:
                logger.error(f"Failed to send Telegram group welcome: {e}")

        except Exception as e:
            logger.error(f"Telegram group onboarding failed: {e}", exc_info=True)

    # Bot 被移出群聊
    elif new_status in ("left", "kicked"):
        try:
            from app.chat.lifecycle import deactivate_chat
            deactivate_chat(chat_id, platform="telegram")
            logger.info(f"Telegram bot removed from chat: {chat_id}")
        except Exception as e:
            logger.error(f"Telegram chat deactivation failed: {e}", exc_info=True)
