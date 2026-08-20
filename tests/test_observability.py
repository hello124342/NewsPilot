"""多平台可观测性测试

覆盖多平台可观测性升级的测试闭环（A+B+F+G 中的 G）：
1. `track_platform_send` 装饰器行为（成功计数 sent / 失败计数 errors 并上抛 / 记录耗时）
2. 三个平台 adapter 的 `send_message` 已正确接线装饰器（sent / errors 按 platform 记账）
3. `deliver_job` 投递计数带 `platform` 维度

全部 mock 底层 SDK，不碰真实网络。
"""
import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.core.metrics import (
    platform_message_sent_total,
    platform_message_errors_total,
    platform_message_duration_seconds,
    deliver_cards_sent_total,
    deliver_errors_total,
    track_platform_send,
)
from app.platforms.message_model import RichMessage

try:  # Discord 为可选依赖，缺失时跳过对应测试
    import discord  # noqa: F401
    _DISCORD_OK = True
except ImportError:
    _DISCORD_OK = False


def _delta(counter, **labels):
    """读取指标当前值（计数器为进程内单例，断言一律用差值避免跨测试污染）"""
    return counter.labels(**labels)._value.get()


class TestTrackPlatformSendDecorator:
    """track_platform_send 装饰器行为（隔离测试）"""

    def test_success_increments_sent(self):
        """成功发送按 platform 递增 sent 计数"""

        @track_platform_send("feishu")
        def send():
            return {"success": True}

        before = _delta(platform_message_sent_total, platform="feishu")
        assert send() == {"success": True}
        assert _delta(platform_message_sent_total, platform="feishu") == before + 1

    def test_success_records_duration(self):
        """成功发送记录耗时直方图"""

        @track_platform_send("telegram")
        def send():
            return {}

        send()
        sample = platform_message_duration_seconds.labels(platform="telegram")
        assert sample._sum.get() > 0

    def test_failure_increments_errors_and_reraises(self):
        """失败按 error_type 计数 errors，且异常原样上抛"""

        @track_platform_send("discord")
        def send():
            raise ConnectionError("boom")

        before = _delta(
            platform_message_errors_total, platform="discord", error_type="ConnectionError"
        )
        with pytest.raises(ConnectionError, match="boom"):
            send()
        assert (
            _delta(platform_message_errors_total, platform="discord", error_type="ConnectionError")
            == before + 1
        )

    def test_preserves_function_metadata(self):
        """装饰器保留函数元信息"""

        @track_platform_send("feishu")
        def send():
            """my docstring"""
            return 42

        assert send.__name__ == "send"
        assert send.__doc__ == "my docstring"
        assert send() == 42


class TestFeishuAdapterMetrics:
    """FeishuAdapter.send_message 已接线 track_platform_send('feishu')"""

    def _adapter(self):
        from app.platforms.feishu.adapter import FeishuAdapter

        adapter = object.__new__(FeishuAdapter)
        adapter._client = MagicMock()
        return adapter

    def test_send_message_counts_sent(self):
        """成功发送 → platform_message_sent_total{feishu} +1"""
        adapter = self._adapter()
        adapter._client.send_card.return_value = {"code": 0, "msg": "om_123"}

        before = _delta(platform_message_sent_total, platform="feishu")
        result = adapter.send_message("oc_1", RichMessage(title="t", body="b"))
        assert result["success"] is True
        assert _delta(platform_message_sent_total, platform="feishu") == before + 1

    def test_send_message_failure_counts_errors(self):
        """发送抛异常 → errors{feishu, RuntimeError} +1 且异常上抛"""
        adapter = self._adapter()
        adapter._client.send_card.side_effect = RuntimeError("feishu down")

        before = _delta(
            platform_message_errors_total, platform="feishu", error_type="RuntimeError"
        )
        with pytest.raises(RuntimeError, match="feishu down"):
            adapter.send_message("oc_1", RichMessage(body="b"))
        assert (
            _delta(platform_message_errors_total, platform="feishu", error_type="RuntimeError")
            == before + 1
        )


class TestTelegramAdapterMetrics:
    """TelegramAdapter.send_message 已接线 track_platform_send('telegram')"""

    def _adapter(self):
        from app.platforms.telegram.adapter import TelegramAdapter

        adapter = object.__new__(TelegramAdapter)
        fake_bot = MagicMock()
        sent = MagicMock()
        sent.message_id = 777
        fake_bot.send_message.return_value = sent
        adapter._bot = fake_bot
        return adapter

    def test_send_message_counts_sent(self):
        """成功发送 → platform_message_sent_total{telegram} +1"""
        adapter = self._adapter()

        before = _delta(platform_message_sent_total, platform="telegram")
        result = adapter.send_message("-100", RichMessage(title="t", body="b"))
        assert result["message_id"] == "777"
        assert _delta(platform_message_sent_total, platform="telegram") == before + 1

    def test_send_message_failure_counts_errors(self):
        """发送抛异常 → errors{telegram, RuntimeError} +1 且异常上抛"""
        from app.platforms.telegram.adapter import TelegramAdapter

        adapter = object.__new__(TelegramAdapter)
        fake_bot = MagicMock()
        fake_bot.send_message.side_effect = RuntimeError("tg down")
        adapter._bot = fake_bot

        before = _delta(
            platform_message_errors_total, platform="telegram", error_type="RuntimeError"
        )
        with pytest.raises(RuntimeError, match="tg down"):
            adapter.send_message("-100", RichMessage(body="b"))
        assert (
            _delta(platform_message_errors_total, platform="telegram", error_type="RuntimeError")
            == before + 1
        )


