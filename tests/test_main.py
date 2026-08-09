"""FastAPI 入口与调度器集成测试

测试健康检查、管理端点和 APScheduler 调度任务。
飞书事件通过 WebSocket 长连接接收，不再测试 webhook 端点。
"""
import pytest
from unittest.mock import patch, Mock, MagicMock


class TestEndpoints:
    """管理端点测试"""

    def test_health_check(self):
        """测试健康检查端点"""
        from app.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @patch("app.main.process_rss_job")
    def test_manual_trigger_rss_endpoint(self, mock_job):
        """测试手动触发 RSS 抓取端点"""
        mock_job.return_value = {"status": "ok", "processed": 3}

        from app.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.post("/admin/trigger-rss")
        assert response.status_code == 200
        assert response.json()["processed"] == 3


class TestRssJob:
    """RSS 定时任务测试（基于 LangGraph 流水线）"""

    @patch("app.graph.news_push_graph.build_push_graph")
    @patch("app.main.RedisClient")
    @patch("app.fetcher.kimi_scraper.fetch_kimi_articles")
    @patch("app.fetcher.rss_fetcher.fetch_rss_items")
    def test_process_rss_job_new_articles(
        self, mock_fetch, mock_kimi, mock_redis, mock_build_graph
    ):
        """测试 RSS job 发现新文章时通过 Graph 流水线处理"""
        mock_kimi.return_value = []

        # Mock: RSS 源返回一篇新文章
        mock_fetch.side_effect = [
            [{"title": "New Article", "url": "https://example.com/new", "published_at": "2026-08-07"}],
            [], [], [], [], [], [], [], [],
        ]

        # Mock: Redis 返回未处理
        mock_redis_instance = MagicMock()
        mock_redis_instance.is_url_processed.return_value = False
        mock_redis.return_value = mock_redis_instance

        # Mock: Graph 流水线返回 SUCCESS
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {"status": "SUCCESS"}
        mock_build_graph.return_value = mock_graph

        from app.main import process_rss_job

        result = process_rss_job()
        assert result["status"] == "ok"
        assert result["processed"] == 1
        mock_graph.invoke.assert_called_once()

    @patch("app.graph.news_push_graph.build_push_graph")
    @patch("app.main.RedisClient")
    @patch("app.fetcher.kimi_scraper.fetch_kimi_articles")
    @patch("app.fetcher.rss_fetcher.fetch_rss_items")
    def test_process_rss_job_graph_failure(
        self, mock_fetch, mock_kimi, mock_redis, mock_build_graph
    ):
        """测试 RSS job 中 Graph 处理失败时不计数"""
        mock_kimi.return_value = []
        mock_fetch.side_effect = [
            [{"title": "Bad Article", "url": "https://example.com/bad", "published_at": ""}],
            [], [], [], [], [], [], [], [],
        ]

        mock_redis_instance = MagicMock()
        mock_redis_instance.is_url_processed.return_value = False
        mock_redis.return_value = mock_redis_instance

        # Mock: Graph 流水线返回 FAILED
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {"status": "FAILED"}
        mock_build_graph.return_value = mock_graph

        from app.main import process_rss_job

        result = process_rss_job()
        assert result["status"] == "ok"
        assert result["processed"] == 0

    @patch("app.graph.news_push_graph.build_push_graph")
    @patch("app.main.RedisClient")
    @patch("app.fetcher.kimi_scraper.fetch_kimi_articles")
    @patch("app.fetcher.rss_fetcher.fetch_rss_items")
    def test_process_rss_job_no_new_articles(
        self, mock_fetch, mock_kimi, mock_redis, mock_build_graph
    ):
        """测试 RSS job 无新文章时 Graph 不被调用"""
        mock_fetch.side_effect = [[], [], [], [], [], [], [], [], []]
        mock_kimi.return_value = []

        mock_redis_instance = MagicMock()
        mock_redis.return_value = mock_redis_instance

        mock_graph = MagicMock()
        mock_build_graph.return_value = mock_graph

        from app.main import process_rss_job

        result = process_rss_job()
        assert result["status"] == "ok"
        assert result["processed"] == 0
        mock_graph.invoke.assert_not_called()

    @patch("app.graph.news_push_graph.build_push_graph")
    @patch("app.main.RedisClient")
    @patch("app.fetcher.kimi_scraper.fetch_kimi_articles")
    @patch("app.fetcher.rss_fetcher.fetch_rss_items")
    def test_process_rss_job_url_already_processed(
        self, mock_fetch, mock_kimi, mock_redis, mock_build_graph
    ):
        """测试已处理的 URL 被跳过"""
        mock_kimi.return_value = []
        mock_fetch.side_effect = [
            [{"title": "Old News", "url": "https://example.com/old", "published_at": ""}],
            [], [], [], [], [], [], [], [],
        ]

        mock_redis_instance = MagicMock()
        mock_redis_instance.is_url_processed.return_value = True
        mock_redis.return_value = mock_redis_instance

        mock_graph = MagicMock()
        mock_build_graph.return_value = mock_graph

        from app.main import process_rss_job

        result = process_rss_job()
        assert result["status"] == "ok"
        assert result["processed"] == 0
        mock_graph.invoke.assert_not_called()
