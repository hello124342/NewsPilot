"""推送投递消费者（Redis Stream 消费端）

deliver_job 瘦身为纯生产者后，实际发送由此消费者完成：
- N 个消费线程循环 XREADGROUP → 查库渲染 RichMessage → adapter.send_message → ACK
- 每条消息发送前抢幂等锁（at-least-once 下防重投导致重复发送）
- 独立线程周期性 claim_stale：接管崩溃 consumer 未 ACK 的消息重投；超限进 DLQ
- lifespan 启动/优雅关闭；未 ACK 消息留在 stream，进程重启后续投

消费者独立于查询池（query_executor）——推送与查询是两类负载，互不挤占。
"""
import logging
import threading
import time
from typing import Optional

from app.core.config import Settings

logger = logging.getLogger(__name__)


def build_article_message(article, vendor: str):
    """将 NewsArticle 渲染为平台无关的 RichMessage（生产者与消费者共用）"""
    from app.platforms.message_model import RichMessage, ActionButton

    points = (article.summary_points or "").split("\n") if article.summary_points else []
    pub_date = article.published_at.strftime("%Y-%m-%d") if article.published_at else ""
    points_md = "\n".join(f"  {i}. {p}" for i, p in enumerate(points, 1)) if points else "暂无摘要"
    body = (
        f"📰 **{article.title}**\n\n"
        f"💡 **核心要点总结**\n{points_md}"
    )
    return RichMessage(
        title=vendor,
        body=body,
        buttons=[
            ActionButton(label="📖 阅读原文", action="url", value=article.url or "", style="primary"),
            ActionButton(
                label=f"🔕 退订 {vendor}",
                action="callback",
                value=f'{{"action":"unsubscribe","vendor":"{vendor}"}}',
                style="default",
            ),
        ],
        color_hint="info",
        footer=f"📅 {pub_date}",
    )


def process_message(payload: dict, settings: Settings) -> bool:
    """处理单条推送消息：查库 → 渲染 → 发送。

    Returns True 表示已处理（含幂等跳过），应 ACK；False 表示可重试的失败，不 ACK。
    """
    from app.db.database import SessionLocal
    from app.db.models import NewsArticle
    from app.platforms.registry import get_platform_adapter
    from app.queue.stream_queue import get_stream_queue
    from app.core.metrics import deliver_consumed_total, deliver_errors_total

    article_id = payload.get("article_id")
    platform = payload.get("platform", "feishu")
    conversation_id = payload.get("conversation_id")
    push_time = payload.get("push_time", "")

    if not article_id or not conversation_id:
        logger.warning(f"deliver_consumer: malformed payload, dropping: {payload}")
        return True  # 脏消息无法处理，ACK 丢弃

    # 幂等去重：抢锁失败说明已发过（重投消息），直接视为已处理
    queue = get_stream_queue()
    if queue is not None and not queue.acquire_send_lock(article_id, platform, conversation_id):
        logger.info(f"deliver_consumer: dedup skip article={article_id} → {platform}/{conversation_id}")
        return True

    if SessionLocal is None:
        return False  # 无 DB，稍后重试
    db = SessionLocal()
    try:
        article = db.query(NewsArticle).filter_by(id=article_id).first()
    except Exception as e:
        logger.error(f"deliver_consumer: article query failed: {e}")
        db.close()
        return False
    finally:
        db.close()

    if article is None:
        logger.warning(f"deliver_consumer: article {article_id} not found, dropping")
        return True

    adapter = get_platform_adapter(platform, settings)
    if adapter is None:
        logger.warning(f"deliver_consumer: no adapter for platform '{platform}', dropping")
        return True

    try:
        msg = build_article_message(article, article.vendor)
        adapter.send_message(conversation_id, msg)
        deliver_consumed_total.labels(platform=platform).inc()
        return True
    except Exception as e:
        logger.warning(
            f"deliver_consumer: send failed article={article_id} → {platform}/{conversation_id}: {e}"
        )
        deliver_errors_total.labels(push_time=push_time or "queue", platform=platform).inc()
        return False  # 发送失败 → 不 ACK，等 claim_stale 重投


class DeliverConsumerPool:
    """推送消费者线程池：N 个消费线程 + 1 个 claim/指标线程"""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or Settings()  # type: ignore[call-arg]
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        from app.queue.stream_queue import get_stream_queue
        if get_stream_queue() is None:
            logger.warning("DeliverConsumerPool: Redis unavailable, consumers not started (inline fallback active)")
            return
        n = max(1, self._settings.DELIVER_CONSUMERS)
        for i in range(n):
            t = threading.Thread(target=self._consume_loop, args=(f"consumer-{i}",),
                                 name=f"deliver-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        maint = threading.Thread(target=self._maintenance_loop, name="deliver-maint", daemon=True)
        maint.start()
        self._threads.append(maint)
        self._started = True
        logger.info(f"DeliverConsumerPool started: {n} consumers + maintenance")

    def stop(self, timeout: float = 5.0) -> None:
        if not self._started:
            return
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._started = False
        logger.info("DeliverConsumerPool stopped (unacked messages remain in stream for restart)")

    def _consume_loop(self, consumer: str) -> None:
        from app.queue.stream_queue import get_stream_queue
        queue = get_stream_queue()
        if queue is None:
            return
        block_ms = self._settings.DELIVER_BLOCK_MS
        while not self._stop.is_set():
            try:
                messages = queue.consume(consumer, block_ms=block_ms, count=10)
            except Exception as e:
                logger.warning(f"deliver_consumer[{consumer}]: consume error: {e}")
                time.sleep(1.0)
                continue
            for msg_id, payload in messages:
                if process_message(payload, self._settings):
                    try:
                        queue.ack(msg_id)
                    except Exception as e:
                        logger.warning(f"deliver_consumer[{consumer}]: ack failed {msg_id}: {e}")

    def _maintenance_loop(self) -> None:
        """周期性 claim_stale（重投崩溃消息）+ 刷新队列深度指标"""
        from app.queue.stream_queue import get_stream_queue
        from app.core.metrics import (
            deliver_queue_depth, deliver_pending_messages, deliver_retry_total,
        )
        queue = get_stream_queue()
        if queue is None:
            return
        idle_ms = self._settings.DELIVER_CLAIM_IDLE_MS
        while not self._stop.is_set():
            try:
                stale = queue.claim_stale("maintenance", min_idle_ms=idle_ms, count=20)
                for msg_id, payload in stale:
                    deliver_retry_total.inc()
                    if process_message(payload, self._settings):
                        queue.ack(msg_id)
                deliver_queue_depth.set(queue.depth())
                deliver_pending_messages.set(queue.pending())
            except Exception as e:
                logger.debug(f"deliver_consumer maintenance error: {e}")
            # 30s 一轮（stop 期间可提前唤醒）
            self._stop.wait(timeout=30.0)


# ========== 模块级单例 ==========

_pool: Optional[DeliverConsumerPool] = None


def start_consumers(settings: Optional[Settings] = None) -> None:
    """lifespan 启动时调用"""
    global _pool
    if _pool is None:
        _pool = DeliverConsumerPool(settings)
    _pool.start()


def stop_consumers() -> None:
    """lifespan 退出时调用"""
    global _pool
    if _pool is not None:
        _pool.stop()
        _pool = None
