"""NewsPushGraph: 新闻推送工作流定义

编排抓取→总结→存储→建卡→推送的完整处理链路。
支持节点级条件路由（FAILED 跳过后续节点）、
MemorySaver 检查点（可恢复中断的工作流）、
Human-in-the-Loop（发送前人工审核）。
"""
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from app.graph.state import PushState
from app.graph.nodes.extract import extract_node
from app.graph.nodes.summarize import summarize_node
from app.graph.nodes.store import store_node
from app.graph.nodes.build_card import build_card_node
from app.graph.nodes.send_feishu import send_feishu_node


def _should_continue(state: PushState) -> str:
    """条件路由：上游节点失败时直接结束"""
    if state.get("status") == "FAILED":
        return END
    return "continue"


def build_push_graph(
    enable_checkpoint: bool = False,
    enable_human_review: bool = False,
    push_enabled: bool = True,
) -> StateGraph:
    """构建 NewsPushGraph 工作流

    Args:
        enable_checkpoint: 启用 MemorySaver 检查点（可恢复中断的工作流）
        enable_human_review: 启用 send_feishu 前人工审核（Human-in-the-Loop）
        push_enabled: False 时仅抓取+总结+存储（不建卡不推送），用于凌晨预处理

    Returns:
        编译后的 StateGraph
    """
    graph = StateGraph(PushState)

    graph.add_node("extract", extract_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("store", store_node)

    graph.set_entry_point("extract")

    if push_enabled:
        # 完整链路：extract → summarize → store → build_card → send_feishu → END
        graph.add_node("build_card", build_card_node)
        graph.add_node("send_feishu", send_feishu_node)

        graph.add_conditional_edges("extract", _should_continue,
            {"continue": "summarize", END: END})
        graph.add_conditional_edges("summarize", _should_continue,
            {"continue": "store", END: END})
        graph.add_conditional_edges("store", _should_continue,
            {"continue": "build_card", END: END})
        graph.add_conditional_edges("build_card", _should_continue,
            {"continue": "send_feishu", END: END})
        graph.add_edge("send_feishu", END)
    else:
        # 仅处理：extract → summarize → store → END
        graph.add_conditional_edges("extract", _should_continue,
            {"continue": "summarize", END: END})
        graph.add_conditional_edges("summarize", _should_continue,
            {"continue": "store", END: END})
        graph.add_edge("store", END)

    compile_kwargs = {}
    if enable_checkpoint:
        compile_kwargs["checkpointer"] = MemorySaver()
    if enable_human_review and push_enabled:
        compile_kwargs["interrupt_before"] = ["send_feishu"]

    return graph.compile(**compile_kwargs)
