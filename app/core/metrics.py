"""Prometheus 指标定义模块（项目内所有指标的唯一来源）

通过 prometheus_client 定义 Counter / Gauge / Histogram。
指标命名遵循 OpenMetrics 规范：namespace_subsystem_name_unit

设计原则：
- 所有指标集中定义在此模块，禁止在其他模块直接 import prometheus_client
- 指标注册使用全局单例 CollectorRegistry，避免污染全局默认 registry
- 提供 decorator 和 context manager 用于便捷埋点，不污染业务代码
"""
import time
import functools
from contextlib import contextmanager
from typing import Callable, TypeVar

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST

T = TypeVar("T")

# 独立 registry，不污染全局默认 registry
_registry = CollectorRegistry()

# ========== Circuit Breaker ==========

cb_state = Gauge(
    "feishu_bot_circuit_breaker_state",
    "Circuit breaker state: 0=CLOSED, 1=OPEN, 2=HALF_OPEN",
    ["cb_name"],
    registry=_registry,
)

# ========== RSS / Fetch Job ==========

rss_job_duration_seconds = Histogram(
    "feishu_bot_rss_job_duration_seconds",
    "RSS job total duration in seconds",
    registry=_registry,
)

rss_articles_fetched_total = Counter(
    "feishu_bot_rss_articles_fetched_total",
    "Total articles fetched from upstream sources",
    ["source"],
    registry=_registry,
)

rss_articles_processed_total = Counter(
    "feishu_bot_rss_articles_processed_total",
    "Total articles successfully processed through pipeline",
    registry=_registry,
)

rss_articles_skipped_total = Counter(
    "feishu_bot_rss_articles_skipped_total",
    "Total articles skipped (already processed or keyword filter)",
    registry=_registry,
)

rss_graph_errors_total = Counter(
    "feishu_bot_rss_graph_errors_total",
    "Total unhandled exceptions from LangGraph runtime",
    ["source"],
    registry=_registry,
)

# ========== Deliver Job ==========

deliver_job_duration_seconds = Histogram(
    "feishu_bot_deliver_job_duration_seconds",
    "Deliver job total duration in seconds",
    ["push_time"],
    registry=_registry,
)

deliver_cards_sent_total = Counter(
    "feishu_bot_deliver_cards_sent_total",
    "Total cards sent during deliver job, by push time and platform",
    ["push_time", "platform"],
    registry=_registry,
)

deliver_errors_total = Counter(
    "feishu_bot_deliver_errors_total",
    "Total errors during deliver job, by push time and platform",
    ["push_time", "platform"],
    registry=_registry,
)

# ========== Redis Stream 推送队列 ==========

deliver_queue_depth = Gauge(
    "feishu_bot_deliver_queue_depth",
    "当前推送 Stream 的消息条数（XLEN）",
    registry=_registry,
)

deliver_pending_messages = Gauge(
    "feishu_bot_deliver_pending_messages",
    "consumer group 已投递未 ACK 的消息数（XPENDING）",
    registry=_registry,
)

deliver_enqueued_total = Counter(
    "feishu_bot_deliver_enqueued_total",
    "入队的推送消息总数",
    registry=_registry,
)

deliver_consumed_total = Counter(
    "feishu_bot_deliver_consumed_total",
    "消费并成功发送的推送消息总数",
    ["platform"],
    registry=_registry,
)

deliver_retry_total = Counter(
    "feishu_bot_deliver_retry_total",
    "被 XAUTOCLAIM 重投的消息总数",
    registry=_registry,
)

deliver_dlq_total = Counter(
    "feishu_bot_deliver_dlq_total",
    "超过最大重试次数进入死信队列的消息总数",
    registry=_registry,
)

queue_fallback_total = Counter(
    "feishu_bot_queue_fallback_total",
    "Redis 不可用时降级为内联同步发送的次数",
    registry=_registry,
)

# ========== LLM Calls ==========

