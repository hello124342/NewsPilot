"""FastAPI 主入口

多平台支持：飞书（WebSocket 长连接）+ Telegram（Webhook）。
FastAPI 提供健康检查、Prometheus 指标、管理接口和 APScheduler 调度任务。

架构：
- WebSocket 长连接（daemon 线程）→ app/feishu/event_router.py → 业务逻辑
- Telegram Webhook → app/platforms/telegram/webhook.py → 业务逻辑
- FastAPI → /health, /metrics, /admin/*
- APScheduler → 5:00 抓取存储 + 9:00/12:00/18:00 投递
"""
import logging
import secrets
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException
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
    database._run_migrations()
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

    # --- 启动推送投递消费者（Redis Stream 消费端，独立线程池） ---
    try:
        from app.queue.deliver_consumer import start_consumers
        start_consumers(settings)
    except Exception as e:
        logger.warning(f"Deliver consumers not started: {e}")

    # --- 查询执行器模式切换（thread=同步线程池默认；async=asyncio 协程池灰度） ---
    if settings.QUERY_EXECUTOR_MODE.strip().lower() == "async":
        try:
            from app.core.async_query_executor import start_async_executor
            from app.core.query_executor import set_submit_delegate
            async_exec = start_async_executor(settings)
            set_submit_delegate(async_exec.submit)  # 平台层无感转发到协程池
            _app_services["async_executor"] = async_exec
            logger.info("Query executor mode: async (asyncio coroutine pool)")
        except Exception as e:
            logger.error(f"Async executor start failed, falling back to thread pool: {e}")
    else:
        logger.info("Query executor mode: thread (sync thread pool)")

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

    # --- 注册 Telegram Webhook（如果已配置） ---
    if settings.telegram_configured:
        from app.platforms.telegram.webhook import configure_webhook, telegram_router

        def _on_telegram_message(incoming):
            """Telegram 消息回调：自动注册 + 命令分派 + NL 查询"""
            from app.graph.bot_query_graph import build_query_graph
            incoming_text = incoming.text

            # 自动检测 chat 类型并注册（首次消息时）
            _auto_detect_and_register_telegram_chat(incoming.chat_id)

            # 先检查是否是订阅命令
            from app.subscription.handler import detect_command
            cmd = detect_command(incoming_text)
            if cmd:
                _handle_telegram_command(cmd, incoming.chat_id, incoming.sender_id)
                return

            # NL 查询 → BotQueryGraph
            state = {
                "platform": "telegram",
                "user_id": incoming.sender_id,
                "chat_id": incoming.chat_id,
                "user_query": incoming_text,
            }
            try:
                graph = build_query_graph()
                graph.invoke(state)
                logger.debug(f"Telegram query processed: chat_id={incoming.chat_id}")
            except Exception as e:
                logger.error(f"Telegram query failed: {e}")

        def _on_telegram_callback(callback_data, chat_id, sender_id):
            """Telegram 按钮回调：转发到订阅管理"""
            _handle_telegram_callback_action(callback_data, chat_id, sender_id)

        configure_webhook(
            settings=settings,
            on_message=_on_telegram_message,
            on_callback=_on_telegram_callback,
        )
        # 注册 webhook 路由
        app.include_router(telegram_router)
        logger.info(
            f"Telegram webhook configured at {settings.TELEGRAM_WEBHOOK_PATH}"
        )
    else:
        logger.info("Telegram not configured (TELEGRAM_BOT_TOKEN is empty), skipping")

    # --- 启动 Discord 网关（如果已配置） ---
    if settings.discord_configured:
        from app.platforms.discord.gateway import (
            configure as configure_discord_gateway,
            start as start_discord_gateway,
        )

        def _on_discord_message(incoming):
            """Discord 消息回调：自动注册 + 命令分派 + NL 查询"""
            from app.graph.bot_query_graph import build_query_graph
            incoming_text = incoming.text

            # 自动检测频道类型并注册（首次消息时）
            _auto_register_discord_channel(
                incoming.chat_id,
                is_dm=incoming.raw_payload.get("is_dm", False),
            )

            # help / start 关键词（Discord 无欢迎页，提供显式入口）
            _normalized = incoming_text.strip().lower()
            if _normalized in ("help", "帮助"):
                _handle_discord_command(("help", None), incoming.chat_id, incoming.sender_id)
                return
            if _normalized in ("start", "开始"):
                _handle_discord_command(("start", None), incoming.chat_id, incoming.sender_id)
                return

            # 先检查是否是订阅命令
            from app.subscription.handler import detect_command
            cmd = detect_command(incoming_text)
            if cmd:
                _handle_discord_command(cmd, incoming.chat_id, incoming.sender_id)
                return

            # NL 查询 → BotQueryGraph
            state = {
                "platform": "discord",
                "user_id": incoming.sender_id,
                "chat_id": incoming.chat_id,
                "user_query": incoming_text,
            }
            try:
                graph = build_query_graph()
                graph.invoke(state)
                logger.debug(f"Discord query processed: chat_id={incoming.chat_id}")
            except Exception as e:
                logger.error(f"Discord query failed: {e}")

        def _on_discord_callback(callback_data, chat_id, sender_id):
            """Discord 按钮回调：转发到订阅管理"""
            _handle_discord_callback_action(callback_data, chat_id, sender_id)

        configure_discord_gateway(
            settings=settings,
            on_message=_on_discord_message,
            on_callback=_on_discord_callback,
        )
        discord_thread = start_discord_gateway()
        _app_services["discord_thread"] = discord_thread
        if discord_thread:
            logger.info(f"Discord gateway started (thread: {discord_thread.name})")
        else:
            logger.warning(
                "Discord gateway failed to start "
                "(discord.py not installed or DISCORD_BOT_TOKEN missing)"
            )
    else:
        logger.info("Discord not configured (DISCORD_BOT_TOKEN is empty), skipping")

    yield
    scheduler.shutdown()
    # 优雅关闭推送消费者（未 ACK 消息留在 stream，重启后续投）
    try:
        from app.queue.deliver_consumer import stop_consumers
        stop_consumers()
    except Exception as e:
        logger.warning(f"Deliver consumers shutdown error: {e}")
    # 优雅关闭事件处理线程池（等待正在处理的请求完成）
    from app.feishu.event_router import shutdown_executor
    shutdown_executor(wait=True)
    # 注销异步委托并停止协程池（async 模式）
    try:
        from app.core.query_executor import set_submit_delegate
        set_submit_delegate(None)
        from app.core.async_query_executor import shutdown_async_executor
        shutdown_async_executor(wait=True)
    except Exception as e:
        logger.warning(f"Async executor shutdown error: {e}")
    # 优雅关闭共享查询池
    from app.core.query_executor import shutdown as query_shutdown
    query_shutdown(wait=True)
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


