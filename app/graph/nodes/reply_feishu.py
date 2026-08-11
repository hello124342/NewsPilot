"""ReplyFeishuNode: 多平台回复发送节点

将格式化后的消息发送到用户所在的对话。
支持飞书（FeishuAdapter）和 Telegram（TelegramAdapter）等平台。
"""
import logging
from app.graph.state import QueryState
from app.core.config import Settings

logger = logging.getLogger(__name__)


def reply_feishu_node(state: QueryState) -> QueryState:
    """向触发查询的群聊/用户发送回复

    平台感知：根据 state["platform"] 选择对应的适配器发送。
    - "feishu": 使用 FeishuAdapter 发送 Interactive Card
    - "telegram": 使用 TelegramAdapter 发送 Markdown + InlineKeyboard
    - 未指定时默认使用飞书（向后兼容）

    Returns:
        更新后的 QueryState（无额外字段）
    """
    chat_id = state.get("chat_id", "")
    platform = state.get("platform", "feishu")

    if not chat_id:
        logger.error("reply_feishu_node: chat_id is empty, cannot send reply")
        return state

    try:
        settings = Settings()  # type: ignore[call-arg]

        # 平台感知发送
        if platform == "telegram" and _has_rich_message(state):
            _send_via_telegram(settings, chat_id, state)
        elif _has_rich_message(state):
            _send_via_adapter(settings, platform, chat_id, state)
        else:
            _send_legacy_feishu(settings, chat_id, state)

    except Exception as e:
        logger.error(f"reply_feishu_node failed for chat_id={chat_id}, platform={platform}: {e}")

    return state


def _has_rich_message(state: QueryState) -> bool:
    """检查 state 中是否有 RichMessage"""
    rm = state.get("rich_message", {})
    return bool(rm and (rm.get("body") or rm.get("title")))


def _send_via_adapter(
    settings: Settings,
    platform: str,
    chat_id: str,
    state: QueryState,
) -> None:
    """通过平台适配器发送 RichMessage"""
    from app.platforms.registry import get_platform_adapter
    from app.platforms.message_model import RichMessage

    adapter = get_platform_adapter(platform, settings)
    if adapter is None:
        logger.warning(f"Platform '{platform}' adapter not available, falling back to feishu")
        _send_legacy_feishu(settings, chat_id, state)
        return

    rm_dict = state.get("rich_message", {})
    msg = RichMessage(
        title=rm_dict.get("title"),
        body=rm_dict.get("body", ""),
        buttons=[],
        color_hint=rm_dict.get("color_hint"),
        footer=rm_dict.get("footer"),
    )

    # 恢复按钮
    for b in rm_dict.get("buttons", []):
        from app.platforms.message_model import ActionButton
        msg.buttons.append(ActionButton(
            label=b.get("label", ""),
            action=b.get("action", "callback"),
            value=b.get("value", ""),
            style=b.get("style", "default"),
        ))

    result = adapter.send_message(chat_id, msg)
    logger.info(
        f"Reply sent via {platform} adapter to {chat_id}: "
        f"message_id={result.get('message_id')}"
    )


def _send_via_telegram(
    settings: Settings,
    chat_id: str,
    state: QueryState,
) -> None:
    """通过 Telegram 适配器发送"""
    from app.platforms.registry import get_platform_adapter
    from app.platforms.message_model import RichMessage, ActionButton

    adapter = get_platform_adapter("telegram", settings)
    if adapter is None:
        logger.warning("Telegram adapter not available, cannot send reply")
        return

    rm_dict = state.get("rich_message", {})
    msg = RichMessage(
        title=rm_dict.get("title"),
        body=rm_dict.get("body", ""),
        color_hint=rm_dict.get("color_hint"),
        footer=rm_dict.get("footer"),
    )
    for b in rm_dict.get("buttons", []):
        msg.buttons.append(ActionButton(
            label=b.get("label", ""),
            action=b.get("action", "callback"),
            value=b.get("value", ""),
            style=b.get("style", "default"),
        ))

    adapter.send_message(chat_id, msg)
    logger.info(f"Reply sent via Telegram adapter to {chat_id}")


def _send_legacy_feishu(
    settings: Settings,
    chat_id: str,
    state: QueryState,
) -> None:
    """[过渡期] 原有飞书发送路径（保持向后兼容）"""
    card_json = state.get("reply_card_json", {})
    if not card_json:
        logger.warning("reply_feishu_node: no reply_card_json to send")
        return

    from app.feishu.client import FeishuClient
    feishu = FeishuClient(settings)
    result = feishu.send_card(chat_id, card_json)
    logger.info(f"Reply sent to chat_id={chat_id}: code={result.get('code')}")