llm_call_duration_seconds = Histogram(
    "feishu_bot_llm_call_duration_seconds",
    "LLM API call latency in seconds",
    ["provider", "operation"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
    registry=_registry,
)

llm_call_errors_total = Counter(
    "feishu_bot_llm_call_errors_total",
    "Total LLM call errors",
    ["provider", "operation", "error_type"],
    registry=_registry,
)

# ========== WebSocket ==========

ws_connection_status = Gauge(
    "feishu_bot_ws_connection_status",
    "WebSocket connection status: 1=connected, 0=disconnected",
    registry=_registry,
)

ws_disconnect_total = Counter(
    "feishu_bot_ws_disconnect_total",
    "Total WebSocket disconnections",
    registry=_registry,
)

# ========== Feishu API ==========

feishu_api_duration_seconds = Histogram(
    "feishu_bot_feishu_api_duration_seconds",
    "Feishu API call latency in seconds",
    ["method"],
    registry=_registry,
)

feishu_api_errors_total = Counter(
    "feishu_bot_feishu_api_errors_total",
    "Feishu API call errors",
    ["method", "code"],
    registry=_registry,
)

# ========== Platform Message Sends（多平台统一发送埋点）==========
# 装饰各 PlatformAdapter 的 send_message，使 Feishu / Telegram / Discord
# 的发送量、延迟、错误以统一的 platform 标签维度可观测。

platform_message_sent_total = Counter(
    "feishu_bot_platform_message_sent_total",
    "Total messages successfully sent via platform adapters",
    ["platform"],
    registry=_registry,
)

platform_message_errors_total = Counter(
    "feishu_bot_platform_message_errors_total",
    "Total platform message send errors",
    ["platform", "error_type"],
    registry=_registry,
)

platform_message_duration_seconds = Histogram(
    "feishu_bot_platform_message_duration_seconds",
    "Platform message send latency in seconds",
    ["platform"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
    registry=_registry,
)

# ========== Content Scraping ==========

scrape_success_total = Counter(
    "feishu_bot_scrape_success_total",
    "Total successful content scrapes",
    ["fetcher_type"],
    registry=_registry,
)

scrape_failure_total = Counter(
    "feishu_bot_scrape_failure_total",
    "Total failed content scrapes",
    ["fetcher_type"],
    registry=_registry,
)

# ========== RAG (Retrieval-Augmented Generation) ==========

rag_embed_duration_seconds = Histogram(
    "feishu_bot_rag_embed_duration_seconds",
    "RAG embedding API call latency in seconds",
    registry=_registry,
)

rag_embed_total = Counter(
    "feishu_bot_rag_embed_total",
    "Total RAG embedding calls",
    registry=_registry,
)

rag_embed_errors_total = Counter(
    "feishu_bot_rag_embed_errors_total",
    "Total RAG embedding call errors",
    registry=_registry,
)

rag_retrieve_duration_seconds = Histogram(
    "feishu_bot_rag_retrieve_duration_seconds",
    "RAG semantic retrieval latency in seconds",
    registry=_registry,
)

rag_answer_duration_seconds = Histogram(
    "feishu_bot_rag_answer_duration_seconds",
    "RAG LLM answer generation latency in seconds",
    registry=_registry,
)

rag_query_total = Counter(
    "feishu_bot_rag_query_total",
    "Total RAG queries by type",
    ["query_type"],
    registry=_registry,
)

# ========== Query Executor（并发查询池）==========

query_queue_depth = Gauge(
    "feishu_bot_query_queue_depth",
    "Number of queries currently pending (queued + active) in the bounded query pool",
    registry=_registry,
)

query_workers_busy = Gauge(
    "feishu_bot_query_workers_busy",
    "Number of query workers currently busy executing a task",
    registry=_registry,
)

query_dropped_total = Counter(
    "feishu_bot_query_dropped_total",
    "Total queries dropped before execution (queue full or rate limited)",
    ["reason"],
    registry=_registry,
)

query_processed_total = Counter(
    "feishu_bot_query_processed_total",
    "Total queries successfully processed by the query pool",
    registry=_registry,
)

query_queue_wait_seconds = Histogram(
    "feishu_bot_query_queue_wait_seconds",
    "Time a query waited in the queue before execution started",
    registry=_registry,
)

# ========== HTTP Middleware ==========

http_requests_total = Counter(
    "feishu_bot_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
    registry=_registry,
)

http_request_duration_seconds = Histogram(
    "feishu_bot_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    registry=_registry,
)

# ========== Multi-Level Cache ==========

cache_hit_total = Counter(
    "feishu_bot_cache_hit_total",
    "Total cache hits by cache namespace and level (l1/l2)",
    ["cache", "level"],
    registry=_registry,
)

cache_miss_total = Counter(
    "feishu_bot_cache_miss_total",
    "Total cache misses by cache namespace",
    ["cache"],
    registry=_registry,
)

# ========== Service Governance (熔断 / 降级) ==========

degraded_requests_total = Counter(
    "feishu_bot_degraded_requests_total",
    "Total requests served by degraded fallback path (LLM 不可用时的关键词/列表兜底)",
    ["path"],  # router | intent | rag_answer
    registry=_registry,
)

# ========== Decorators 和 Context Managers ==========


@contextmanager
def track_duration(histogram: Histogram, **labels):
    """记录代码块执行耗时。

    用法:
        with track_duration(llm_call_duration_seconds, provider="openai", operation="summarize"):
            result = do_work()
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        if labels:
            histogram.labels(**labels).observe(elapsed)
        else:
            histogram.observe(elapsed)


def track_llm_call(provider: str, operation: str):
    """LLM 调用埋点 decorator，自动记录耗时和错误次数。

    用法:
        @retry(stop=stop_after_attempt(3), wait=wait_exponential())
        @track_llm_call(provider="deepseek", operation="summarize")
        def _call_llm_summarize(llm, prompt: str) -> str:
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                llm_call_duration_seconds.labels(
                    provider=provider, operation=operation
                ).observe(elapsed)
                return result
            except Exception as e:
                elapsed = time.perf_counter() - start
                llm_call_duration_seconds.labels(
                    provider=provider, operation=operation
                ).observe(elapsed)
                llm_call_errors_total.labels(
                    provider=provider,
                    operation=operation,
                    error_type=type(e).__name__,
                ).inc()
                raise
        return wrapper
    return decorator


def track_feishu_api(method: str):
    """飞书 API 调用埋点 decorator，记录耗时和按错误码分类的错误。

    用法:
        @track_feishu_api("send_card")
        def _send_card_impl(self, request):
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                feishu_api_duration_seconds.labels(method=method).observe(elapsed)
                return result
            except Exception as e:
                elapsed = time.perf_counter() - start
                feishu_api_duration_seconds.labels(method=method).observe(elapsed)
                feishu_api_errors_total.labels(
                    method=method, code=type(e).__name__
                ).inc()
                raise
        return wrapper
    return decorator


def track_platform_send(platform: str):
    """平台消息发送埋点 decorator，统一记录耗时、成功与错误。

    装饰各 PlatformAdapter 的 send_message（均为同步方法）。
    成功返回即记 sent_total；抛异常记 errors_total 后重新上抛（不吞异常）。

    用法:
        @track_platform_send("telegram")
        def send_message(self, conversation_id, message):
            ...
    """
    def decorator(func: Callable[..., dict]) -> Callable[..., dict]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> dict:
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                platform_message_duration_seconds.labels(platform=platform).observe(elapsed)
                platform_message_sent_total.labels(platform=platform).inc()
                return result
            except Exception as e:
                elapsed = time.perf_counter() - start
                platform_message_duration_seconds.labels(platform=platform).observe(elapsed)
                platform_message_errors_total.labels(
                    platform=platform,
                    error_type=type(e).__name__,
                ).inc()
                raise
        return wrapper
    return decorator


def track_job_metrics(job_type: str):
    """定时任务埋点 decorator，记录总耗时和成功/失败。

    job_type: "rss" | "deliver"

    被装饰函数的返回值应包含 {"status": "ok", "processed": N} 或 {"status": "ok", "delivered": N}。
    """
    def decorator(func: Callable[..., dict]) -> Callable[..., dict]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> dict:
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                if job_type == "rss":
                    rss_job_duration_seconds.observe(elapsed)
                elif job_type == "deliver":
                    push_time = kwargs.get("push_time", args[0] if args else "09:00")
                    deliver_job_duration_seconds.labels(push_time=push_time).observe(elapsed)
                return result
            except Exception:
                elapsed = time.perf_counter() - start
                if job_type == "rss":
                    rss_job_duration_seconds.observe(elapsed)
                elif job_type == "deliver":
                    push_time = kwargs.get("push_time", args[0] if args else "09:00")
                    deliver_job_duration_seconds.labels(push_time=push_time).observe(elapsed)
                raise
        return wrapper
    return decorator


# ========== Registry 访问 ==========


def init_metrics() -> None:
    """初始化所有指标为零值，确保 Prometheus 抓取时所有指标都存在。

    在应用启动时调用一次。Prometheus 只在指标首次有数据时才注册，
    未初始化的指标在 Grafana 中会显示 "No data"。
    """
    # Circuit Breaker
    cb_state.labels(cb_name="feishu-api").set(0)

    # RSS Job
    rss_articles_processed_total.inc(0)
    rss_articles_skipped_total.inc(0)

    # Deliver Job（3 个推送时段 × 3 个平台）
    for _pt in ("09:00", "12:00", "18:00"):
        for _pf in ("feishu", "telegram", "discord"):
            deliver_cards_sent_total.labels(push_time=_pt, platform=_pf).inc(0)
            deliver_errors_total.labels(push_time=_pt, platform=_pf).inc(0)

    # Redis Stream 推送队列
    deliver_queue_depth.set(0)
    deliver_pending_messages.set(0)
    deliver_enqueued_total.inc(0)
    deliver_retry_total.inc(0)
    deliver_dlq_total.inc(0)
    queue_fallback_total.inc(0)
    for _pf in ("feishu", "telegram", "discord"):
        deliver_consumed_total.labels(platform=_pf).inc(0)

    # WebSocket
    ws_connection_status.set(0)
    ws_disconnect_total.inc(0)

    # Feishu API
    feishu_api_duration_seconds.labels(method="send_card").observe(0)
    feishu_api_duration_seconds.labels(method="get_chat_info").observe(0)

    # Scraping
    scrape_success_total.labels(fetcher_type="trafilatura").inc(0)
    scrape_failure_total.labels(fetcher_type="trafilatura").inc(0)

    # LLM
    llm_call_duration_seconds.labels(provider="auto", operation="summarize").observe(0)
    llm_call_duration_seconds.labels(provider="auto", operation="intent").observe(0)

    # RAG
    rag_embed_total.inc(0)
    rag_embed_errors_total.inc(0)
    rag_query_total.labels(query_type="list").inc(0)
    rag_query_total.labels(query_type="qa").inc(0)

    # Query Executor
    query_queue_depth.set(0)
    query_workers_busy.set(0)
    query_dropped_total.labels(reason="queue_full").inc(0)
    query_dropped_total.labels(reason="rate_limited").inc(0)
    query_processed_total.inc(0)
    query_queue_wait_seconds.observe(0)

    # Platform Message Sends（3 个平台）
    for _pf in ("feishu", "telegram", "discord"):
        platform_message_sent_total.labels(platform=_pf).inc(0)
        platform_message_duration_seconds.labels(platform=_pf).observe(0)

    # Multi-Level Cache（3 个命名空间 × 2 层）
    for _ns in ("llm", "embed", "db"):
        cache_hit_total.labels(cache=_ns, level="l1").inc(0)
        cache_hit_total.labels(cache=_ns, level="l2").inc(0)
        cache_miss_total.labels(cache=_ns).inc(0)

    # 降级路径（LLM 熔断/失败时的兜底）
    for _path in ("router", "intent", "rag_answer"):
        degraded_requests_total.labels(path=_path).inc(0)

    # LLM 熔断器初始 CLOSED 状态
    cb_state.labels(cb_name="llm").set(0)


def get_metrics_text() -> bytes:
    """返回 Prometheus text 格式的指标数据"""
    return generate_latest(_registry)


def get_metrics_content_type() -> str:
    """返回 Prometheus 指标的 Content-Type"""
    return CONTENT_TYPE_LATEST
