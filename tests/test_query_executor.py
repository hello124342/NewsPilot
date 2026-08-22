"""并发查询执行器测试

覆盖：有界队列（打满丢弃）、每用户限流、worker 执行与异常隔离、
计数指标、关闭幂等、三平台接线、ChromaDB 并发初始化。
"""

import threading
import time

import pytest
from unittest.mock import patch, MagicMock

from app.core.query_executor import (
    QuerySubmitStatus,
    submit,
    shutdown,
    active_count,
    pending_count,
    _reconfigure,
)


@pytest.fixture
def small_pool():
    """小池子：便于测试队列打满 / 限流"""
    _reconfigure(max_workers=2, max_queue=2, queue_timeout=0.1, rate_limit_seconds=0)
    yield
    _reconfigure()  # 恢复默认池子
    shutdown()


class TestQueryExecutor:

    def test_submit_executes_in_worker(self, small_pool):
        result = {}
        done = threading.Event()

        def fn():
            result["ok"] = True
            done.set()

        assert submit(fn) is QuerySubmitStatus.ACCEPTED
        assert done.wait(timeout=3) is True
        assert result.get("ok") is True

    def test_queue_full_drops(self, small_pool):
        # 1 worker + 队列容量 1：只允许 1 个任务在途
        _reconfigure(max_workers=1, max_queue=1, queue_timeout=0.1, rate_limit_seconds=0)
        try:
            entered = threading.Event()
            release = threading.Event()
            finished = threading.Event()

            def fn():
                entered.set()
                release.wait(timeout=3)
                finished.set()

            # 第一个任务占用唯一 worker + 唯一队列位
            assert submit(fn) is QuerySubmitStatus.ACCEPTED
            assert entered.wait(timeout=3) is True

            # 队列打满 → 丢弃
            assert submit(fn) is QuerySubmitStatus.QUEUE_FULL

            release.set()
            assert finished.wait(timeout=3) is True
        finally:
            _reconfigure()

    def test_rate_limit_blocks_same_user(self, small_pool):
        # burst=1 → 令牌桶退化为单请求限流：一次放行后立即耗尽
        _reconfigure(
            max_workers=2, max_queue=10, queue_timeout=0.1,
            rate_limit_seconds=5, rate_burst=1, rate_refill=0.1,
        )
        try:
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.ACCEPTED
            # 令牌耗尽 → 限流
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.RATE_LIMITED
            # 不同用户独立令牌桶，不受影响
            assert submit(lambda: None, user_id="u2") is QuerySubmitStatus.ACCEPTED
        finally:
            _reconfigure()

    def test_rate_limit_allows_burst(self, small_pool):
        # 令牌桶允许短突发：burst=3 → 用户可连问 3 条，第 4 条被限
        _reconfigure(
            max_workers=4, max_queue=10, queue_timeout=0.1,
            rate_limit_seconds=5, rate_burst=3, rate_refill=0.1,
        )
        try:
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.ACCEPTED
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.ACCEPTED
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.ACCEPTED
            # 突发额度耗尽 → 限流
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.RATE_LIMITED
        finally:
            _reconfigure()

    def test_rate_limit_expires(self, small_pool):
        # 令牌回填：耗尽后等待足够时间，令牌回填至 >=1 即再次放行
        _reconfigure(
            max_workers=2, max_queue=10, queue_timeout=0.1,
            rate_limit_seconds=0.2, rate_burst=1, rate_refill=5.0,
        )
        try:
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.ACCEPTED
            time.sleep(0.4)  # refill=5/s → 0.4s 回填 2 个令牌
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.ACCEPTED
        finally:
            _reconfigure()

    def test_rate_limit_disabled_when_zero(self, small_pool):
        _reconfigure(max_workers=2, max_queue=10, queue_timeout=0.1, rate_limit_seconds=0)
        try:
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.ACCEPTED
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.ACCEPTED
            assert submit(lambda: None, user_id="u1") is QuerySubmitStatus.ACCEPTED
        finally:
            _reconfigure()

    def test_worker_exception_swallowed(self, small_pool):
        def boom():
            raise RuntimeError("boom")

        assert submit(boom) is QuerySubmitStatus.ACCEPTED
        time.sleep(0.2)  # 给 worker 执行时间；异常被记录，不传播

    def test_active_and_pending_counts(self, small_pool):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def fn():
            entered.set()
            release.wait(timeout=3)
            finished.set()

        submit(fn)
        assert entered.wait(timeout=3) is True
        assert active_count() >= 1
        assert pending_count() >= 1

        release.set()
        assert finished.wait(timeout=3) is True

    def test_shutdown_idempotent_and_blocks_new(self, small_pool):
        _reconfigure(max_workers=2, max_queue=10, queue_timeout=0.1, rate_limit_seconds=0)
        try:
            shutdown(wait=True)
            shutdown(wait=True)  # 二次调用不报错
            # 关闭后提交 → QUEUE_FULL
            assert submit(lambda: None) is QuerySubmitStatus.QUEUE_FULL
        finally:
            _reconfigure()

    def test_user_id_none_not_rate_limited(self, small_pool):
        _reconfigure(max_workers=2, max_queue=10, queue_timeout=0.1, rate_limit_seconds=1)
        try:
            assert submit(lambda: None, user_id=None) is QuerySubmitStatus.ACCEPTED
            assert submit(lambda: None) is QuerySubmitStatus.ACCEPTED  # 无 user_id 不限流
        finally:
            _reconfigure()


