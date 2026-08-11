"""FormatResponseNode: 查询结果格式化节点

将 MySQL 查询结果格式化为平台无关的 RichMessage，
支持多篇文章列表展示和空结果处理。
同时保留 reply_card_json 用于向后兼容飞书格式。
"""
import logging
from app.graph.state import QueryState
from app.platforms.message_model import RichMessage, ActionButton

logger = logging.getLogger(__name__)


def _build_rich_message(results: list[dict], intent: dict) -> RichMessage:
    """将查询结果构建为平台无关的 RichMessage

    Args:
        results: 文章列表，每项含 title, vendor, published_at, url, summary_points
        intent: 查询意图 {"vendor": str|None, "days": int}

    Returns:
        RichMessage（Markdown 正文 + 阅读原文按钮）
    """
    vendor_name = intent.get("vendor") or "所有厂商"
    days = intent.get("days") or 3

    if not results:
        return RichMessage(
            title=f"🔍 {vendor_name} · 近{days}天",
            body="😕 **未找到相关新闻**\n\n可尝试扩大搜索范围或稍后再试。",
            color_hint="info",
        )

    # 构建文章列表 Markdown
    lines = []
    for article in results:
        lines.append(f"**{article['title']}**")
        lines.append(f"🏷️ {article['vendor']} · 📅 {article['published_at']}")

        points = article.get("summary_points", [])
        if points and points[0]:
            for p in points[:3]:
                if p:
                    lines.append(f"  • {p}")

        lines.append("")  # 文章间空行

    body = "\n".join(lines)
    title = f"🔍 {vendor_name} · 近{days}天  ({len(results)} 篇)"

    # 构建按钮：第一篇的「阅读原文」
    buttons = []
    first_url = results[0].get("url") if results else ""
    if first_url:
        buttons.append(ActionButton(
            label="📖 阅读原文",
            action="url",
            value=first_url,
            style="primary",
        ))

    return RichMessage(
        title=title,
        body=body,
        buttons=buttons,
        color_hint="info",
        footer="💡 发送「帮助」查看更多使用方式",
    )


def _build_result_card_legacy(results: list[dict], intent: dict) -> dict:
    """[过渡期] 构建飞书特定格式卡片（保持向后兼容）"""
    from app.platforms.feishu.renderer import render_card
    return render_card(_build_rich_message(results, intent))


def format_response_node(state: QueryState) -> QueryState:
    """将 query_results 格式化为 RichMessage + 飞书卡片

    写入 rich_message 和 reply_card_json 到 state。
    """
    results = state.get("query_results", [])
    intent = state.get("parsed_intent", {})

    try:
        msg = _build_rich_message(results, intent)
        state["reply_card_json"] = _build_result_card_legacy(results, intent)
        state["rich_message"] = {
            "title": msg.title,
            "body": msg.body,
            "buttons": [
                {"label": b.label, "action": b.action, "value": b.value, "style": b.style}
                for b in msg.buttons
            ],
            "color_hint": msg.color_hint,
            "footer": msg.footer,
        }
        logger.info(f"Response built: {len(results)} articles")
    except Exception as e:
        logger.error(f"format_response_node failed: {e}")
        msg = _build_rich_message([], intent)
        state["reply_card_json"] = _build_result_card_legacy([], intent)
        state["rich_message"] = {
            "title": msg.title, "body": msg.body,
            "buttons": [], "color_hint": msg.color_hint, "footer": msg.footer,
        }

    return state
