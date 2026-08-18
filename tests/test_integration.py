"""集成测试

测试完整的 NewsPushGraph 和 BotQueryGraph 流水线，
以及飞书 Webhook 事件处理。
全部外部依赖均通过 Mock 隔离。
"""
import json
import pytest
from unittest.mock import patch, MagicMock


class TestNewsPushGraphIntegration:
    """NewsPushGraph 完整流水线集成测试（mock 所有外部依赖）"""

    @patch("app.chat.lifecycle.get_active_chat_ids")
    @patch("app.graph.nodes.send_feishu.FeishuClient")
    @patch("app.graph.nodes.send_feishu.RedisClient")
    @patch("app.db.database.SessionLocal")
    @patch("app.graph.nodes.summarize.get_llm")
    @patch("app.graph.nodes.extract.scrape_article_text")
    def test_full_pipeline_success(
        self, mock_scrape, mock_get_llm, mock_session, mock_redis_client, mock_feishu, mock_active_chats
    ):
        """测试完整流水线：抓取→总结→存储→建卡→发送 全部成功"""
        from app.graph.news_push_graph import build_push_graph
        from app.graph.state import PushState

        # Mock: 抓取返回正文
        mock_scrape.return_value = "这是一篇关于 GPT-5 的测试文章"

        # Mock: LLM 返回 3 条摘要
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "1. GPT-5 发布\n2. 性能提升10倍\n3. 支持多模态"
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        # Mock: chat_registry 返回活跃 chat
        mock_active_chats.return_value = ["chat_001"]

        # Mock: 数据库会话
        mock_db = MagicMock()
        mock_session.return_value = mock_db

        # Mock: 飞书客户端（mock_feishu 是类的 mock，mock_feishu_client 是实例 mock）
        mock_feishu_client = MagicMock()
        mock_feishu_client.send_card_to_all.return_value = [{"code": 0}]
        mock_feishu.return_value = mock_feishu_client

        # Mock: Redis（mock_redis_client 是类的 mock）
        mock_redis_instance = MagicMock()
        mock_redis_client.return_value = mock_redis_instance

        graph = build_push_graph()

        state: PushState = {
            "raw_url": "https://example.com/gpt5",
            "vendor": "OpenAI",
            "title": "GPT-5 正式发布",
            "published_at": "2026-08-09",
            "channel": "Blog",
            "status": "PENDING",
        }

        result = graph.invoke(state)
        assert result["status"] == "SUCCESS"
        assert len(result.get("summary_points", [])) == 3
        assert result.get("card_json", {}) != {}
        # 验证卡片发送被调用（新逻辑：逐个 send_card 而非 send_card_to_all）
        mock_feishu_client.send_card.assert_called()
        # 验证 URL 标记已处理
        mock_redis_instance.mark_url_processed.assert_called_once_with(
            "https://example.com/gpt5"
        )

    @patch("app.graph.nodes.extract.scrape_article_text")
    def test_pipeline_extract_failure_short_circuit(self, mock_scrape):
        """测试抓取失败时流水线短路：extract FAILED → 后续节点跳过"""
        from app.graph.news_push_graph import build_push_graph
        from app.graph.state import PushState

        mock_scrape.return_value = ""  # 抓取失败

        graph = build_push_graph()

        state: PushState = {
            "raw_url": "https://example.com/broken",
            "vendor": "OpenAI",
            "title": "Broken Article",
            "status": "PENDING",
        }

        result = graph.invoke(state)
        assert result["status"] == "FAILED"
        # summary_points 应为空（summary 节点未执行）
        assert result.get("summary_points", []) == []

    @patch("app.chat.lifecycle.get_active_chat_ids")
    @patch("app.graph.nodes.send_feishu.FeishuClient")
    @patch("app.graph.nodes.send_feishu.RedisClient")
    @patch("app.db.database.SessionLocal")
    @patch("app.graph.nodes.summarize.get_llm")
    @patch("app.graph.nodes.extract.scrape_article_text")
    def test_pipeline_with_checkpoint(
        self, mock_scrape, mock_get_llm, mock_session, mock_redis, mock_feishu, mock_active
    ):
        """测试带 MemorySaver checkpointer 的流水线"""
        from app.graph.news_push_graph import build_push_graph
        from app.graph.state import PushState

        mock_scrape.return_value = "测试内容"
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "1. 要点一\n2. 要点二\n3. 要点三"
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        mock_db = MagicMock()
        mock_session.return_value = mock_db
        mock_feishu.return_value = MagicMock()
        mock_redis.return_value = MagicMock()
        mock_active.return_value = ["chat_001"]

        graph = build_push_graph(enable_checkpoint=True)

        config = {"configurable": {"thread_id": "test-thread-1"}}
        state: PushState = {
            "raw_url": "https://example.com/test",
            "vendor": "OpenAI",
            "title": "Test",
            "published_at": "2026-08-09",
            "channel": "Blog",
            "status": "PENDING",
        }

        result = graph.invoke(state, config)
        assert result["status"] == "SUCCESS"

    @patch("app.chat.lifecycle.get_active_chat_ids")
    @patch("app.graph.nodes.send_feishu.FeishuClient")
    @patch("app.graph.nodes.send_feishu.RedisClient")
    @patch("app.db.database.SessionLocal")
    @patch("app.graph.nodes.summarize.get_llm")
    @patch("app.graph.nodes.extract.scrape_article_text")
    def test_pipeline_human_review_interrupt(
        self, mock_scrape, mock_get_llm, mock_session, mock_redis, mock_feishu, mock_active
    ):
        """测试 Human-in-the-Loop：send_feishu 前中断，审核后恢复"""
        from app.graph.news_push_graph import build_push_graph
        from app.graph.state import PushState

        mock_scrape.return_value = "测试内容"
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "1. 要点一\n2. 要点二\n3. 要点三"
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        mock_db = MagicMock()
        mock_session.return_value = mock_db

        mock_feishu_instance = MagicMock()
        mock_feishu.return_value = mock_feishu_instance
        mock_redis_instance = MagicMock()
        mock_redis.return_value = mock_redis_instance
        mock_active.return_value = ["chat_001"]

        graph = build_push_graph(enable_checkpoint=True, enable_human_review=True)

        config = {"configurable": {"thread_id": "test-review-1"}}
        state: PushState = {
            "raw_url": "https://example.com/test",
            "vendor": "OpenAI",
            "title": "Test",
            "published_at": "2026-08-09",
            "channel": "Blog",
            "status": "PENDING",
        }

        # 第一次 invoke：运行到 send_feishu 前中断
        result = graph.invoke(state, config)

        # 检查中断状态：card_json 应该已被 build_card 节点生成
        assert result.get("card_json", {}) != {}
        # 获取状态快照
        current_state = graph.get_state(config)
        assert current_state is not None

        # 模拟人工审核通过后恢复执行（传入 None 继续）
        if current_state.next:
            result2 = graph.invoke(None, config)
            assert result2.get("status") == "SUCCESS"


