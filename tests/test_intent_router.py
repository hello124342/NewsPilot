"""意图路由节点测试

测试 intent_router_node 的分类逻辑：
- LLM 分类（list / qa）
- 关键词降级方案
- 边界情况
"""
import pytest
from unittest.mock import patch, MagicMock
from app.graph.state import QueryState


class TestIntentRouterLLM:
    """LLM 意图分类测试"""

    @patch("app.llm.provider.get_llm")
    def test_route_list_query(self, mock_get_llm):
        """测试「查列表」类型: 厂商+最近+新闻"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"type": "list"}'
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        from app.graph.nodes.intent_router import intent_router_node

        state: QueryState = {
            "user_id": "user_1",
            "chat_id": "oc_1",
            "user_query": "OpenAI 最近有什么新闻",
        }
        result = intent_router_node(state)
        assert result["query_type"] == "list"

    @patch("app.llm.provider.get_llm")
    def test_route_qa_query(self, mock_get_llm):
        """测试「问答」类型: 具体问题"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"type": "qa"}'
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        from app.graph.nodes.intent_router import intent_router_node

        state: QueryState = {
            "user_id": "user_1",
            "chat_id": "oc_1",
            "user_query": "GPT-5 什么时候发布",
        }
        result = intent_router_node(state)
        assert result["query_type"] == "qa"

    @patch("app.llm.provider.get_llm")
    def test_route_list_fallback_news_keywords(self, mock_get_llm):
        """测试 LLM 不可用时降级: 含「新闻」→ list"""
        mock_get_llm.side_effect = Exception("LLM unavailable")

        from app.graph.nodes.intent_router import intent_router_node

        state: QueryState = {
            "user_id": "user_1",
            "chat_id": "oc_1",
            "user_query": "DeepSeek 有什么新闻",
        }
        result = intent_router_node(state)
        assert result["query_type"] == "list"

    @patch("app.llm.provider.get_llm")
    def test_route_qa_fallback_question_keywords(self, mock_get_llm):
        """测试 LLM 不可用时降级: 含疑问词 → qa"""
        mock_get_llm.side_effect = Exception("LLM unavailable")

        from app.graph.nodes.intent_router import intent_router_node

        state: QueryState = {
            "user_id": "user_1",
            "chat_id": "oc_1",
            "user_query": "为什么 OpenAI 要发布 GPT-5？",
        }
        result = intent_router_node(state)
        assert result["query_type"] == "qa"

    @patch("app.llm.provider.get_llm")
    def test_route_default_list_for_ambiguous(self, mock_get_llm):
        """测试模糊查询 → 默认 list"""
        mock_get_llm.side_effect = Exception("LLM unavailable")

        from app.graph.nodes.intent_router import intent_router_node

        state: QueryState = {
            "user_id": "user_1",
            "chat_id": "oc_1",
            "user_query": "Anthropic",
        }
        result = intent_router_node(state)
        assert result["query_type"] == "list"

    def test_route_empty_query(self):
        """测试空查询 → 默认 list"""
        from app.graph.nodes.intent_router import intent_router_node

        state: QueryState = {
            "user_id": "user_1",
            "chat_id": "oc_1",
            "user_query": "",
        }
        result = intent_router_node(state)
        assert result["query_type"] == "list"


class TestKeywordHeuristic:
    """关键词启发式分类测试"""

    def test_qa_signals(self):
        from app.graph.nodes.intent_router import _classify_by_keywords

        assert _classify_by_keywords("GPT-5 什么时候发布？") == "qa"
        assert _classify_by_keywords("为什么 DeepSeek 这么强") == "qa"
        assert _classify_by_keywords("OpenAI 和 Anthropic 有什么区别") == "qa"
        assert _classify_by_keywords("能不能介绍一下 GPT-5") == "qa"

    def test_list_signals(self):
        from app.graph.nodes.intent_router import _classify_by_keywords

        assert _classify_by_keywords("OpenAI 最近有什么新闻") == "list"
        assert _classify_by_keywords("最近 AI 的动态") == "list"
        assert _classify_by_keywords("订阅列表") == "list"

    def test_ambiguous_defaults_to_list(self):
        from app.graph.nodes.intent_router import _classify_by_keywords

        assert _classify_by_keywords("Hello") == "list"
        assert _classify_by_keywords("123") == "list"
        assert _classify_by_keywords("") == "list"
