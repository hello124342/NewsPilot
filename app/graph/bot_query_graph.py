"""BotQueryGraph: 飞书交互查询工作流定义

编排 @Bot 消息触发后的意图识别→数据库检索→格式化→回复链路。
"""
from langgraph.graph import StateGraph, END
from app.graph.state import QueryState
from app.graph.nodes.intent import intent_node
from app.graph.nodes.search_db import search_db_node
from app.graph.nodes.format_response import format_response_node
from app.graph.nodes.reply_feishu import reply_feishu_node


def build_query_graph() -> StateGraph:
    """构建 BotQueryGraph 工作流

    节点顺序：
    intent → search_db → format_response → reply → END

    Returns:
        编译后的 StateGraph，可直接 invoke 执行。
    """
    graph = StateGraph(QueryState)

    # 注册 4 个处理节点
    graph.add_node("intent", intent_node)
    graph.add_node("search_db", search_db_node)
    graph.add_node("format_response", format_response_node)
    graph.add_node("reply", reply_feishu_node)

    # 线性流程
    graph.set_entry_point("intent")
    graph.add_edge("intent", "search_db")
    graph.add_edge("search_db", "format_response")
    graph.add_edge("format_response", "reply")
    graph.add_edge("reply", END)

    return graph.compile()
