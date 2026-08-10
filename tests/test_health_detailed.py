"""/health 和 /health/detailed 端点测试

测试健康检查端点返回正确的状态字段和格式。
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


class TestHealth:
    """基础健康检查测试"""

    def test_liveness_check(self):
        """/health 返回 ok 状态"""
        from app.main import app
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_detailed_returns_json(self):
        """/health/detailed 返回 JSON 格式"""
        from app.main import app, _app_services
        # 确保 _app_services 中有 mock CB
        _app_services.clear()
        mock_cb = MagicMock()
        mock_cb.status = {"name": "feishu-api", "state": "closed", "failure_count": 0, "threshold": 5}
        _app_services["circuit_breaker"] = mock_cb

        client = TestClient(app)
        response = client.get("/health/detailed")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "database" in data
        assert "redis" in data
        assert "circuit_breaker" in data

    def test_detailed_includes_cb_fields(self):
        """/health/detailed 包含熔断器状态字段"""
        from app.main import app, _app_services
        _app_services.clear()
        mock_cb = MagicMock()
        mock_cb.status = {
            "name": "feishu-api",
            "state": "closed",
            "failure_count": 0,
            "threshold": 5,
        }
        _app_services["circuit_breaker"] = mock_cb

        client = TestClient(app)
        response = client.get("/health/detailed")
        data = response.json()
        assert data["circuit_breaker"]["status"] == "healthy"
        assert data["circuit_breaker"]["state"] == "closed"
        assert data["circuit_breaker"]["failure_count"] == 0

    def test_detailed_no_cb_returns_not_configured(self):
        """/health/detailed 在没有熔断器时返回 not_configured"""
        from app.main import app, _app_services
        _app_services.clear()

        client = TestClient(app)
        response = client.get("/health/detailed")
        data = response.json()
        assert data["circuit_breaker"]["status"] == "not_configured"

    @patch("app.main._check_database")
    def test_detailed_degraded_when_db_down(self, mock_db_check):
        """/health/detailed 数据库不健康时返回 degraded"""
        mock_db_check.return_value = {"status": "unhealthy", "error": "connection refused"}
        from app.main import app, _app_services
        _app_services.clear()
        mock_cb = MagicMock()
        mock_cb.status = {"name": "feishu-api", "state": "closed", "failure_count": 0, "threshold": 5}
        _app_services["circuit_breaker"] = mock_cb

        with patch("app.main._check_redis", return_value={"status": "healthy"}):
            client = TestClient(app)
            response = client.get("/health/detailed")
            data = response.json()
            assert data["status"] == "degraded"

    def test_detailed_all_healthy(self):
        """/health/detailed 所有组件健康时返回 healthy"""
        from app.main import app, _app_services
        _app_services.clear()
        mock_cb = MagicMock()
        mock_cb.status = {"name": "feishu-api", "state": "closed", "failure_count": 0, "threshold": 5}
        _app_services["circuit_breaker"] = mock_cb

        with patch("app.main._check_database", return_value={"status": "healthy"}):
            with patch("app.main._check_redis", return_value={"status": "healthy"}):
                client = TestClient(app)
                response = client.get("/health/detailed")
                data = response.json()
                assert data["status"] in ("ok", "healthy")
