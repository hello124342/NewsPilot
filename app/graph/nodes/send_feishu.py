"""SendFeishuNode: 飞书卡片发送节点

从 PushState 读取 card_json，根据用户订阅偏好推送到目标群聊。
成功后标记 URL 已处理。
"""
import logging
from app.graph.state import PushState
from app.feishu.client import FeishuClient
from app.db.redis import RedisClient
from app.core.config import Settings
from app.subscription.handler import get_subscribers, has_any_subscription

logger = logging.getLogger(__name__)


def _resolve_targets(vendor: str) -> list[str]:
    """从 chat_registry 查询活跃 chat 并按订阅偏好过滤

    逻辑：
    - 从 chat_registry 获取所有活跃 chat_id
    - chat_id 没有任何订阅记录 → 默认接收全部（群默认订阅的场景）
    - chat_id 有订阅记录且 vendor 在订阅列表中 → 发送
    - chat_id 有订阅记录但 vendor 不在列表中 → 跳过

    Args:
        vendor: 当前新闻所属厂商

    Returns:
        实际应该推送的 chat_id 列表
    """
    from app.chat.lifecycle import get_active_chat_ids

    active_chats = get_active_chat_ids()
    if not active_chats:
        return []

    subscribers = set(get_subscribers(vendor))
    targets = []

    for cid in active_chats:
        if has_any_subscription(cid):
            if cid in subscribers:
                targets.append(cid)
        else:
            # 无订阅记录 → 默认接收（兼容老群 / 群默认订阅）
            targets.append(cid)

    skipped = len(active_chats) - len(targets)
    if skipped > 0:
        logger.info(f"Subscription filter: {skipped}/{len(active_chats)} chats skipped for {vendor}")
    return targets


def _resolve_targets_legacy(vendor: str, configured_chats: list[str]) -> list[str]:
    """向后兼容：同时支持 FEISHU_CHAT_IDS + chat_registry"""
    from app.chat.lifecycle import get_active_chat_ids

    # 合并 env 配置和自动发现的 chat
    all_chats = list(set(configured_chats) | set(get_active_chat_ids()))
    if not all_chats:
        return []

    subscribers = set(get_subscribers(vendor))
    targets = []

    for cid in all_chats:
        if has_any_subscription(cid):
            if cid in subscribers:
                targets.append(cid)
        else:
            targets.append(cid)

    return targets


def send_feishu_node(state: PushState) -> PushState:
    """发送飞书卡片到订阅了该厂商的目标群聊

    Returns:
        更新后的 PushState（status 设为 SUCCESS 或 FAILED）
    """
    if state.get("status") == "FAILED":
        logger.warning("send_feishu_node skipped: upstream status is FAILED")
        return state

    card_json = state.get("card_json")
    if not card_json:
        logger.error("send_feishu_node: card_json is empty")
        state["status"] = "FAILED"
        return state

    try:
        settings = Settings()  # type: ignore[call-arg]
        vendor = state.get("vendor", "")

        # 按订阅偏好过滤推送目标（从 chat_registry 自动发现）
        targets = _resolve_targets(vendor)

        if not targets:
            logger.info(f"No subscribed targets for {vendor}, skipping push")
            # 即使无人订阅也标记 URL 已处理，避免重复抓取+LLM 总结
            raw_url = state.get("raw_url", "")
            if raw_url:
                redis = RedisClient(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
                redis.mark_url_processed(raw_url)
            state["status"] = "SUCCESS"
            return state

        # 向过滤后的目标发送
        feishu = FeishuClient(settings)
        for cid in targets:
            feishu.send_card(cid, card_json)
        logger.info(f"Card sent to {len(targets)} target(s): {state.get('title', 'Unknown')[:50]}")

        # 推送成功后标记 URL 已处理
        raw_url = state.get("raw_url", "")
        if raw_url:
            redis = RedisClient(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
            redis.mark_url_processed(raw_url)

        state["status"] = "SUCCESS"
    except Exception as e:
        logger.error(f"send_feishu_node failed: {e}")
        state["status"] = "FAILED"

    return state