class TestQueryConfig:

    def test_query_settings_defaults(self):
        from app.core.config import Settings
        s = Settings()
        assert s.QUERY_MAX_WORKERS >= 1
        assert s.QUERY_MAX_QUEUE >= 1
        assert s.QUERY_QUEUE_TIMEOUT_SECONDS >= 0
        assert s.QUERY_RATE_LIMIT_SECONDS >= 0
        assert s.QUERY_RATE_BURST >= 1
        assert s.QUERY_RATE_REFILL > 0


class TestPlatformWiring:

    def test_feishu_dispatch_uses_query_executor(self):
        import app.feishu.event_router as er
        with patch("app.feishu.event_router.query_submit", return_value=QuerySubmitStatus.ACCEPTED) as m:
            er._dispatch_async(lambda: None, user_id="u1")
            m.assert_called_once()
            # user_id 传给执行器用于限流
            assert m.call_args.kwargs.get("user_id") == "u1"

    def test_telegram_message_submits_async(self, monkeypatch):
        from app.platforms.telegram import webhook
        monkeypatch.setattr(webhook, "_on_message", lambda inc: None)
        with patch("app.platforms.telegram.webhook.query_submit", return_value=QuerySubmitStatus.ACCEPTED) as m:
            webhook._handle_message({"chat": {"id": "111"}, "from": {"id": "2"}, "text": "hi"})
            m.assert_called_once()
            assert m.call_args.args[0] is webhook._on_message

    def test_telegram_busy_message_on_drop(self, monkeypatch):
        from app.platforms.telegram import webhook
        monkeypatch.setattr(webhook, "_on_message", lambda inc: None)
        sent = []

        class FakeAdapter:
            def send_message(self, chat_id, msg):
                sent.append((chat_id, msg.body))

        with patch("app.platforms.telegram.webhook.query_submit", return_value=QuerySubmitStatus.QUEUE_FULL), \
             patch("app.platforms.registry.get_platform_adapter", return_value=FakeAdapter()):
            webhook._handle_message({"chat": {"id": "111"}, "from": {"id": "2"}, "text": "hi"})

        assert len(sent) == 1
        assert sent[0][0] == "111"
        assert "系统繁忙" in sent[0][1]

    def test_telegram_rate_limited_message(self, monkeypatch):
        from app.platforms.telegram import webhook
        monkeypatch.setattr(webhook, "_on_message", lambda inc: None)
        sent = []

        class FakeAdapter:
            def send_message(self, chat_id, msg):
                sent.append(msg.body)

        with patch("app.platforms.telegram.webhook.query_submit", return_value=QuerySubmitStatus.RATE_LIMITED), \
             patch("app.platforms.registry.get_platform_adapter", return_value=FakeAdapter()):
            webhook._handle_message({"chat": {"id": "111"}, "from": {"id": "2"}, "text": "hi"})

        assert sent and "频繁" in sent[0]

    def test_telegram_dedup_update(self):
        from app.platforms.telegram import webhook
        # 同一 update_id 第二次返回重复
        assert webhook._dedup_update(42) is False
        assert webhook._dedup_update(42) is True
        assert webhook._dedup_update(43) is False

    def test_discord_message_submits_async(self):
        from app.platforms.discord import gateway
        with patch("app.platforms.discord.gateway.query_submit", return_value=QuerySubmitStatus.ACCEPTED) as m:
            gateway._submit_query(lambda: None, user_id="u1", chat_id="c1")
            m.assert_called_once()
            assert m.call_args.kwargs.get("user_id") == "u1"


class TestVectorStoreConcurrency:

    def test_concurrent_init_returns_single_instance(self):
        import app.rag.vector_store as vs

        fake_client = MagicMock()
        fake_collection = MagicMock()
        fake_client.get_collection.return_value = fake_collection
        fake_client.create_collection.return_value = fake_collection

        with patch.object(vs, "_client", None), \
             patch.object(vs, "_collection", None), \
             patch.object(vs.chromadb, "PersistentClient", return_value=fake_client):
            results = []

            def worker():
                results.append(vs.get_collection())

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert all(r is fake_collection for r in results)
            # PersistentClient 只初始化一次（无竞态）
            assert vs.chromadb.PersistentClient.call_count == 1
