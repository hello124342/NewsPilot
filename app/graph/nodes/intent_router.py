"""IntentRouterNode: 查询意图分类节点

区分用户是想"查列表"还是"问问题"，以便路由到不同的处理链路。
- list: 按厂商/时间查找文章列表 → 现有 search_db 路径
- qa:   自然语言问答 → 新 RAG 路径（语义检索 + LLM 综合回答）
- command: 订阅/设置等管理命令（在 event_router 层已拦截，此处兜底）

LLM 分类优先，失败时降级为关键词启发式规则。
"""
import json
import logging
import re
from app.graph.state import QueryState

logger = logging.getLogger(__name__)

# 轻量分类 Prompt（复用项目的 YAML loader 模式）
_INTENT_ROUTER_PROMPT = """你是查询意图分类器。判断用户消息属于哪种类型，返回 JSON。

类型说明：
- "list": 用户想看文章列表、查找新闻、浏览动态。关键词：有什么、最近、新闻、动态、列表、查找、搜索
- "qa": 用户在问一个具体问题，希望得到答案而非文章列表。关键词：什么时候、为什么、是什么、怎么样、对比、区别、是否、能不能

用户消息：{query}

返回格式（仅返回 JSON）：
{{"type": "list"}} 或 {{"type": "qa"}}"""

# QA 类问题的启发式信号（问号 / 疑问词）
_QA_SIGNALS = [
    r"[?？]",           # 问号
    r"什么时候|何时",
    r"为什么|为何",
    r"是什么|什么是",
    r"怎么样|如何|怎样",
    r"对比|比较|区别|差异",
    r"是否|能不能|可以.*吗",
    r"哪个.*好|谁.*强",
    r"解释|介绍一下|说说",
    r"分析|评价|评估",
]

# List 类问题的启发式信号
_LIST_SIGNALS = [
    r"有什么|有哪些",
    r"最近|近日|近期",
    r"新闻|动态|消息|更新",
    r"列表|列出|列举",
    r"订阅|退订|设置",
]


def _classify_by_keywords(query: str) -> str:
    """关键词启发式分类（LLM 失败时的降级方案）

    优先级：QA 信号 > List 信号 > 默认 list
    """
    qa_score = sum(1 for pat in _QA_SIGNALS if re.search(pat, query))
    list_score = sum(1 for pat in _LIST_SIGNALS if re.search(pat, query))

    # QA 信号优先：只要检测到疑问意图，就走 qa 路径
    if qa_score > 0:
        return "qa"
    # 含新闻/动态关键词 → list
    if list_score > 0:
        return "list"
    # 默认走 list（返回文章列表比什么都不返友好）
    return "list"


def intent_router_node(state: QueryState) -> QueryState:
    """分类用户意图并写入 state

    优先走 LLM 分类（3 次重试通过 _call_llm_router），
    失败时降级为关键词启发式。
    """
    query = state.get("user_query", "").strip()
    if not query:
        state["query_type"] = "list"
        return state

    # 尝试 LLM 分类
    try:
        from app.llm.provider import get_llm
        from app.core.config import Settings
        from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

        @retry(
            stop=stop_after_attempt(2),
            wait=wait_exponential(multiplier=1, min=1, max=3),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        def _call_llm_router(llm, prompt: str) -> str:
            response = llm.invoke(prompt)
            return response.content  # type: ignore[union-attr]

        settings = Settings()  # type: ignore[call-arg]
        llm = get_llm(settings)
        prompt = _INTENT_ROUTER_PROMPT.format(query=query)
        content = _call_llm_router(llm, prompt)

        json_match = re.search(r"\{[^}]+\}", content)
        if json_match:
            result = json.loads(json_match.group())
            query_type = result.get("type", "list")
            if query_type in ("list", "qa"):
                state["query_type"] = query_type
                logger.info(f"Intent routed: '{query[:50]}' → {query_type} (LLM)")
                return state

    except Exception as e:
        logger.warning(f"LLM intent routing failed, falling back to keywords: {e}")

    # 降级：关键词启发式
    query_type = _classify_by_keywords(query)
    state["query_type"] = query_type
    logger.info(f"Intent routed: '{query[:50]}' → {query_type} (heuristic)")
    return state