@pytest.mark.skipif(not _DISCORD_OK, reason="discord.py not installed")
class TestDiscordAdapterMetrics:
    """DiscordAdapter.send_message 已接线 track_platform_send('discord')"""

    def test_send_message_counts_sent(self):
        """成功发送 → platform_message_sent_total{discord} +1"""
        from app.platforms.discord.adapter import DiscordAdapter

        mock_channel = MagicMock()
        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel
        sent = MagicMock()
        sent.id = 12345
        fut = MagicMock()
        fut.result.return_value = sent

        before = _delta(platform_message_sent_total, platform="discord")
        with patch("app.platforms.discord.gateway.get_client", return_value=mock_client), \
             patch("app.platforms.discord.adapter.asyncio.run_coroutine_threadsafe", return_value=fut):
            adapter = object.__new__(DiscordAdapter)
            result = adapter.send_message("111", RichMessage(title="t", body="b"))
        assert result["message_id"] == "12345"
        assert _delta(platform_message_sent_total, platform="discord") == before + 1

    def test_send_message_failure_counts_errors(self):
        """发送抛异常 → errors{discord, RuntimeError} +1 且异常上抛"""
        from app.platforms.discord.adapter import DiscordAdapter

        mock_channel = MagicMock()
        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel
        fut = MagicMock()
        fut.result.side_effect = RuntimeError("boom")

        before = _delta(
            platform_message_errors_total, platform="discord", error_type="RuntimeError"
        )
        with patch("app.platforms.discord.gateway.get_client", return_value=mock_client), \
             patch("app.platforms.discord.adapter.asyncio.run_coroutine_threadsafe", return_value=fut):
            adapter = object.__new__(DiscordAdapter)
            with pytest.raises(RuntimeError, match="boom"):
                adapter.send_message("111", RichMessage(body="b"))
        assert (
            _delta(platform_message_errors_total, platform="discord", error_type="RuntimeError")
            == before + 1
        )


class TestDeliverJobPlatformMetrics:
    """deliver_job 投递计数带 platform 维度（mock 数据库与适配器）"""

    @staticmethod
    def _chat(conv_id, platform):
        return SimpleNamespace(
            conversation_id=conv_id, chat_id=conv_id, platform=platform, is_active=True
        )

    @staticmethod
    def _article():
        return SimpleNamespace(
            vendor="OpenAI",
            title="Test Article",
            summary_points="point1\npoint2",
            published_at=datetime.now(timezone.utc),
            url="https://openai.com/blog/test",
        )

    @staticmethod
    def _db_sessions(chat_rows, article_rows):
        chat_session = MagicMock()
        chat_session.query.return_value.filter.return_value.all.return_value = chat_rows
        article_session = MagicMock()
        # 真实调用链: db.query(NewsArticle).filter(...).order_by(...).all()
        article_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = article_rows
        return chat_session, article_session

    def _deliver(self, chat_rows, article_rows, adapter):
        """用 mock 的 DB / 平台注册 / 订阅处理器驱动 deliver_job"""
        chat_session, article_session = self._db_sessions(chat_rows, article_rows)
        with patch("app.db.database.SessionLocal", side_effect=[chat_session, article_session]), \
             patch("app.platforms.registry.get_platform_adapter", return_value=adapter), \
             patch("app.subscription.handler.get_preference",
                   return_value={"push_time": "09:00", "frequency": "daily"}), \
             patch("app.subscription.handler.is_today_in_frequency", return_value=True), \
             patch("app.subscription.handler.has_any_subscription", return_value=True), \
             patch("app.subscription.handler.get_subscribers",
                   return_value={c.conversation_id for c in chat_rows}):
            from app.main import deliver_job
            return deliver_job("09:00")

    def test_deliver_counts_sent_per_platform(self):
        """feishu + telegram 两个平台各推 1 篇 → 各自 sent +1"""
        feishu_chat = self._chat("c1", "feishu")
        telegram_chat = self._chat("c2", "telegram")
        adapter = MagicMock()

        before_feishu = _delta(deliver_cards_sent_total, push_time="09:00", platform="feishu")
        before_tg = _delta(deliver_cards_sent_total, push_time="09:00", platform="telegram")

        result = self._deliver([feishu_chat, telegram_chat], [self._article()], adapter)

        assert result["delivered"] == 2
        assert _delta(deliver_cards_sent_total, push_time="09:00", platform="feishu") == before_feishu + 1
        assert _delta(deliver_cards_sent_total, push_time="09:00", platform="telegram") == before_tg + 1

    def test_deliver_counts_errors_per_platform(self):
        """发送失败 → deliver_errors_total{platform} +1，delivered 不计入"""
        feishu_chat = self._chat("c1", "feishu")
        adapter = MagicMock()
        adapter.send_message.side_effect = RuntimeError("send failed")

        before = _delta(deliver_errors_total, push_time="09:00", platform="feishu")

        result = self._deliver([feishu_chat], [self._article()], adapter)

        assert result["delivered"] == 0
        assert _delta(deliver_errors_total, push_time="09:00", platform="feishu") == before + 1