class TestBotQueryGraphIntegration:
    """BotQueryGraph 完整流水线集成测试"""

    @patch("app.feishu.client.FeishuClient")
    @patch("app.graph.nodes.search_db.SessionLocal")
    @patch("app.graph.nodes.intent.get_llm")
    def test_full_query_pipeline_with_results(
        self, mock_get_llm, mock_session, mock_feishu
    ):
        """测试完整查询流水线：意图→检索→格式化→回复，有结果"""
        from app.graph.bot_query_graph import build_query_graph
        from app.graph.state import QueryState
        from datetime import datetime

        # Mock: LLM 意图解析
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"vendor": "OpenAI", "days": 7}'
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        # Mock: 数据库查询返回 2 篇文章
        mock_article1 = MagicMock()
        mock_article1.title = "GPT-5 Released"
        mock_article1.vendor = "OpenAI"
        mock_article1.published_at = datetime(2026, 8, 8)
        mock_article1.url = "https://example.com/gpt5"
        mock_article1.summary_points = "Point 1\nPoint 2\nPoint 3"

        mock_article2 = MagicMock()
        mock_article2.title = "Sora Update"
        mock_article2.vendor = "OpenAI"
        mock_article2.published_at = datetime(2026, 8, 7)
        mock_article2.url = "https://example.com/sora"
        mock_article2.summary_points = "Point A\nPoint B"

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [mock_article1, mock_article2]

        mock_db = MagicMock()
        mock_db.query.return_value = mock_query
        mock_session.return_value = mock_db

        # Mock: 飞书回复
        mock_client = MagicMock()
        mock_client.send_card.return_value = {"code": 0}
        mock_feishu.return_value = mock_client

        graph = build_query_graph()

        state: QueryState = {
            "user_id": "user_123",
            "chat_id": "chat_456",
            "user_query": "OpenAI 最近有什么新闻",
        }

        result = graph.invoke(state)
        assert len(result.get("query_results", [])) == 2
        assert result.get("reply_card_json", {}) != {}
        mock_client.send_card.assert_called_once()

    @patch("app.graph.nodes.search_db.SessionLocal")
    @patch("app.graph.nodes.intent.get_llm")
    def test_full_query_pipeline_no_results(
        self, mock_get_llm, mock_session
    ):
        """测试查询流水线：无匹配结果时仍构建卡片"""
        from app.graph.bot_query_graph import build_query_graph
        from app.graph.state import QueryState

        # Mock: LLM 意图解析
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"vendor": "UnknownVendor", "days": 1}'
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        # Mock: 数据库无结果
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []

        mock_db = MagicMock()
        mock_db.query.return_value = mock_query
        mock_session.return_value = mock_db

        graph = build_query_graph()

        state: QueryState = {
            "user_id": "user_123",
            "chat_id": "chat_456",
            "user_query": "UnknownVendor 有什么消息",
        }

        result = graph.invoke(state)
        assert result.get("query_results", []) == []
        # 即使无结果，也应有一张"未找到"卡片
        card = result.get("reply_card_json", {})
        assert card != {}


class TestAdminEndpoints:
    """管理端点集成测试"""

    def test_health_endpoint(self):
        """测试健康检查端点"""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_trigger_rss_endpoint(self, monkeypatch):
        """测试手动触发 RSS 端点（mock 整个 process_rss_job）"""
        from fastapi.testclient import TestClient
        from unittest.mock import patch

        # /admin/* 需要鉴权，未配置令牌时端点整体禁用（fail-closed）
        monkeypatch.setenv("ADMIN_API_TOKEN", "test-admin-token")

        with patch("app.main.process_rss_job") as mock_job:
            mock_job.return_value = {"status": "ok", "processed": 3}
            from app.main import app

            client = TestClient(app)
            response = client.post(
                "/admin/trigger-rss", headers={"X-Admin-Token": "test-admin-token"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["processed"] == 3
