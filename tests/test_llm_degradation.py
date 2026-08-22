"""LLM 熔断 + 降级链路测试

验证 LLM 不可用时（熔断器 OPEN 或调用异常）各节点走兜底链路，
不抛错且返回可用结果。
"""
import pytest
from unittest.mock import patch, MagicMock

from app.core.resilience import CircuitBreakerOpenError
from app.graph.state import QueryState
from app.core.multi_cache import reset_caches_for_tests


@pytest.fixture(autouse=True)
def _clean_caches():
    """每个用例前重置缓存，避免缓存命中掩盖降级路径"""
    reset_caches_for_tests()
    yield
    reset_caches_for_tests()


def _make_state(query: str, query_type: str = "qa") -> QueryState:
    return {
        "platform": "feishu",
        "user_id": "u1",
        "chat_id": "c1",
        "user_query": query,
        "query_type": query_type,
    }  # type: ignore[return-value]


class TestIntentRouterDegradation:
    def test_circuit_open_falls_back_to_keywords(self):
        """路由节点：熔断 OPEN → 关键词启发式，不抛错"""
        from app.graph.nodes import intent_router

        with patch.object(
            intent_router, "_emit_degraded"
        ) as mock_degraded, patch("app.llm.provider.llm_circuit_breaker.call") as mock_call:
            mock_call.side_effect = CircuitBreakerOpenError("open")
            state = _make_state("GPT-5 什么时候发布？")
            result = intent_router.intent_router_node(state)

        # 疑问句 → qa（关键词启发式命中）
        assert result["query_type"] == "qa"
        mock_degraded.assert_called_once_with("router")


class TestIntentDegradation:
    def test_circuit_open_falls_back_to_keywords(self):
        """意图解析节点：熔断 OPEN → 关键词兜底"""
        from app.graph.nodes import intent

        with patch.object(intent, "_emit_degraded") as mock_degraded, patch(
            "app.graph.nodes.intent.llm_circuit_breaker.call"
        ) as mock_call, patch("app.graph.nodes.intent.get_llm") as mock_get_llm:
            mock_call.side_effect = CircuitBreakerOpenError("open")
            mock_get_llm.return_value = MagicMock()
            state = _make_state("OpenAI 最近有什么新闻", query_type="list")
            result = intent.intent_node(state)

        parsed = result["parsed_intent"]
        assert parsed["vendor"] == "OpenAI"  # 别名匹配兜底
        assert parsed["days"] >= 1
        mock_degraded.assert_called_once_with("intent")


class TestRagAnswerDegradation:
    def test_circuit_open_returns_article_list(self):
        """RAG 答案节点：熔断 OPEN → 返回检索文章列表兜底"""
        from app.graph.nodes import rag_answer

        state = _make_state("GPT-5 发布了吗？")
        state["rag_context"] = [
            {"id": 1, "title": "GPT-5 发布", "vendor": "OpenAI",
             "published_at": "2026-08-20", "url": "http://x", "raw_content": "..."},
            {"id": 2, "title": "新模型评测", "vendor": "OpenAI",
             "published_at": "2026-08-21", "url": "http://y", "raw_content": "..."},
        ]

        with patch.object(rag_answer, "_emit_degraded") as mock_degraded, patch(
            "app.llm.provider.get_llm"
        ) as mock_get_llm, patch(
            "app.llm.provider.llm_circuit_breaker.call"
        ) as mock_call:
            mock_get_llm.return_value = MagicMock()
            mock_call.side_effect = CircuitBreakerOpenError("open")
            result = rag_answer.rag_answer_node(state)

        answer = result["rag_answer"]
        # 降级答复包含两篇文章标题 + 提示语
        assert "GPT-5 发布" in answer["answer_text"]
        assert "新模型评测" in answer["answer_text"]
        assert len(answer["sources"]) == 2
        mock_degraded.assert_called_once_with("rag_answer")

    def test_generic_exception_also_degrades(self):
        """RAG 答案节点：普通异常同样降级为文章列表（不抛错）"""
        from app.graph.nodes import rag_answer

        state = _make_state("问题")
        state["rag_context"] = [
            {"id": 1, "title": "文章A", "vendor": "OpenAI",
             "published_at": "2026-08-20", "url": "http://x"},
        ]

        with patch.object(rag_answer, "_emit_degraded"), patch(
            "app.llm.provider.get_llm"
        ) as mock_get_llm, patch(
            "app.llm.provider.llm_circuit_breaker.call"
        ) as mock_call:
            mock_get_llm.return_value = MagicMock()
            mock_call.side_effect = RuntimeError("boom")
            result = rag_answer.rag_answer_node(state)

        assert "文章A" in result["rag_answer"]["answer_text"]
        assert len(result["rag_answer"]["sources"]) == 1
