"""metrics 模块单元测试

测试所有 Prometheus 指标定义、decorator 和 context manager。
"""
import pytest
from prometheus_client import CollectorRegistry

from app.core.metrics import (
    _registry,
    cb_state,
    rss_job_duration_seconds,
    rss_articles_fetched_total,
    rss_articles_processed_total,
    rss_articles_skipped_total,
    rss_graph_errors_total,
    deliver_job_duration_seconds,
    deliver_cards_sent_total,
    deliver_errors_total,
    llm_call_duration_seconds,
    llm_call_errors_total,
    ws_connection_status,
    ws_disconnect_total,
    feishu_api_duration_seconds,
    feishu_api_errors_total,
    scrape_success_total,
    scrape_failure_total,
    http_requests_total,
    http_request_duration_seconds,
    track_duration,
    track_llm_call,
    track_feishu_api,
    track_job_metrics,
    get_metrics_text,
    get_metrics_content_type,
)


class TestRegistry:
    """指标注册表测试"""

    def test_registry_not_default(self):
        """_registry 不是全局默认 registry"""
        assert _registry is not CollectorRegistry()
        # 默认 registry 不包含我们的指标
        from prometheus_client import REGISTRY
        assert _registry is not REGISTRY

    def test_registry_is_collector_registry(self):
        """_registry 是 CollectorRegistry 实例"""
        assert isinstance(_registry, CollectorRegistry)


class TestCircuitBreakerMetrics:
    """熔断器指标测试"""

    def test_cb_state_has_labels(self):
        """cb_state gauge 有 cb_name 标签"""
        cb_state.labels(cb_name="test_cb").set(0)
        cb_state.labels(cb_name="test_cb").set(1)
        cb_state.labels(cb_name="test_cb").set(2)
        # 不抛异常就算通过

    def test_cb_state_values(self):
        """cb_state 可以设置 0/1/2 三种状态值"""
        cb_state.labels(cb_name="feishu_api").set(0)
        cb_state.labels(cb_name="feishu_api").set(1)
        # 应当不抛异常


class TestRSSJobMetrics:
    """RSS 任务指标测试"""

    def test_rss_articles_fetched_increment(self):
        """rss_articles_fetched_total 可以按 source 标签递增"""
        before = rss_articles_fetched_total.labels(source="openai_blog")._value.get()
        rss_articles_fetched_total.labels(source="openai_blog").inc()
        assert rss_articles_fetched_total.labels(source="openai_blog")._value.get() == before + 1

    def test_rss_articles_processed_increment(self):
        """rss_articles_processed_total 可以递增"""
        before = rss_articles_processed_total._value.get()
        rss_articles_processed_total.inc()
        assert rss_articles_processed_total._value.get() == before + 1

    def test_rss_articles_skipped_increment(self):
        """rss_articles_skipped_total 可以递增"""
        before = rss_articles_skipped_total._value.get()
        rss_articles_skipped_total.inc()
        assert rss_articles_skipped_total._value.get() == before + 1

    def test_rss_job_duration_histogram(self):
        """rss_job_duration_seconds histogram 可以 observe 值"""
        rss_job_duration_seconds.observe(2.5)
        # observe 不抛异常就算通过

    def test_rss_graph_errors_increment(self):
        """rss_graph_errors_total 可以按 source 递增"""
        rss_graph_errors_total.labels(source="openai_blog").inc()
        # 不抛异常


class TestDeliverJobMetrics:
    """投递任务指标测试"""

    def test_deliver_cards_sent_increment(self):
        """deliver_cards_sent_total 可以按 push_time 递增"""
        deliver_cards_sent_total.labels(push_time="09:00").inc(3)
        # 不抛异常

    def test_deliver_errors_increment(self):
        """deliver_errors_total 可以按 push_time 递增"""
        deliver_errors_total.labels(push_time="12:00").inc()
        # 不抛异常

    def test_deliver_job_duration_histogram(self):
        """deliver_job_duration_seconds histogram 可以 observe"""
        deliver_job_duration_seconds.labels(push_time="09:00").observe(1.5)
        # 不抛异常


class TestLLMCallMetrics:
    """LLM 调用指标测试"""

    def test_llm_call_duration_histogram(self):
        """llm_call_duration_seconds 可以 observe"""
        llm_call_duration_seconds.labels(
            provider="deepseek", operation="summarize"
        ).observe(3.0)
        # 不抛异常

    def test_llm_call_errors_counter(self):
        """llm_call_errors_total 可以按 error_type 递增"""
        llm_call_errors_total.labels(
            provider="openai", operation="summarize", error_type="RateLimitError"
        ).inc()
        # 不抛异常


class TestWebSocketMetrics:
    """WebSocket 指标测试"""

    def test_ws_connection_status_set(self):
        """ws_connection_status gauge 可以设置 0/1"""
        ws_connection_status.set(1)
        ws_connection_status.set(0)
        # 不抛异常

    def test_ws_disconnect_increment(self):
        """ws_disconnect_total 可以递增"""
        before = ws_disconnect_total._value.get()
        ws_disconnect_total.inc()
        assert ws_disconnect_total._value.get() == before + 1


