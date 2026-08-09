"""ReplyFeishuNode: 飞书回复发送节点

将 format_response 生成的卡片发送到用户所在的 chat_id。
"""
import logging
from app.graph.state import QueryState
from app.feishu.client import FeishuClient
from app.core.config import Settings

logger = logging.getLogger(__name__)


def reply_feishu_node(state: QueryState) -> QueryState:
    """向触发查询的群聊/用户发送回复卡片

    Returns:
        更新后的 QueryState（无额外字段）
    """
    chat_id = state.get("chat_id", "")
    card_json = state.get("reply_card_json", {})

    if not chat_id:
        logger.error("reply_feishu_node: chat_id is empty, cannot send reply")
        return state

    if not card_json:
        logger.warning("reply_feishu_node: reply_card_json is empty")
        return state

    try:
        settings = Settings()  # type: ignore[call-arg]
        feishu = FeishuClient(settings)
        result = feishu.send_card(chat_id, card_json)
        logger.info(f"Reply sent to chat_id={chat_id}: code={result.get('code')}")
    except Exception as e:
        logger.error(f"reply_feishu_node failed for chat_id={chat_id}: {e}")

    return state
