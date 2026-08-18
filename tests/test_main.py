"""FastAPI 入口与调度器集成测试

测试健康检查、管理端点鉴权和 APScheduler 调度任务。
飞书事件通过 WebSocket 长连接接收，不再测试 webhook 端点。
"""
import pytest
from unittest.mock import patch, Mock, MagicMock


@pytest.fixture
def admin_token(monkeypatch):
    """配置管理端点令牌（verify_admin_token 在请求时新建 Settings，读环境变量生效）"""
    monkeypatch.setenv("ADMIN_API_TOKEN", "test-admin-token")
    return "test-admin-token"


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

    def test_metrics_endpoint(self):
        """测试 /metrics 端点返回 Prometheus 格式"""
        from app.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert b"feishu_bot_" in response.content

    @patch("app.main.process_rss_job")
    def test_manual_trigger_rss_endpoint(self, mock_job, admin_token):
        """测试手动触发 RSS 抓取端点（带合法令牌）"""
        mock_job.return_value = {"status": "ok", "processed": 3}

        from app.main import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.post("/admin/trigger-rss", headers={"X-Admin-Token": admin_token})
        assert response.status_code == 200
        assert response.json()["processed"] == 3


class TestAdminAuth:
    """管理端点鉴权测试

    /admin/* 可触发全量群发和批量 embedding 计费，必须鉴权且默认关闭。
    """

    ENDPOINTS = [
        "/admin/trigger-rss",
        "/admin/backfill-chromadb",
        "/admin/trigger-push",
        "/admin/test-card",
    ]

    def _client(self):
        from app.main import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_disabled_when_token_not_configured(self, path, monkeypatch):
        """未配置 ADMIN_API_TOKEN 时 fail-closed：整体禁用而非放行"""
        monkeypatch.setenv("ADMIN_API_TOKEN", "")
        response = self._client().post(path)
        assert response.status_code == 503
        assert "disabled" in response.json()["detail"].lower()

    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_missing_token_rejected(self, path, admin_token):
        """已配置令牌但请求不带 header → 401"""
        response = self._client().post(path)
        assert response.status_code == 401

    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_wrong_token_rejected(self, path, admin_token):
        """令牌错误 → 401"""
        response = self._client().post(path, headers={"X-Admin-Token": "wrong"})
        assert response.status_code == 401

    @patch("app.main.deliver_job")
    def test_valid_token_accepted(self, mock_deliver, admin_token):
        """合法令牌放行，业务逻辑被真正调用"""
        mock_deliver.return_value = {"status": "ok", "sent": 0}
        response = self._client().post(
            "/admin/trigger-push", headers={"X-Admin-Token": admin_token}
        )
        assert response.status_code == 200
        mock_deliver.assert_called_once()

    def test_health_endpoints_remain_public(self, admin_token):
        """健康检查和指标端点不受鉴权影响"""
        client = self._client()
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200


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
