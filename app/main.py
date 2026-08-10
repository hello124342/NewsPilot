"""FastAPI 主入口

飞书事件通过 WebSocket 长连接接收（lark-oapi SDK），FastAPI 仅保留
健康检查、管理接口和 APScheduler 调度任务。

架构：
- WebSocket 长连接（daemon 线程）→ app/feishu/event_router.py → 业务逻辑
- FastAPI → /health, /admin/trigger-rss
- APScheduler → 5:00 抓取存储 + 9:00/12:00/18:00 投递
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.core.logging_config import setup_logging
from app.db.redis import RedisClient
import app.db.database as database
from app.graph.state import PushState

# 结构化日志：JSON 格式输出，LOG_LEVEL 通过环境变量控制
setup_logging(Settings())
logger = logging.getLogger(__name__)

# ========== 信源配置 ==========
# 每项: {vendor, channel ("Blog"|"Twitter"), url, fetcher ("rss"|"kimi"), filter (可选关键词)}
# Twitter 通过 Nitter RSS 桥接获取
SOURCES = [
    # --- OpenAI ---
    {"vendor": "OpenAI", "channel": "Blog",    "url": "https://openai.com/blog/rss.xml", "fetcher": "rss"},
    {"vendor": "OpenAI", "channel": "Twitter", "url": "https://nitter.net/OpenAI/rss", "fetcher": "rss"},
    # --- Anthropic ---
    {"vendor": "Anthropic", "channel": "Blog",    "url": "https://www.anthropic.com/blog/rss.xml", "fetcher": "rss"},
    {"vendor": "Anthropic", "channel": "Twitter", "url": "https://nitter.net/AnthropicAI/rss", "fetcher": "rss"},
    # --- Google DeepMind ---
    {"vendor": "Google DeepMind", "channel": "Blog",    "url": "https://blog.google/technology/ai/rss/", "fetcher": "rss"},
    {"vendor": "Google DeepMind", "channel": "Twitter", "url": "https://nitter.net/GoogleDeepMind/rss", "fetcher": "rss"},
    # --- DeepSeek: 仅 Twitter（Blog 为 SPA 渲染，不可抓取） ---
    {"vendor": "DeepSeek", "channel": "Twitter", "url": "https://nitter.net/deepseek_ai/rss", "fetcher": "rss"},
    # --- Kimi (Moonshot) ---
    {"vendor": "Kimi (Moonshot)", "channel": "Blog",    "url": "https://www.kimi.com/blog/", "fetcher": "kimi"},
    {"vendor": "Kimi (Moonshot)", "channel": "Twitter", "url": "https://nitter.net/MoonshotAI/rss", "fetcher": "rss"},
    # --- Z.ai / 智谱: 仅 Twitter（无官方 Blog） ---
    {"vendor": "Z.ai / 智谱", "channel": "Twitter", "url": "https://nitter.net/zhipuai/rss", "fetcher": "rss"},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：初始化 DB、启动调度器和 WebSocket 长连接"""
    settings = Settings()  # type: ignore[call-arg]
    database.init_db(settings)
    from app.db.models import Base
    Base.metadata.create_all(bind=database.engine)
    logger.info("Database initialized: tables created")

    # 初始化 Prometheus 指标为零值（确保 Grafana 中不显示 "No data"）
    from app.core.metrics import init_metrics
    init_metrics()

    # --- 启动 APScheduler ---
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()

    # 5:00 AM — 抓取+总结+存储（不推送）
    scheduler.add_job(
        func=schedule_rss_polling,
        trigger="cron",
        hour=5,
        minute=0,
        id="rss_process",
        replace_existing=True,
    )

    # 9:00 / 12:00 / 18:00 — 按用户偏好推送
    for deliver_hour in [9, 12, 18]:
        scheduler.add_job(
            func=lambda h=deliver_hour: deliver_job(f"{h:02d}:00"),
            trigger="cron",
            hour=deliver_hour,
            minute=0,
            id=f"deliver_{deliver_hour:02d}00",
            replace_existing=True,
        )

    scheduler.start()
    logger.info("APScheduler started: process at 05:00, deliver at 09:00/12:00/18:00")

    # --- 启动飞书 WebSocket 长连接（独立 daemon 线程） ---
    from app.feishu.client import FeishuClient
    from app.feishu.event_router import build_event_handler
    from app.feishu.ws_client import start_ws_thread
    from app.core.resilience import CircuitBreaker

    # 为飞书 API 调用创建熔断器（5 次连续失败 → 熔断 60s）
    feishu_cb = CircuitBreaker(name="feishu-api", failure_threshold=5, recovery_timeout=60.0)
    feishu = FeishuClient(settings, circuit_breaker=feishu_cb)
    event_handler = build_event_handler(feishu)

    ws_thread = start_ws_thread(
        app_id=settings.FEISHU_APP_ID,
        app_secret=settings.FEISHU_APP_SECRET,
        event_handler=event_handler,
    )

    # 注册到 /health 可访问的模块级引用
    _app_services["circuit_breaker"] = feishu_cb
    _app_services["ws_thread"] = ws_thread
    logger.info(f"Feishu WS long connection started (thread: {ws_thread.name})")

    yield
    scheduler.shutdown()
    # 优雅关闭事件处理线程池（等待正在处理的请求完成）
    from app.feishu.event_router import shutdown_executor
    shutdown_executor(wait=True)
    logger.info("Scheduler and event executor shut down (WS thread will exit as daemon)")


