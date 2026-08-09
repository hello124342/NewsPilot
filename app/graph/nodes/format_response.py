"""FormatResponseNode: 查询结果格式化节点

将 MySQL 查询结果格式化为飞书 Interactive Card，
支持多篇文章列表展示和空结果处理。
"""
import logging
from app.graph.state import QueryState

logger = logging.getLogger(__name__)


def _build_result_card(results: list[dict], intent: dict) -> dict:
    """将查询结果构建为飞书卡片（多文章列表）

    Args:
        results: 文章列表，每项含 title, vendor, published_at, url, summary_points
        intent: 查询意图 {"vendor": str|None, "days": int}

    Returns:
        飞书卡片 JSON dict
    """
    vendor_name = intent.get("vendor") or "所有厂商"
    days = intent.get("days") or 3

    # 卡片标题
    header_title = f"🔍 {vendor_name} · 近{days}天"
    if not results:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": header_title},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "😕 **未找到相关新闻**\n\n可尝试扩大搜索范围或稍后再试。"},
                }
            ],
        }

    elements = []
    for i, article in enumerate(results):
        # 文章标题 + 厂商
        title_block = f"**{article['title']}**"
        meta_block = f"🏷️ {article['vendor']} · 📅 {article['published_at']}"

        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": title_block},
        })
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": meta_block},
        })

        # 摘要要点（如果有）
        points = article.get("summary_points", [])
        if points and points[0]:
            points_md = "\n".join(f"  • {p}" for p in points[:3] if p)
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": points_md},
            })

        # 阅读原文按钮
        if article.get("url"):
            elements.append({
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📖 阅读原文"},
                    "type": "default",
                    "url": article["url"],
                }],
            })

        # 文章间分隔线（非最后一篇）
        if i < len(results) - 1:
            elements.append({"tag": "hr"})

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": f"{header_title}  ({len(results)} 篇)"},
        },
        "elements": elements,
    }
    return card


def format_response_node(state: QueryState) -> QueryState:
    """将 query_results 格式化为飞书回复卡片

    写入 reply_card_json 到 state。
    """
    results = state.get("query_results", [])
    intent = state.get("parsed_intent", {})

    try:
        card = _build_result_card(results, intent)
        state["reply_card_json"] = card
        logger.info(f"Response card built: {len(results)} articles")
    except Exception as e:
        logger.error(f"format_response_node failed: {e}")
        # 始终构建一张兜底卡片
        state["reply_card_json"] = _build_result_card([], intent)

    return state