# ========== 管理端点鉴权 ==========

def verify_admin_token(x_admin_token: str = Header(default="")) -> None:
    """校验 /admin/* 端点的访问令牌

    这些端点可以触发全量群发消息和批量 embedding 计费，必须鉴权。
    采用 fail-closed：未配置 ADMIN_API_TOKEN 时整体禁用，而不是放行。
    """
    settings = Settings()  # type: ignore[call-arg]
    if not settings.admin_configured:
        raise HTTPException(
            status_code=503,
            detail="Admin endpoints are disabled: ADMIN_API_TOKEN is not configured",
        )
    # 常量时间比较，避免通过响应耗时侧信道逐字节猜测令牌
    if not secrets.compare_digest(x_admin_token, settings.ADMIN_API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")


@app.post("/admin/trigger-rss", dependencies=[Depends(verify_admin_token)])
async def trigger_rss():
    """手动触发 RSS 抓取（便于调试）"""
    result = process_rss_job()
    return JSONResponse(result)


@app.post("/admin/backfill-chromadb", dependencies=[Depends(verify_admin_token)])
async def backfill_chromadb(max_articles: int = 0):
    """回填已有文章到 ChromaDB 向量库

    遍历 MySQL 中已有但未在 ChromaDB 中的文章，逐条生成 embedding 并写入。
    用于现有数据库的 RAG 初始化或 ChromaDB 修复。

    Args:
        max_articles: 最多回填 N 篇（0=全部）。每篇调用一次 embedding API，注意 OpenAI 计费。
    """
    import logging as _logging
    _logger = _logging.getLogger("backfill")
    from app.db.database import SessionLocal
    from app.db.models import NewsArticle
    from app.rag.embedder import get_embedding, build_article_embed_text
    from app.rag.vector_store import add_article, collection_count

    db = SessionLocal()
    try:
        # 查询所有文章（标题+摘要即可用于 embedding，不依赖 raw_content）
        query = db.query(NewsArticle).order_by(NewsArticle.published_at.desc())

        if max_articles > 0:
            query = query.limit(max_articles)

        articles = query.all()
        _logger.info(f"Backfill: {len(articles)} articles to process")

        chroma_before = collection_count()
        embedded = 0
        skipped = 0

        for article in articles:
            # 构建 embedding 文本（标题+摘要，与 store_node 一致）
            summary = article.summary_points or ""
            embed_text = build_article_embed_text(
                title=article.title,
                vendor=article.vendor,
                summary_points=summary,
                channel="",  # 回填不区分 Blog/Twitter
            )

            try:
                embedding = get_embedding(embed_text)
                add_article(
                    article_id=article.id,
                    embedding=embedding,
                    document=embed_text,
                    metadata={
                        "vendor": article.vendor,
                        "title": article.title,
                        "url": article.url or "",
                        "published_at": article.published_at.strftime("%Y-%m-%d") if article.published_at else "",
                        "channel": "",
                    },
                )
                embedded += 1
            except ValueError:
                skipped += 1
            except Exception as exc:
                _logger.warning(f"Backfill: article {article.id} failed: {exc}")
                skipped += 1

        chroma_after = collection_count()
        return JSONResponse({
            "status": "ok",
            "total_articles": len(articles),
            "embedded": embedded,
            "skipped": skipped,
            "chromadb_before": chroma_before,
            "chromadb_after": chroma_after,
        })
    finally:
        db.close()


@app.post("/admin/trigger-push", dependencies=[Depends(verify_admin_token)])
async def trigger_push(time: str = "09:00", limit: int = 0):
    """手动触发推送（便于调试）。limit=N 只发前 N 篇文章，默认 0=全部。"""
    result = deliver_job(time, limit=limit)
    return JSONResponse(result)


@app.post("/admin/test-card", dependencies=[Depends(verify_admin_token)])
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


def _handle_telegram_command(cmd: tuple, chat_id: str, sender_id: str) -> None:
    """处理 Telegram 订阅命令（含权限校验）

    Args:
        cmd: detect_command 返回的 (action, vendor) 元组
        chat_id: Telegram chat_id
        sender_id: Telegram user_id
    """
    action, vendor = cmd
    logger.info(f"Telegram command: {action} {vendor}, chat_id={chat_id}, sender={sender_id}")

    from app.platforms.registry import get_platform_adapter
    from app.platforms.message_model import RichMessage
    from app.platforms.telegram.commands import (
        resolve_vendor, build_welcome_message, build_help_message,
    )

    settings = Settings()  # type: ignore[call-arg]
    adapter = get_platform_adapter("telegram", settings)
    if adapter is None:
        logger.warning("Telegram adapter not available")
        return

    PLATFORM = "telegram"

    # 权限检查：modify 操作仅管理员/群主可执行
    _modify_actions = {"subscribe", "unsubscribe", "set_time", "set_freq", "settings"}
    if action in _modify_actions:
        from app.chat.lifecycle import can_manage_subscription
        if not can_manage_subscription(
            chat_id, sender_id, platform=PLATFORM, platform_adapter=adapter
        ):
            try:
                adapter.send_message(chat_id, RichMessage(
                    title="⛔ 权限不足",
                    body="只有**群管理员**可以修改本群的订阅设置。\n\n发送 /list 查看当前订阅状态。",
                    color_hint="warning",
                ))
            except Exception:
                pass
            return

    try:
        if action == "subscribe":
            from app.subscription.handler import subscribe, __ALL__
            resolved = __ALL__ if vendor == __ALL__ else resolve_vendor(vendor)
            if resolved is None:
                adapter.send_message(chat_id, RichMessage(
                    body=f"❓ 未知厂商: {vendor}。\n可用: OpenAI, Anthropic, Google DeepMind, DeepSeek, Kimi (Moonshot), Z.ai / 智谱\n或 /subscribe all 订阅全部。",
                    color_hint="warning",
                ))
                return
            # 确保 chat 已注册
            _ensure_chat_registered(chat_id, "group", PLATFORM)
            subscribe(chat_id, resolved, platform=PLATFORM)
            adapter.send_message(chat_id, RichMessage(
                body=f"✅ 已订阅 **{resolved}**\n\n发送 /list 查看订阅列表。",
                color_hint="success",
            ))

        elif action == "unsubscribe":
            from app.subscription.handler import unsubscribe, __ALL__
            resolved = __ALL__ if vendor == __ALL__ else resolve_vendor(vendor)
            if resolved is None:
                adapter.send_message(chat_id, RichMessage(
                    body=f"❓ 未知厂商: {vendor}",
                    color_hint="warning",
                ))
                return
            _ensure_chat_registered(chat_id, "group", PLATFORM)
            unsubscribe(chat_id, resolved, platform=PLATFORM)
            adapter.send_message(chat_id, RichMessage(
                body=f"🔕 已退订 **{resolved}**\n\n发送 /list 查看订阅列表。",
                color_hint="info",
            ))

        elif action == "list":
            from app.subscription.handler import list_subscriptions
            subs = list_subscriptions(chat_id, platform=PLATFORM)
            if subs:
                lines = "\n".join(f"  • {v}" for v in subs)
                body = f"📋 **当前订阅了 {len(subs)} 个厂商：**\n\n{lines}"
            else:
                body = "⚠️ 尚未订阅任何厂商。\n发送 /subscribe OpenAI 开始订阅。"
            adapter.send_message(chat_id, RichMessage(
                title="📋 我的订阅", body=body, color_hint="info",
            ))

        elif action == "settings":
            from app.subscription.handler import get_preference, PUSH_TIMES, FREQUENCIES
            pref = get_preference(chat_id, platform=PLATFORM)
            body = (
                f"⏰ **推送时间：** {PUSH_TIMES.get(pref['push_time'], pref['push_time'])}\n"
                f"📅 **推送频率：** {FREQUENCIES.get(pref['frequency'], pref['frequency'])}\n\n"
                "修改：/settime 18:00 或 /setfrequency weekdays"
            )
            adapter.send_message(chat_id, RichMessage(
                title="⚙️ 推送设置", body=body, color_hint="info",
            ))

        elif action == "set_time":
            from app.subscription.handler import set_push_time
            # vendor param carries time value in cmd tuple
            time_val = vendor
            norm_time = f"{time_val}:00" if ":" not in time_val else time_val
            if len(norm_time) == 4:
                norm_time = f"0{norm_time}"
            set_push_time(chat_id, norm_time, platform=PLATFORM)
            adapter.send_message(chat_id, RichMessage(
                body=f"✅ 推送时间已设为 **{norm_time}**",
                color_hint="success",
            ))

        elif action == "set_freq":
            from app.subscription.handler import set_frequency
            set_frequency(chat_id, vendor, platform=PLATFORM)
            adapter.send_message(chat_id, RichMessage(
                body=f"✅ 推送频率已设为 **{vendor}**",
                color_hint="success",
            ))

        elif action == "help":
            adapter.send_message(chat_id, build_help_message())

        elif action == "start":
            _ensure_chat_registered(chat_id, "user", PLATFORM)
            adapter.send_message(chat_id, build_welcome_message())

        else:
            adapter.send_message(chat_id, RichMessage(
                body=f"❓ 未知命令: /{action}。发送 /help 查看可用命令。",
                color_hint="warning",
            ))
    except Exception as e:
        logger.error(f"Telegram command handler failed: {e}", exc_info=True)
        try:
            adapter.send_message(chat_id, RichMessage(
                body="❌ 操作失败，请稍后再试。",
                color_hint="warning",
            ))
        except Exception:
            pass


def _handle_telegram_callback_action(callback_data, chat_id: str, sender_id: str) -> None:
    """处理 Telegram 按钮回调（退订、设置等，含权限校验）"""
    from app.platforms.registry import get_platform_adapter
    from app.platforms.message_model import RichMessage

    settings = Settings()  # type: ignore[call-arg]
    adapter = get_platform_adapter("telegram", settings)
    if adapter is None:
        return

    PLATFORM = "telegram"
    action = callback_data.action

    # 权限检查（modify 操作仅管理员可执行）
    _modify_actions = {"unsubscribe", "subs:manage", "settings", "set_time", "set_freq"}
    if action in _modify_actions:
        from app.chat.lifecycle import can_manage_subscription
        if not can_manage_subscription(
            chat_id, sender_id, platform=PLATFORM, platform_adapter=adapter,
        ):
            try:
                adapter.send_message(chat_id, RichMessage(
                    body="⛔ 只有**群管理员**可以修改订阅设置。",
                    color_hint="warning",
                ))
            except Exception:
                pass
            return

    try:
        if action == "unsubscribe":
            vendor = callback_data.params.get("vendor", "")
            if vendor:
                from app.subscription.handler import unsubscribe
                unsubscribe(chat_id, vendor, platform=PLATFORM)
                adapter.send_message(chat_id, RichMessage(
                    body=f"🔕 已退订 **{vendor}**\n\n发送 /list 查看订阅列表。",
                    color_hint="info",
                ))

        elif action == "subs:manage":
            from app.subscription.handler import list_subscriptions
            subs = list_subscriptions(chat_id, platform=PLATFORM)
            if subs:
                lines = "\n".join(f"  • {v}" for v in subs)
                body = f"📋 **当前订阅了 {len(subs)} 个厂商：**\n\n{lines}"
            else:
                body = "⚠️ 尚未订阅任何厂商。"
            adapter.send_message(chat_id, RichMessage(
                title="📋 我的订阅", body=body, color_hint="info",
            ))

        elif action == "list":
            from app.subscription.handler import list_subscriptions
            subs = list_subscriptions(chat_id, platform=PLATFORM)
            if subs:
                lines = "\n".join(f"  • {v}" for v in subs)
                body = f"📋 **当前订阅了 {len(subs)} 个厂商：**\n\n{lines}"
            else:
                body = "⚠️ 尚未订阅任何厂商。"
            adapter.send_message(chat_id, RichMessage(
                title="📋 我的订阅", body=body, color_hint="info",
            ))

        elif action == "settings":
            from app.subscription.handler import get_preference, PUSH_TIMES, FREQUENCIES
            pref = get_preference(chat_id, platform=PLATFORM)
            body = (
                f"⏰ **推送时间：** {PUSH_TIMES.get(pref['push_time'], pref['push_time'])}\n"
                f"📅 **推送频率：** {FREQUENCIES.get(pref['frequency'], pref['frequency'])}\n\n"
                "修改：/settime 18:00 或 /setfrequency weekdays"
            )
            adapter.send_message(chat_id, RichMessage(
                title="⚙️ 推送设置", body=body, color_hint="info",
            ))

        elif action == "set_time":
            time_val = callback_data.params.get("time", "09:00")
            from app.subscription.handler import set_push_time
            set_push_time(chat_id, time_val, platform=PLATFORM)
            adapter.send_message(chat_id, RichMessage(
                body=f"✅ 推送时间已设为 **{time_val}**",
                color_hint="success",
            ))

        elif action == "set_freq":
            freq = callback_data.params.get("freq", "daily")
            from app.subscription.handler import set_frequency
            set_frequency(chat_id, freq, platform=PLATFORM)
            adapter.send_message(chat_id, RichMessage(
                body=f"✅ 推送频率已设为 **{freq}**",
                color_hint="success",
            ))

        else:
            logger.debug(f"Unhandled Telegram callback action: {action}, params={callback_data.params}")
    except Exception as e:
        logger.error(f"Telegram callback handler failed: {e}", exc_info=True)
        try:
            adapter.send_message(chat_id, RichMessage(
                body="❌ 操作失败，请稍后再试。",
                color_hint="warning",
            ))
        except Exception:
            pass


def _handle_discord_command(cmd: tuple, channel_id: str, sender_id: str) -> None:
    """处理 Discord 订阅命令（含权限校验）

    Args:
        cmd: detect_command 返回的 (action, vendor) 元组
        channel_id: Discord channel_id
        sender_id: Discord user_id
    """
    action, vendor = cmd
    logger.info(f"Discord command: {action} {vendor}, channel={channel_id}, sender={sender_id}")

    from app.platforms.registry import get_platform_adapter
    from app.platforms.message_model import RichMessage
    from app.platforms.discord.commands import (
        resolve_vendor, build_welcome_message, build_help_message,
    )

    settings = Settings()  # type: ignore[call-arg]
    adapter = get_platform_adapter("discord", settings)
    if adapter is None:
        logger.warning("Discord adapter not available")
        return

    PLATFORM = "discord"

    # 权限检查：modify 操作仅服务器管理员/群主可执行
    _modify_actions = {"subscribe", "unsubscribe", "set_time", "set_freq", "settings"}
    if action in _modify_actions:
        from app.chat.lifecycle import can_manage_subscription
        if not can_manage_subscription(
            channel_id, sender_id, platform=PLATFORM, platform_adapter=adapter
        ):
            try:
                adapter.send_message(channel_id, RichMessage(
                    title="⛔ 权限不足",
                    body="只有**服务器管理员**可以修改本频道的订阅设置。\n\n@我 发送「订阅列表」查看当前订阅状态。",
                    color_hint="warning",
                ))
            except Exception:
                pass
            return

    try:
        if action == "subscribe":
            from app.subscription.handler import subscribe, ALL_VENDORS
            if vendor == "__ALL__":
                for v in ALL_VENDORS:
                    subscribe(channel_id, v, platform=PLATFORM)
                _ensure_chat_registered(channel_id, "group", PLATFORM)
                adapter.send_message(channel_id, RichMessage(
                    body=f"✅ 已订阅全部 {len(ALL_VENDORS)} 个厂商。",
                    color_hint="success",
                ))
                return
            resolved = resolve_vendor(vendor)
            if resolved is None:
                adapter.send_message(channel_id, RichMessage(
                    body=f"❓ 未知厂商: {vendor}。\n可用: OpenAI, Anthropic, Google DeepMind, DeepSeek, Kimi (Moonshot), Z.ai / 智谱\n或 @我「订阅所有」。",
                    color_hint="warning",
                ))
                return
            _ensure_chat_registered(channel_id, "group", PLATFORM)
            subscribe(channel_id, resolved, platform=PLATFORM)
            adapter.send_message(channel_id, RichMessage(
                body=f"✅ 已订阅 **{resolved}**\n\n@我 发送「订阅列表」查看订阅。",
                color_hint="success",
            ))

        elif action == "unsubscribe":
            from app.subscription.handler import unsubscribe, ALL_VENDORS
            if vendor == "__ALL__":
                for v in ALL_VENDORS:
                    unsubscribe(channel_id, v, platform=PLATFORM)
                adapter.send_message(channel_id, RichMessage(
                    body=f"🔕 已退订全部 {len(ALL_VENDORS)} 个厂商。",
                    color_hint="info",
                ))
                return
            resolved = resolve_vendor(vendor)
            if resolved is None:
                adapter.send_message(channel_id, RichMessage(
                    body=f"❓ 未知厂商: {vendor}",
                    color_hint="warning",
                ))
                return
            _ensure_chat_registered(channel_id, "group", PLATFORM)
            unsubscribe(channel_id, resolved, platform=PLATFORM)
            adapter.send_message(channel_id, RichMessage(
                body=f"🔕 已退订 **{resolved}**\n\n@我 发送「订阅列表」查看订阅。",
                color_hint="info",
            ))

        elif action == "list":
            from app.subscription.handler import list_subscriptions
            subs = list_subscriptions(channel_id, platform=PLATFORM)
            if subs:
                lines = "\n".join(f"  • {v}" for v in subs)
                body = f"📋 **当前订阅了 {len(subs)} 个厂商：**\n\n{lines}"
            else:
                body = "⚠️ 尚未订阅任何厂商。\n@我 发送「订阅 OpenAI」开始订阅。"
            adapter.send_message(channel_id, RichMessage(
                title="📋 我的订阅", body=body, color_hint="info",
            ))

        elif action == "settings":
            from app.subscription.handler import get_preference, PUSH_TIMES, FREQUENCIES
            pref = get_preference(channel_id, platform=PLATFORM)
            body = (
                f"⏰ **推送时间：** {PUSH_TIMES.get(pref['push_time'], pref['push_time'])}\n"
                f"📅 **推送频率：** {FREQUENCIES.get(pref['frequency'], pref['frequency'])}\n\n"
                "@我 发送「设置推送时间 18:00」或「设置推送频率 工作日」修改"
            )
            adapter.send_message(channel_id, RichMessage(
                title="⚙️ 推送设置", body=body, color_hint="info",
            ))

        elif action == "set_time":
            from app.subscription.handler import set_push_time
            # vendor param carries time value in cmd tuple
            time_val = vendor
            norm_time = f"{time_val}:00" if ":" not in time_val else time_val
            if len(norm_time) == 4:
                norm_time = f"0{norm_time}"
            set_push_time(channel_id, norm_time, platform=PLATFORM)
            adapter.send_message(channel_id, RichMessage(
                body=f"✅ 推送时间已设为 **{norm_time}**",
                color_hint="success",
            ))

        elif action == "set_freq":
            from app.subscription.handler import set_frequency
            set_frequency(channel_id, vendor, platform=PLATFORM)
            adapter.send_message(channel_id, RichMessage(
                body=f"✅ 推送频率已设为 **{vendor}**",
                color_hint="success",
            ))

        elif action == "help":
            adapter.send_message(channel_id, build_help_message())

        elif action == "start":
            _ensure_chat_registered(channel_id, "user", PLATFORM)
            adapter.send_message(channel_id, build_welcome_message())

        else:
            adapter.send_message(channel_id, RichMessage(
                body="❓ 未知命令。@我 发送「帮助」查看可用命令。",
                color_hint="warning",
            ))
    except Exception as e:
        logger.error(f"Discord command handler failed: {e}", exc_info=True)
        try:
            adapter.send_message(channel_id, RichMessage(
                body="❌ 操作失败，请稍后再试。",
                color_hint="warning",
            ))
        except Exception:
            pass


def _handle_discord_callback_action(callback_data, channel_id: str, sender_id: str) -> None:
    """处理 Discord 按钮回调（退订、设置等，含权限校验）"""
    from app.platforms.registry import get_platform_adapter
    from app.platforms.message_model import RichMessage

    settings = Settings()  # type: ignore[call-arg]
    adapter = get_platform_adapter("discord", settings)
    if adapter is None:
        return

    PLATFORM = "discord"
    action = callback_data.action

    # 权限检查（modify 操作仅管理员可执行）
    _modify_actions = {"unsubscribe", "subs:manage", "settings", "set_time", "set_freq"}
    if action in _modify_actions:
        from app.chat.lifecycle import can_manage_subscription
        if not can_manage_subscription(
            channel_id, sender_id, platform=PLATFORM, platform_adapter=adapter,
        ):
            try:
                adapter.send_message(channel_id, RichMessage(
                    body="⛔ 只有**服务器管理员**可以修改订阅设置。",
                    color_hint="warning",
                ))
            except Exception:
                pass
            return

    try:
        if action == "unsubscribe":
            vendor = callback_data.params.get("vendor", "")
            if vendor:
                from app.subscription.handler import unsubscribe
                unsubscribe(channel_id, vendor, platform=PLATFORM)
                adapter.send_message(channel_id, RichMessage(
                    body=f"🔕 已退订 **{vendor}**\n\n@我 发送「订阅列表」查看订阅。",
                    color_hint="info",
                ))

        elif action in ("subs:manage", "list"):
            from app.subscription.handler import list_subscriptions
            subs = list_subscriptions(channel_id, platform=PLATFORM)
            if subs:
                lines = "\n".join(f"  • {v}" for v in subs)
                body = f"📋 **当前订阅了 {len(subs)} 个厂商：**\n\n{lines}"
            else:
                body = "⚠️ 尚未订阅任何厂商。"
            adapter.send_message(channel_id, RichMessage(
                title="📋 我的订阅", body=body, color_hint="info",
            ))

        elif action == "settings":
            from app.subscription.handler import get_preference, PUSH_TIMES, FREQUENCIES
            pref = get_preference(channel_id, platform=PLATFORM)
            body = (
                f"⏰ **推送时间：** {PUSH_TIMES.get(pref['push_time'], pref['push_time'])}\n"
                f"📅 **推送频率：** {FREQUENCIES.get(pref['frequency'], pref['frequency'])}\n\n"
                "@我 发送「设置推送时间 18:00」或「设置推送频率 工作日」修改"
            )
            adapter.send_message(channel_id, RichMessage(
                title="⚙️ 推送设置", body=body, color_hint="info",
            ))

        elif action == "set_time":
            time_val = callback_data.params.get("time", "09:00")
            from app.subscription.handler import set_push_time
            set_push_time(channel_id, time_val, platform=PLATFORM)
            adapter.send_message(channel_id, RichMessage(
                body=f"✅ 推送时间已设为 **{time_val}**",
                color_hint="success",
            ))

        elif action == "set_freq":
            freq = callback_data.params.get("freq", "daily")
            from app.subscription.handler import set_frequency
            set_frequency(channel_id, freq, platform=PLATFORM)
            adapter.send_message(channel_id, RichMessage(
                body=f"✅ 推送频率已设为 **{freq}**",
                color_hint="success",
            ))

        else:
            logger.debug(f"Unhandled Discord callback action: {action}, params={callback_data.params}")
    except Exception as e:
        logger.error(f"Discord callback handler failed: {e}", exc_info=True)
        try:
            adapter.send_message(channel_id, RichMessage(
                body="❌ 操作失败，请稍后再试。",
                color_hint="warning",
            ))
        except Exception:
            pass


def _auto_register_discord_channel(channel_id: str, is_dm: bool = False) -> None:
    """自动注册 Discord 频道（首次消息时触发）

    服务器文字频道 → 群聊（group），自动订阅全部厂商；
    私聊 DM → 用户（user）。
    """
    from app.chat.lifecycle import is_new_chat, register_chat
    from app.subscription.handler import subscribe, ALL_VENDORS

    if not is_new_chat(channel_id, platform="discord"):
        return

    chat_type = "user" if is_dm else "group"
    register_chat(channel_id, chat_type=chat_type, platform="discord")
    logger.info(f"Discord channel auto-registered: {channel_id} ({chat_type})")

    if not is_dm:
        for v in ALL_VENDORS:
            subscribe(channel_id, v, platform="discord")
        logger.info(f"Discord channel auto-subscribed all vendors: {channel_id}")


def _auto_detect_and_register_telegram_chat(chat_id: str) -> None:
    """自动检测 Telegram chat 类型并注册（首次消息时触发）

    Telegram chat_id 规则：负数 = 群聊，正数 = 私聊
    """
    from app.chat.lifecycle import is_new_chat, register_chat
    from app.subscription.handler import subscribe, ALL_VENDORS

    if not is_new_chat(chat_id, platform="telegram"):
        return

    # 根据 chat_id 符号判断：私聊 ID 为正整数，群聊 ID 为负整数
    try:
        cid = int(chat_id)
        is_group = cid < 0
    except (ValueError, TypeError):
        is_group = len(chat_id) > 15  # fallback: 长 ID 大概率是群

    chat_type = "group" if is_group else "user"
    register_chat(chat_id, chat_type=chat_type, platform="telegram")
    logger.info(f"Telegram chat auto-registered: {chat_id} ({chat_type})")

    if is_group:
        for v in ALL_VENDORS:
            subscribe(chat_id, v, platform="telegram")
        logger.info(f"Telegram group auto-subscribed all vendors: {chat_id}")


def _ensure_chat_registered(chat_id: str, chat_type: str, platform: str) -> None:
    """确保 chat 已在 chat_registry 中注册（幂等）"""
    from app.chat.lifecycle import register_chat, is_new_chat
    from app.subscription.handler import subscribe, ALL_VENDORS

    if is_new_chat(chat_id, platform=platform):
        is_group = chat_type == "group"
        register_chat(chat_id, chat_type=chat_type, platform=platform)
        logger.info(f"Chat auto-registered: {platform}/{chat_id} ({chat_type})")
        # 群聊自动订阅全部厂商
        if is_group:
            for v in ALL_VENDORS:
                subscribe(chat_id, v, platform=platform)
            logger.info(f"Group auto-subscribed all vendors: {platform}/{chat_id}")


def schedule_rss_polling():
    """调度器回调入口（仅抓取+存储，不推送）"""
    return process_rss_job()


def deliver_job(push_time: str, limit: int = 0) -> dict:
    """按用户偏好将今日已存储的文章投递到各平台 chat（多平台）

    Args:
        push_time: 当前推送时间窗口（"09:00" / "12:00" / "18:00"）
        limit: 最多投递 N 条（0=全部）

    逻辑（生产者）：
    1. 查询今日存储的所有文章
    2. 查询所有平台活跃 chat（含 platform 信息）
    3. 对每篇文章，按 推送时间 → 频率 → 订阅 三层过滤
    4. 命中的 (article, platform, chat) 逐条入队到 Redis Stream；实际发送由
       deliver_consumer 异步完成（可靠投递：崩溃重投 + 幂等去重 + 死信）
    5. Redis 不可用时降级为内联同步发送（Redis 挂了推送不挂）
    """
    import time as _time
    from datetime import datetime, timezone
    from app.db.database import SessionLocal
    from app.db.models import NewsArticle, ChatRegistry
    from app.subscription.handler import (
        get_preference, get_subscribers, has_any_subscription,
        is_today_in_frequency,
    )
    from app.platforms.registry import get_platform_adapter
    from app.queue.stream_queue import get_stream_queue
    from app.queue.deliver_consumer import build_article_message
    from app.core.metrics import (
        deliver_cards_sent_total, deliver_errors_total, deliver_job_duration_seconds,
        deliver_enqueued_total, queue_fallback_total,
    )

    start_time = _time.perf_counter()
    settings = Settings()  # type: ignore[call-arg]

    # 查询各平台活跃 chat
    db = SessionLocal()
    try:
        active_chats = (
            db.query(ChatRegistry)
            .filter(ChatRegistry.is_active == True)
            .all()
        )
        # 按平台分组：{platform: [chat_id, ...]}
        platform_chats: dict[str, list[str]] = {}
        for chat in active_chats:
            conv_id = chat.conversation_id or chat.chat_id
            platform = chat.platform or "feishu"
            platform_chats.setdefault(platform, []).append(conv_id)
    except Exception as e:
        logger.error(f"deliver_job query failed: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

    total_chats = sum(len(c) for c in platform_chats.values())
    if total_chats == 0:
        logger.info(f"deliver_job({push_time}): no active chats")
        deliver_job_duration_seconds.labels(push_time=push_time).observe(_time.perf_counter() - start_time)
        return {"status": "ok", "delivered": 0, "enqueued": 0}

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
        logger.error(f"deliver_job article query failed: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

    if not articles:
        logger.info(f"deliver_job({push_time}): no articles to deliver")
        deliver_job_duration_seconds.labels(push_time=push_time).observe(_time.perf_counter() - start_time)
        return {"status": "ok", "delivered": 0, "enqueued": 0}

    # 是否启用队列：配置开启且 Redis 可用；否则降级内联发送
    queue = get_stream_queue() if settings.DELIVER_QUEUE_ENABLED else None
    use_queue = queue is not None
    if not use_queue:
        queue_fallback_total.inc()
        logger.warning(f"deliver_job({push_time}): queue unavailable, falling back to inline send")

    enqueued = 0   # 入队条数
    delivered = 0  # 内联降级时的实际发送条数
    count = 0      # 命中过滤的目标条数（用于 limit）
    stop = False

    for article in articles:
        if stop:
            break
        vendor = article.vendor

        for platform, chat_list in platform_chats.items():
            if stop:
                break
            adapter = None
            if not use_queue:
                adapter = get_platform_adapter(platform, settings)
                if adapter is None:
                    logger.warning(f"deliver_job: platform '{platform}' adapter unavailable, skipping {len(chat_list)} chats")
                    continue

            for chat_id in chat_list:
                # 过滤 1: 推送时间匹配
                pref = get_preference(chat_id, platform=platform)
                if pref["push_time"] != push_time:
                    continue
                # 过滤 2: 频率匹配
                if not is_today_in_frequency(pref["frequency"]):
                    continue
                # 过滤 3: 订阅匹配
                if has_any_subscription(chat_id, platform=platform):
                    subscribers = set(get_subscribers(vendor, platform=platform))
                    if chat_id not in subscribers:
                        continue

                count += 1
                if use_queue:
                    # 纯生产者：入队瘦身消息，消费者按 article_id 查库渲染
                    try:
                        queue.enqueue({
                            "article_id": article.id,
                            "platform": platform,
                            "conversation_id": chat_id,
                            "push_time": push_time,
                            "retry_count": 0,
                            "enqueued_at": _time.time(),
                        })
                        enqueued += 1
                        deliver_enqueued_total.inc()
                    except Exception as e:
                        logger.error(f"deliver_job enqueue failed: {e}")
                        deliver_errors_total.labels(push_time=push_time, platform=platform).inc()
                else:
                    # 降级：内联同步发送
                    try:
                        adapter.send_message(chat_id, build_article_message(article, vendor))
                        delivered += 1
                        deliver_cards_sent_total.labels(push_time=push_time, platform=platform).inc()
                    except Exception as e:
                        logger.warning(f"deliver_job inline send failed for {article.url} to {platform}/{chat_id}: {e}")
                        deliver_errors_total.labels(push_time=push_time, platform=platform).inc()

                if limit and count >= limit:
                    stop = True
                    break

    elapsed = _time.perf_counter() - start_time
    deliver_job_duration_seconds.labels(push_time=push_time).observe(elapsed)
    if use_queue:
        logger.info(f"deliver_job({push_time}): enqueued {enqueued} message(s) across {len(platform_chats)} platform(s)")
        return {"status": "ok", "enqueued": enqueued, "mode": "queue"}
    logger.info(f"deliver_job({push_time}): {delivered} cards sent inline (fallback) across {len(platform_chats)} platform(s)")
    return {"status": "ok", "delivered": delivered, "mode": "inline"}
