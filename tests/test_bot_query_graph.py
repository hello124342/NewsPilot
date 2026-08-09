"""LangGraph BotQueryGraph 测试

测试 @Bot 交互查询的意图识别和 Graph 组装。
"""
import pytest
from unittest.mock import patch, MagicMock


class TestIntentNode:
    """IntentNode 意图识别测试"""

    @patch("app.graph.nodes.intent.get_llm")
    def test_parse_intent_query_by_vendor(self, mock_get_llm):
        """测试按厂商查询的意图识别"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"vendor": "OpenAI", "days": 7}'
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        from app.graph.nodes.intent import intent_node
        from app.graph.state import QueryState

        state: QueryState = {
            "user_id": "user_123",
            "chat_id": "oc_xxx",
            "user_query": "OpenAI 有什么新消息",
            "parsed_intent": {},
            "query_results": [],
            "reply_card_json": {},
        }
        result = intent_node(state)
        assert result["parsed_intent"]["vendor"] == "OpenAI"
        assert result["parsed_intent"]["days"] == 7

    @patch("app.graph.nodes.intent.get_llm")
    def test_parse_intent_query_all(self, mock_get_llm):
        """测试查询所有厂商最新消息"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"vendor": null, "days": 3}'
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        from app.graph.nodes.intent import intent_node
        from app.graph.state import QueryState

        state: QueryState = {
            "user_id": "user_123",
            "chat_id": "oc_xxx",
            "user_query": "最近有什么新闻",
            "parsed_intent": {},
            "query_results": [],
            "reply_card_json": {},
        }
        result = intent_node(state)
        assert result["parsed_intent"]["vendor"] is None
        assert result["parsed_intent"]["days"] == 3

    @patch("app.graph.nodes.intent.get_llm")
    def test_parse_intent_llm_error_fallback(self, mock_get_llm):
        """测试 LLM 调用失败时的降级处理"""
        mock_get_llm.side_effect = Exception("LLM error")

        from app.graph.nodes.intent import intent_node
        from app.graph.state import QueryState

        state: QueryState = {
            "user_id": "user_123",
            "chat_id": "oc_xxx",
            "user_query": "Something",
            "parsed_intent": {},
            "query_results": [],
            "reply_card_json": {},
        }
        result = intent_node(state)
        # 降级：默认查询最近 3 天所有厂商
        assert result["parsed_intent"] == {"vendor": None, "days": 3}

    def test_extract_vendor_from_query(self):
        """测试从查询文本中提取厂商名称"""
        from app.graph.nodes.intent import extract_vendor_from_query

        assert extract_vendor_from_query("OpenAI 有什么新闻") == "OpenAI"
        assert extract_vendor_from_query("DeepSeek 最新动态") == "DeepSeek"
        assert extract_vendor_from_query("最近有什么新闻") is None


class TestBotQueryGraph:
    """BotQueryGraph 组装测试"""

    def test_graph_compiles(self):
        """测试 BotQueryGraph 能成功编译"""
        from app.graph.bot_query_graph import build_query_graph

        graph = build_query_graph()
        assert graph is not None
        assert hasattr(graph, "invoke")
