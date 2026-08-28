"""Three-way intent routing for NewsPilot queries.

The order is deliberately simple and deterministic:
1. Explicit list/QA signals are handled locally.
2. Unmatched input goes to the optional local Ollama LoRA classifier.
3. Disabled, unavailable, invalid, or low-confidence model output becomes unknown.
"""

from __future__ import annotations

import logging
import re

from app.core.config import Settings
from app.graph.state import QueryState
from app.intent.ollama_classifier import OllamaIntentClassifier

logger = logging.getLogger(__name__)


_QA_SIGNALS = [
    r"[?？]",
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

_LIST_SIGNALS = [
    r"有什么|有哪些",
    r"最近|近日|近期",
    r"新闻|动态|消息|更新",
    r"列表|列出|列举|查找|搜索|查询|浏览|看看",
]

_NEWS_CONTEXT_SIGNALS = [
    r"OpenAI|Anthropic|DeepSeek|Kimi|Moonshot|Google DeepMind|Z\.ai",
    r"GPT|Claude|LLM|RAG|Agent",
    r"AI|人工智能|大模型|模型|生成式",
    r"新闻|动态|消息|报道|资讯|发布|API",
]


def _explicit_keyword_intent(query: str) -> str | None:
    """Return an intent only when a clear local signal is present."""
    if not any(re.search(pattern, query, re.IGNORECASE) for pattern in _NEWS_CONTEXT_SIGNALS):
        return None
    qa_score = sum(1 for pattern in _QA_SIGNALS if re.search(pattern, query))
    list_score = sum(1 for pattern in _LIST_SIGNALS if re.search(pattern, query))
    if qa_score > 0:
        return "qa"
    if list_score > 0:
        return "list"
    return None


def _classify_by_keywords(query: str) -> str:
    """Backward-compatible keyword helper; ambiguous input defaults to list."""
    return _explicit_keyword_intent(query) or "list"


def _set_intent(state: QueryState, intent: str, confidence: float, source: str) -> QueryState:
    state["query_type"] = intent
    state["intent_confidence"] = confidence
    state["intent_source"] = source
    return state


def intent_router_node(state: QueryState) -> QueryState:
    """Classify a query using rules first, then the local Ollama model."""
    query = state.get("user_query", "").strip()
    if not query:
        return _set_intent(state, "unknown", 0.0, "unknown")

    keyword_intent = _explicit_keyword_intent(query)
    if keyword_intent:
        logger.info("Intent routed: '%s' -> %s (rule)", query[:50], keyword_intent)
        return _set_intent(state, keyword_intent, 1.0, "rule")

    settings = Settings()  # type: ignore[call-arg]
    if not settings.INTENT_OLLAMA_ENABLED:
        logger.info("Intent routed: '%s' -> unknown (ollama disabled)", query[:50])
        return _set_intent(state, "unknown", 0.0, "unknown")

    classifier = OllamaIntentClassifier(
        base_url=settings.INTENT_OLLAMA_URL,
        model=settings.INTENT_OLLAMA_MODEL,
        timeout=settings.INTENT_OLLAMA_TIMEOUT_SECONDS,
        threshold=settings.INTENT_CONFIDENCE_THRESHOLD,
    )
    prediction = classifier.predict(query)
    if prediction.intent in ("list", "qa") and prediction.confidence is not None:
        logger.info(
            "Intent routed: '%s' -> %s (ollama, confidence=%.2f)",
            query[:50],
            prediction.intent,
            prediction.confidence,
        )
        return _set_intent(
            state, prediction.intent, prediction.confidence, prediction.source
        )

    logger.info(
        "Intent routed: '%s' -> unknown (%s, confidence=%s)",
        query[:50],
        prediction.source,
        prediction.confidence,
    )
    return _set_intent(
        state,
        "unknown",
        prediction.confidence or 0.0,
        prediction.source,
    )