app = FastAPI(title="Feishu AI News Bot", version="0.2.0", lifespan=lifespan)


# ========== 可观测性：HTTP 指标中间件 ==========

import time as _time
from app.core.metrics import http_requests_total, http_request_duration_seconds


@app.middleware("http")
async def metrics_middleware(request, call_next):
    """统计 HTTP 请求数和延迟，排除 /metrics 自身"""
    path = request.url.path
    if path == "/metrics":
        return await call_next(request)
    start = _time.perf_counter()
    response = await call_next(request)
    elapsed = _time.perf_counter() - start
    http_requests_total.labels(
        method=request.method, path=path, status=str(response.status_code)
    ).inc()
    http_request_duration_seconds.labels(method=request.method, path=path).observe(elapsed)
    return response


@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点（text/plain 格式）"""
    from app.core.metrics import get_metrics_text, get_metrics_content_type
    from fastapi.responses import Response
    return Response(content=get_metrics_text(), media_type=get_metrics_content_type())


# ========== 健康检查端点 ==========

# 模块级引用：lifecycle 期间设置，供 /health 端点读取
_app_services: dict = {}


@app.get("/health")
async def health():
    """健康检查（Kubernetes liveness probe）"""
    return {"status": "ok"}


@app.get("/health/detailed")
async def health_detailed():
    """详细健康检查：数据库、Redis、熔断器、WebSocket 线程状态"""
    result = {
        "status": "ok",
        "database": _check_database(),
        "redis": _check_redis(),
        "circuit_breaker": _check_circuit_breaker(),
    }

    # 如果任何组件不健康，整体状态设为 degraded
    checks = [result["database"], result["redis"], result["circuit_breaker"]]
    if any(c["status"] != "healthy" for c in checks if isinstance(c, dict)):
        result["status"] = "degraded"

    return result


def _check_database() -> dict:
    """检查 MySQL 数据库连接"""
    try:
        from app.db.database import engine
        with engine.connect() as conn:
            conn.execute(engine.dialect.do_ping(None))
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def _check_redis() -> dict:
    """检查 Redis 连接"""
    try:
        from app.db.redis import RedisClient
        from app.core.config import Settings
        settings = Settings()
        redis = RedisClient(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
        if redis._client.ping():
            return {"status": "healthy"}
        return {"status": "unhealthy", "error": "ping failed"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def _check_circuit_breaker() -> dict:
    """返回熔断器状态"""
    cb = _app_services.get("circuit_breaker")
    if cb is None:
        return {"status": "not_configured"}
    return {"status": "healthy", **cb.status}


@app.post("/admin/trigger-rss")
async def trigger_rss():
    """手动触发 RSS 抓取（便于调试）"""
    result = process_rss_job()
    return JSONResponse(result)


@app.post("/admin/trigger-push")
async def trigger_push(time: str = "09:00", limit: int = 0):
    """手动触发推送（便于调试）。limit=N 只发前 N 篇文章，默认 0=全部。"""
    result = deliver_job(time, limit=limit)
    return JSONResponse(result)


@app.post("/admin/test-card")
async def test_card(chat_id: str = ""):
    """发送一张测试卡片，验证按钮点击（便于调试）"""
    from app.feishu.card_builder import build_news_card
    from app.feishu.client import FeishuClient

    settings = Settings()  # type: ignore[call-arg]
    feishu = FeishuClient(settings)

    card = build_news_card(
        title="这是一篇测试文章",
        vendor="OpenAI",
        summary_points=["要点一：测试推送功能", "要点二：验证按钮点击", "要点三：确认无 200671 错误"],
        raw_url="https://openai.com/blog",
        published_at="2026-08-09",
        channel="Blog",
    )
    feishu.send_card(chat_id, card)
    return {"status": "ok", "chat_id": chat_id}


# ========== RSS 处理任务 ==========

def process_rss_job() -> dict:
    """执行一次 RSS 轮询→抓取→总结→推送的完整流程

    遍历配置的 RSS 源，对每篇新文章通过 NewsPushGraph 流水线处理：
    extract → summarize → store → build_card → send_feishu

    Returns:
        {"status": "ok", "processed": N} 或 {"status": "error", "message": str}
    """
    import time as _time
    from app.fetcher.rss_fetcher import fetch_rss_items
    from app.fetcher.kimi_scraper import fetch_kimi_articles
    from app.graph.news_push_graph import build_push_graph
    from app.core.metrics import (
        rss_articles_fetched_total, rss_articles_processed_total,
        rss_articles_skipped_total, rss_graph_errors_total, rss_job_duration_seconds,
    )

    start_time = _time.perf_counter()
    settings = Settings()  # type: ignore[call-arg]
    redis = RedisClient(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    graph = build_push_graph(push_enabled=False)  # 仅抓取+存储，不推送
    processed_count = 0

    for source in SOURCES:
        rss_url = source["url"]
        fetcher_type = source.get("fetcher", "rss")
        vendor = source["vendor"]
        channel = source.get("channel", "Blog")
        keywords = source.get("filter")
        source_label = f"{vendor}_{channel}"

        # 根据 fetcher 类型拉取文章列表
        if fetcher_type == "kimi":
            articles = fetch_kimi_articles()
        else:
            articles = fetch_rss_items(rss_url)

        rss_articles_fetched_total.labels(source=source_label).inc(len(articles))
        logger.info(f"[{vendor}] {channel}: fetched {len(articles)} articles from {rss_url}")

        for article in articles:
            url = article["url"]
            if not url or redis.is_url_processed(url):
                rss_articles_skipped_total.inc()
                continue

            # 关键词过滤
            if keywords:
                title_lower = article["title"].lower()
                if not any(kw.lower() in title_lower for kw in keywords):
                    rss_articles_skipped_total.inc()
                    continue

            # 构建初始状态，通过 LangGraph 流水线处理
            state: PushState = {
                "raw_url": url,
                "vendor": vendor,
                "title": article["title"],
                "published_at": article.get("published_at", ""),
                "channel": channel,
                "rss_summary": article.get("summary", ""),
                "status": "PENDING",
            }

            try:
                result = graph.invoke(state)
                if result.get("status") == "SUCCESS":
                    processed_count += 1
                    rss_articles_processed_total.inc()
                else:
                    logger.warning(
                        f"Pipeline failed for: {article['title'][:50]} "
                        f"(status={result.get('status', 'UNKNOWN')})"
                    )
            except Exception as e:
                logger.error(f"Graph invocation failed for {url}: {e}")
                rss_graph_errors_total.labels(source=source_label).inc()
                continue

    elapsed = _time.perf_counter() - start_time
    rss_job_duration_seconds.observe(elapsed)
    logger.info(f"RSS job complete: {processed_count} articles processed in {elapsed:.1f}s")
    return {"status": "ok", "processed": processed_count}


def schedule_rss_polling():
    """调度器回调入口（仅抓取+存储，不推送）"""
    return process_rss_job()


def deliver_job(push_time: str, limit: int = 0) -> dict:
    """按用户偏好推送今日已存储的文章

    Args:
        push_time: 当前推送时间窗口（"09:00" / "12:00" / "18:00"）

    逻辑：
    1. 查询今日存储的所有文章
    2. 对每篇文章，按 推送时间 → 频率 → 订阅 三层过滤后推送
    """
    import time as _time
    from datetime import date, datetime, timezone
    from app.db.database import SessionLocal
    from app.db.models import NewsArticle
    from app.subscription.handler import (
        get_preference, get_subscribers, has_any_subscription,
        is_today_in_frequency,
    )
    from app.feishu.card_builder import build_news_card
    from app.feishu.client import FeishuClient
    from app.chat.lifecycle import get_active_chat_ids
    from app.core.metrics import deliver_cards_sent_total, deliver_errors_total, deliver_job_duration_seconds

    start_time = _time.perf_counter()
    settings = Settings()  # type: ignore[call-arg]

    chat_ids = get_active_chat_ids()
    if not chat_ids:
        logger.info(f"deliver_job({push_time}): no active chats")
        deliver_job_duration_seconds.labels(push_time=push_time).observe(_time.perf_counter() - start_time)
        return {"status": "ok", "delivered": 0}
    logger.info(f"deliver_job({push_time}): targeting {len(chat_ids)} active chat(s)")

    feishu = FeishuClient(settings)

    # 查询今日文章
    db = SessionLocal()
    try:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        articles = (
            db.query(NewsArticle)
            .filter(NewsArticle.created_at >= today_start)
            .order_by(NewsArticle.created_at.desc())
            .all()
        )
    except Exception as e:
        logger.error(f"deliver_job query failed: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

    if not articles:
        logger.info(f"deliver_job({push_time}): no articles to deliver")
        deliver_job_duration_seconds.labels(push_time=push_time).observe(_time.perf_counter() - start_time)
        return {"status": "ok", "delivered": 0}

    delivered = 0
    for article in articles:
        vendor = article.vendor

        for chat_id in chat_ids:
            # 过滤 1: 推送时间匹配
            pref = get_preference(chat_id)
            if pref["push_time"] != push_time:
                continue

            # 过滤 2: 频率匹配
            if not is_today_in_frequency(pref["frequency"]):
                continue

            # 过滤 3: 订阅匹配
            if has_any_subscription(chat_id):
                subscribers = set(get_subscribers(vendor))
                if chat_id not in subscribers:
                    continue

            # 发送
            try:
                points = (article.summary_points or "").split("\n") if article.summary_points else []
                card = build_news_card(
                    title=article.title,
                    vendor=article.vendor,
                    summary_points=points,
                    raw_url=article.url,
                    published_at=article.published_at.strftime("%Y-%m-%d") if article.published_at else "",
                )
                feishu.send_card(chat_id, card)
                delivered += 1
                deliver_cards_sent_total.labels(push_time=push_time).inc()
            except Exception as e:
                logger.warning(f"deliver_job send failed for {article.url} to {chat_id}: {e}")
                deliver_errors_total.labels(push_time=push_time).inc()

            if limit and delivered >= limit:
                break

    elapsed = _time.perf_counter() - start_time
    deliver_job_duration_seconds.labels(push_time=push_time).observe(elapsed)
    logger.info(f"deliver_job({push_time}): {delivered} cards sent, {len(articles)} articles, limit={limit}")
    return {"status": "ok", "delivered": delivered}
