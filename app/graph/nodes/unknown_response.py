"""Response node for messages outside the AI-news bot's supported scope."""

from app.graph.state import QueryState


UNKNOWN_GUIDANCE = (
    "我主要提供 AI 行业新闻查询和新闻问答。\n\n"
    "你可以问：\n"
    "• 最近 OpenAI 有什么新闻？\n"
    "• 这条新闻对开发者有什么影响？"
)


def unknown_response_node(state: QueryState) -> QueryState:
    """Put a platform-neutral guidance message into the query state."""
    state["rich_message"] = {
        "title": "AI 新闻 Bot",
        "body": UNKNOWN_GUIDANCE,
        "buttons": [],
        "color_hint": "info",
        "footer": None,
    }
    state["reply_card_json"] = {}
    return state
