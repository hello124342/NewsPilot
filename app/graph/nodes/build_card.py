"""BuildCardNode: 飞书卡片构造节点

从 PushState 读取文章元数据，调用 build_news_card() 生成 Interactive Card JSON。
"""
import logging
from app.graph.state import PushState
from app.feishu.card_builder import build_news_card

logger = logging.getLogger(__name__)


def build_card_node(state: PushState) -> PushState:
    """构造飞书 Interactive Card 并写入 state

    读取 title, vendor, summary_points, raw_url, published_at, channel，
    生成 card_json 写入 state。
    """
    if state.get("status") == "FAILED":
        logger.warning("build_card_node skipped: upstream status is FAILED")
        return state

    try:
        card = build_news_card(
            title=state.get("title", ""),
            vendor=state.get("vendor", ""),
            summary_points=state.get("summary_points", []),
            raw_url=state.get("raw_url", ""),
            published_at=state.get("published_at", ""),
            channel=state.get("channel", "Blog"),
        )
        state["card_json"] = card
        logger.info(f"Card built for: {state.get('title', 'Unknown')[:50]}")
    except Exception as e:
        logger.error(f"build_card_node failed: {e}")
        state["status"] = "FAILED"

    return state
