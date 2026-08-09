"""LangGraph NewsPushGraph 测试

测试推送工作流的节点和 Graph 组装。
"""
import pytest
from unittest.mock import patch, Mock, MagicMock


class TestPushState:
    """PushState 状态结构测试"""

    def test_state_fields(self):
        """测试 PushState 包含所有必需字段"""
        from app.graph.state import PushState

        state: PushState = {
            "raw_url": "https://example.com/test",
            "raw_content": "",
            "vendor": "OpenAI",
            "title": "",
            "summary_points": [],
            "card_json": {},
            "status": "PENDING",
        }
        assert "raw_url" in state
        assert "vendor" in state
        assert "status" in state

    def test_state_status_values(self):
        """测试 PushState status 字段的合法值"""
        from app.graph.state import PushState

        for status in ["PENDING", "SUCCESS", "FAILED"]:
            state: PushState = {
                "raw_url": "",
                "raw_content": "",
                "vendor": "",
                "title": "",
                "summary_points": [],
                "card_json": {},
                "status": status,
            }
            assert state["status"] == status


class TestExtractNode:
    """ExtractNode 测试"""

    @patch("app.graph.nodes.extract.scrape_article_text")
    def test_extract_node_success(self, mock_scrape):
        """测试成功抓取正文并更新 state"""
        mock_scrape.return_value = "文章正文内容"

        from app.graph.nodes.extract import extract_node
        from app.graph.state import PushState

        state: PushState = {
            "raw_url": "https://example.com/article",
            "raw_content": "",
            "vendor": "OpenAI",
            "title": "",
            "summary_points": [],
            "card_json": {},
            "status": "PENDING",
        }
        result = extract_node(state)
        assert result["raw_content"] == "文章正文内容"
        assert result["title"] == "https://example.com/article"

    @patch("app.graph.nodes.extract.scrape_article_text")
    def test_extract_node_empty_content(self, mock_scrape):
        """测试抓取失败时 status 变为 FAILED"""
        mock_scrape.return_value = ""

        from app.graph.nodes.extract import extract_node
        from app.graph.state import PushState

        state: PushState = {
            "raw_url": "https://example.com/broken",
            "raw_content": "",
            "vendor": "OpenAI",
            "title": "",
            "summary_points": [],
            "card_json": {},
            "status": "PENDING",
        }
        result = extract_node(state)
        assert result["status"] == "FAILED"


class TestSummarizeNode:
    """SummarizeNode 测试"""

    @patch("app.graph.nodes.summarize.get_llm")
    def test_summarize_node_success(self, mock_get_llm):
        """测试 LLM 总结成功提取三个要点"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "1. 要点一：内容\n2. 要点二：技术\n3. 要点三：影响"
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        from app.graph.nodes.summarize import summarize_node
        from app.graph.state import PushState

        state: PushState = {
            "raw_url": "https://example.com",
            "raw_content": "这是一篇测试文章",
            "vendor": "OpenAI",
            "title": "测试标题",
            "summary_points": [],
            "card_json": {},
            "status": "PENDING",
        }
        result = summarize_node(state)
        assert len(result["summary_points"]) == 3
        assert "要点一" in result["summary_points"][0]
        assert result["status"] == "PENDING"

    @patch("app.graph.nodes.summarize.get_llm")
    def test_summarize_node_empty_content(self, mock_get_llm):
        """测试文章内容为空时 status 变为 FAILED"""
        from app.graph.nodes.summarize import summarize_node
        from app.graph.state import PushState

        state: PushState = {
            "raw_url": "https://example.com",
            "raw_content": "",
            "vendor": "OpenAI",
            "title": "",
            "summary_points": [],
            "card_json": {},
            "status": "PENDING",
        }
        result = summarize_node(state)
        assert result["status"] == "FAILED"

    def test_parse_llm_summary(self):
        """测试 parse_summary 将 LLM 输出解析为要点列表"""
        from app.graph.nodes.summarize import parse_summary

        text = "1. 第一点\n2. 第二点\n3. 第三点"
        points = parse_summary(text)
        assert len(points) == 3

    def test_parse_summary_fallback(self):
        """测试无法解析时按行分割降级处理"""
        from app.graph.nodes.summarize import parse_summary

        text = "- 要点A\n- 要点B"
        points = parse_summary(text)
        assert len(points) == 2


class TestStoreNode:
    """StoreNode 测试"""

    @patch("app.db.database.SessionLocal")
    def test_store_node_success(self, mock_session):
        """测试成功存储文章到 MySQL"""
        from app.graph.nodes.store import store_node
        from app.graph.state import PushState

        state: PushState = {
            "raw_url": "https://example.com/article",
            "raw_content": "正文",
            "vendor": "OpenAI",
            "title": "测试文章",
            "published_at": "2026-08-09",
            "summary_points": ["点一", "点二", "点三"],
            "status": "SUCCESS",
        }
        result = store_node(state)
        assert result["status"] == "SUCCESS"
        mock_session.return_value.commit.assert_called_once()


class TestNewsPushGraph:
    """NewsPushGraph 组装测试"""

    def test_graph_compiles(self):
        """测试 NewsPushGraph 能成功编译"""
        from app.graph.news_push_graph import build_push_graph

        graph = build_push_graph()
        assert graph is not None
        # 编译后的 graph 应该有 invoke 方法
        assert hasattr(graph, "invoke")