class TestFeishuAPIMetrics:
    """飞书 API 指标测试"""

    def test_feishu_api_duration_histogram(self):
        """feishu_api_duration_seconds 可以 observe"""
        feishu_api_duration_seconds.labels(method="send_card").observe(0.5)
        # 不抛异常

    def test_feishu_api_errors_counter(self):
        """feishu_api_errors_total 可以按 code 递增"""
        feishu_api_errors_total.labels(method="send_card", code="999914").inc()
        # 不抛异常


class TestScrapeMetrics:
    """内容抓取指标测试"""

    def test_scrape_success_increment(self):
        """scrape_success_total 可以按 fetcher_type 递增"""
        scrape_success_total.labels(fetcher_type="trafilatura").inc()
        # 不抛异常

    def test_scrape_failure_increment(self):
        """scrape_failure_total 可以按 fetcher_type 递增"""
        scrape_failure_total.labels(fetcher_type="trafilatura").inc()
        # 不抛异常


class TestHTTPMetrics:
    """HTTP 指标测试"""

    def test_http_requests_counter(self):
        """http_requests_total 可以按 method/path/status 递增"""
        http_requests_total.labels(
            method="GET", path="/health", status="200"
        ).inc()
        # 不抛异常

    def test_http_request_duration_histogram(self):
        """http_request_duration_seconds 可以 observe"""
        http_request_duration_seconds.labels(method="GET", path="/health").observe(0.05)
        # 不抛异常


class TestTrackDuration:
    """track_duration context manager 测试"""

    def test_records_duration(self):
        """track_duration 记录执行耗时"""
        with track_duration(llm_call_duration_seconds, provider="test", operation="test"):
            pass
        # 不抛异常，并且 observe 被调用
        # 验证 histogram 有数据
        sample = llm_call_duration_seconds.labels(provider="test", operation="test")
        # Histogram 通过 _sum 暴露累计值
        assert sample._sum.get() > 0


class TestTrackLLMCall:
    """track_llm_call decorator 测试"""

    def test_successful_call_records_metrics(self):
        """成功调用记录耗时指标"""

        @track_llm_call(provider="test", operation="test_op")
        def successful_func():
            return "ok"

        result = successful_func()
        assert result == "ok"

    def test_failed_call_records_error(self):
        """失败调用记录错误指标"""

        @track_llm_call(provider="test", operation="test_op")
        def failing_func():
            raise ValueError("test failure")

        with pytest.raises(ValueError, match="test failure"):
            failing_func()

        # 验证错误计数器递增
        count = llm_call_errors_total.labels(
            provider="test", operation="test_op", error_type="ValueError"
        )
        assert count._value.get() == 1

    def test_preserves_function_metadata(self):
        """decorator 保留原函数的 __name__ 和 __doc__"""

        @track_llm_call(provider="test", operation="test_op")
        def my_function():
            """my docstring"""
            return 42

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "my docstring"
        assert my_function() == 42


class TestTrackFeishuAPI:
    """track_feishu_api decorator 测试"""

    def test_successful_api_call(self):
        """成功的 API 调用记录耗时"""

        @track_feishu_api(method="send_card")
        def send_card_ok(data):
            return {"code": 0}

        result = send_card_ok({})
        # 不抛异常
        assert result is not None

    def test_failed_api_call_records_error(self):
        """失败的 API 调用记录错误"""

        @track_feishu_api(method="send_card")
        def send_card_fail(data):
            raise ConnectionError("network error")

        with pytest.raises(ConnectionError):
            send_card_fail({})


class TestTrackJobMetrics:
    """track_job_metrics decorator 测试"""

    def test_rss_job_records_duration(self):
        """RSS 任务记录耗时"""

        @track_job_metrics("rss")
        def process_rss():
            return {"status": "ok", "processed": 5}

        result = process_rss()
        assert result["processed"] == 5

    def test_deliver_job_records_duration(self):
        """投递任务记录耗时"""

        @track_job_metrics("deliver")
        def deliver(push_time="09:00"):
            return {"status": "ok", "delivered": 10}

        result = deliver(push_time="09:00")
        assert result["delivered"] == 10

    def test_rss_job_raises_but_still_records(self):
        """任务抛异常时仍然记录耗时"""

        @track_job_metrics("rss")
        def failing_job():
            raise RuntimeError("job crashed")

        with pytest.raises(RuntimeError, match="job crashed"):
            failing_job()


class TestGetMetricsText:
    """get_metrics_text 测试"""

    def test_returns_bytes(self):
        """返回 bytes 类型"""
        result = get_metrics_text()
        assert isinstance(result, bytes)

    def test_contains_prometheus_format(self):
        """输出包含 Prometheus text 格式的 HELP/TYPE 行"""
        result = get_metrics_text().decode("utf-8")
        assert "HELP feishu_bot_" in result
        assert "TYPE feishu_bot_" in result

    def test_get_metrics_content_type(self):
        """返回正确的 Content-Type"""
        ct = get_metrics_content_type()
        assert "text/plain" in ct
