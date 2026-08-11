"""BotQueryGraph: 飞书交互查询工作流定义

编排 @Bot 消息触发后的意图分类→处理→回复链路。
支持条件路由：list（文章列表）和 qa（RAG 智能问答）两条路径。
"""
from langgraph.graph import StateGraph, END
from app.graph.state import QueryState
from app.graph.nodes.intent import intent_node
from app.graph.nodes.intent_router import intent_router_node
from app.graph.nodes.search_db import search_db_node
from app.graph.nodes.format_response import format_response_node
from app.graph.nodes.rag_retrieve import rag_retrieve_node
from app.graph.nodes.rag_answer import rag_answer_node
from app.graph.nodes.reply_feishu import reply_feishu_node

import logging
logger = logging.getLogger(__name__)


def _route_by_query_type(state: QueryState) -> str:
    """条件路由：根据 query_type 分发到不同处理路径"""
    query_type = state.get("query_type", "list")
    if query_type == "qa":
        return "qa"
    return "list"


def _format_rag_response_node(state: QueryState) -> QueryState:
    """将 RAG 答案格式化为平台无关 RichMessage + 飞书卡片

    在 rag_answer 生成后调用，构建 RAG 答案并写入 rich_message 和 reply_card_json。
    """
    rag = state.get("rag_answer", {})
    answer_text = rag.get("answer_text", "")
    sources = rag.get("sources", [])
    original_query = state.get("user_query", "")

    try:
        from app.platforms.message_model import RichMessage, ActionButton

        # 构建 RichMessage（平台无关）
        body_parts = []
        if original_query:
            q = original_query[:200] + "..." if len(original_query) > 200 else original_query
            body_parts.append(f"💬 **你问：** {q}")
            body_parts.append("")

        body_parts.append(answer_text or "暂无答案")

        # 来源按钮
        buttons = []
        if sources:
            body_parts.append("")
            body_parts.append("📚 **参考来源：**")
            for i, src in enumerate(sources, 1):
                vendor = src.get("vendor", "")
                title = (src.get("title", "查看原文") or "查看原文")[:30]
                label_parts = []
                if vendor:
                    label_parts.append(vendor)
                label_parts.append(title)
                label = f"📖 {' · '.join(label_parts)}"
                if src.get("url"):
                    buttons.append(ActionButton(
                        label=label[:40],
                        action="url",
                        value=src["url"],
                        style="default",
                    ))

        msg = RichMessage(
            title="🤖 AI 行业情报",
            body="\n".join(body_parts),
            buttons=buttons,
            color_hint="success",
            footer="💡 发送「OpenAI 最近有什么新闻」查看最新动态" if not sources else None,
        )

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

        # 同时构建飞书卡片（向后兼容）
        from app.feishu.card_builder import build_rag_answer_card
        card = build_rag_answer_card(
            answer_text=answer_text,
            sources=sources,
            original_query=original_query,
        )
        state["reply_card_json"] = card
        logger.info("RAG response built (RichMessage + Feishu card)")
    except Exception as e:
        logger.error(f"format_rag_response_node failed: {e}")
        state["reply_card_json"] = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red",
                "title": {"tag": "plain_text", "content": "⚠️ 生成答案时出错了"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "请稍后再试，或发送「**OpenAI 最近有什么新闻**」查看最新动态。"}},
            ],
        }
        state["rich_message"] = {
            "title": "⚠️ 出错了",
            "body": "请稍后再试，或发送「OpenAI 最近有什么新闻」查看最新动态。",
            "buttons": [], "color_hint": "warning", "footer": None,
        }
    return state


def build_query_graph() -> StateGraph:
    """构建 BotQueryGraph 工作流（条件路由）

    节点流程：

      intent_router:
        ├─ "list" → intent (vendor+days) → search_db → format_response → reply → END
        └─ "qa"   → rag_retrieve → rag_answer → format_rag_response → reply → END

    向后兼容：list 路径保留了完整的 4 节点链路（intent → search_db → format_response → reply）。
    qa 路径是新增的 RAG 智能问答链路。

    Returns:
        编译后的 StateGraph，可直接 invoke 执行。
    """
    graph = StateGraph(QueryState)

    # 注册所有节点
    graph.add_node("intent_router", intent_router_node)
    graph.add_node("intent", intent_node)
    graph.add_node("search_db", search_db_node)
    graph.add_node("format_response", format_response_node)
    graph.add_node("rag_retrieve", rag_retrieve_node)
    graph.add_node("rag_answer", rag_answer_node)
    graph.add_node("format_rag_response", _format_rag_response_node)
    graph.add_node("reply", reply_feishu_node)

    # 入口 → 意图路由
    graph.set_entry_point("intent_router")

    # 条件分发
    graph.add_conditional_edges(
        "intent_router",
        _route_by_query_type,
        {
            "list": "intent",
            "qa": "rag_retrieve",
        },
    )

    # --- list 路径（原有逻辑） ---
    graph.add_edge("intent", "search_db")
    graph.add_edge("search_db", "format_response")
    graph.add_edge("format_response", "reply")
    graph.add_edge("reply", END)

    # --- qa 路径（RAG 新逻辑） ---
    graph.add_edge("rag_retrieve", "rag_answer")
    graph.add_edge("rag_answer", "format_rag_response")
    graph.add_edge("format_rag_response", "reply")

    return graph.compile()
