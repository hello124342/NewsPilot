"""订阅功能测试

测试命令检测、订阅/退订 CRUD、推送过滤逻辑。
"""
import pytest
from unittest.mock import patch, MagicMock


class TestDetectCommand:
    """命令检测测试"""

    def test_subscribe_openai(self):
        """测试检测「订阅 OpenAI」"""
        from app.subscription.handler import detect_command

        cmd = detect_command("订阅 OpenAI")
        assert cmd == ("subscribe", "OpenAI")

    def test_subscribe_deepseek(self):
        """测试检测「订阅 DeepSeek」"""
        from app.subscription.handler import detect_command

        cmd = detect_command("订阅 DeepSeek")
        assert cmd == ("subscribe", "DeepSeek")

    def test_subscribe_with_alias_kimi(self):
        """测试别名：用户说「订阅 Kimi」应解析为标准名"""
        from app.subscription.handler import detect_command

        cmd = detect_command("订阅 Kimi")
        assert cmd == ("subscribe", "Kimi (Moonshot)")

    def test_subscribe_with_alias_google(self):
        """测试别名：用户说「订阅 Google」应解析为 Google DeepMind"""
        from app.subscription.handler import detect_command

        cmd = detect_command("订阅 Google")
        assert cmd == ("subscribe", "Google DeepMind")

    def test_subscribe_all(self):
        """测试「订阅所有」"""
        from app.subscription.handler import detect_command

        cmd = detect_command("订阅所有")
        assert cmd == ("subscribe", "__ALL__")

    def test_unsubscribe_openai(self):
        """测试「取消订阅 OpenAI」"""
        from app.subscription.handler import detect_command

        cmd = detect_command("取消订阅 OpenAI")
        assert cmd == ("unsubscribe", "OpenAI")

    def test_unsubscribe_via_tuiding(self):
        """测试通过「退订」关键字取消"""
        from app.subscription.handler import detect_command

        cmd = detect_command("退订 Anthropic")
        assert cmd == ("unsubscribe", "Anthropic")

    def test_unsubscribe_all(self):
        """测试「取消订阅所有」"""
        from app.subscription.handler import detect_command

        cmd = detect_command("取消订阅所有")
        assert cmd == ("unsubscribe", "__ALL__")

    def test_list_subscriptions(self):
        """测试「我的订阅」"""
        from app.subscription.handler import detect_command

        cmd = detect_command("我的订阅")
        assert cmd == ("list", None)

    def test_list_subscriptions_alt(self):
        """测试「订阅列表」"""
        from app.subscription.handler import detect_command

        cmd = detect_command("订阅列表")
        assert cmd == ("list", None)

    def test_not_a_subscription_command(self):
        """测试非订阅命令返回 None"""
        from app.subscription.handler import detect_command

        assert detect_command("OpenAI 最近有什么新闻") is None
        assert detect_command("你好") is None
        assert detect_command("帮我看下 DeepSeek") is None

    def test_unknown_vendor_returns_none(self):
        """测试不认识的厂商返回 None（交给 BotQueryGraph）"""
        from app.subscription.handler import detect_command

        # NothingBot 不是已知厂商
        assert detect_command("订阅 NothingBot") is None


class TestVendorResolution:
    """厂商名解析测试"""

    def test_resolve_openai(self):
        from app.subscription.handler import _resolve_vendor
        assert _resolve_vendor("OpenAI") == "OpenAI"
        assert _resolve_vendor("openai") == "OpenAI"

    def test_resolve_deepseek(self):
        from app.subscription.handler import _resolve_vendor
        assert _resolve_vendor("DeepSeek") == "DeepSeek"
        assert _resolve_vendor("deepseek") == "DeepSeek"

    def test_resolve_kimi_alias(self):
        from app.subscription.handler import _resolve_vendor
        assert _resolve_vendor("Kimi") == "Kimi (Moonshot)"
        assert _resolve_vendor("kimi") == "Kimi (Moonshot)"
        assert _resolve_vendor("Moonshot") == "Kimi (Moonshot)"

    def test_resolve_google_alias(self):
        from app.subscription.handler import _resolve_vendor
        assert _resolve_vendor("Google") == "Google DeepMind"
        assert _resolve_vendor("google") == "Google DeepMind"
        assert _resolve_vendor("deepmind") == "Google DeepMind"

    def test_resolve_zhipu_alias(self):
        from app.subscription.handler import _resolve_vendor
        assert _resolve_vendor("智谱") == "Z.ai / 智谱"

    def test_resolve_anthropic(self):
        from app.subscription.handler import _resolve_vendor
        assert _resolve_vendor("Anthropic") == "Anthropic"
        assert _resolve_vendor("claude") == "Anthropic"

    def test_resolve_unknown(self):
        from app.subscription.handler import _resolve_vendor
        assert _resolve_vendor("NothingBot") is None
        assert _resolve_vendor("") is None


class TestSubscriptionCRUD:
    """订阅 CRUD 操作测试"""

    @patch("app.db.sql_repositories.SessionLocal")
    def test_subscribe_new(self, mock_session):
        """测试首次订阅"""
        from app.subscription.handler import subscribe

        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = None  # 没有旧记录
        mock_session.return_value = mock_db

        result = subscribe("chat_001", "OpenAI")
        assert "已订阅" in result
        assert "OpenAI" in result

    @patch("app.db.sql_repositories.SessionLocal")
    def test_subscribe_duplicate(self, mock_session):
        """测试重复订阅同一厂商"""
        from app.subscription.handler import subscribe

        mock_existing = MagicMock()
        mock_existing.is_active = True
        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = mock_existing
        mock_session.return_value = mock_db

        result = subscribe("chat_001", "OpenAI")
        assert "已经订阅" in result

    @patch("app.db.sql_repositories.SessionLocal")
    def test_subscribe_reactivate(self, mock_session):
        """测试重新激活已退订的厂商"""
        from app.subscription.handler import subscribe

        mock_existing = MagicMock()
        mock_existing.is_active = False
        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = mock_existing
        mock_session.return_value = mock_db

        result = subscribe("chat_001", "OpenAI")
        assert "重新订阅" in result
        assert mock_existing.is_active is True

    @patch("app.db.sql_repositories.SessionLocal")
    def test_unsubscribe_active(self, mock_session):
        """测试退订已订阅的厂商"""
        from app.subscription.handler import unsubscribe

        mock_existing = MagicMock()
        mock_existing.is_active = True
        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = mock_existing
        mock_session.return_value = mock_db

        result = unsubscribe("chat_001", "OpenAI")
        assert "已退订" in result
        assert mock_existing.is_active is False

    @patch("app.db.sql_repositories.SessionLocal")
    def test_unsubscribe_not_subscribed(self, mock_session):
        """测试退订未订阅的厂商"""
        from app.subscription.handler import unsubscribe

        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = None
        mock_session.return_value = mock_db

        result = unsubscribe("chat_001", "OpenAI")
        assert "未订阅" in result

    @patch("app.db.sql_repositories.SessionLocal")
    def test_list_subscriptions(self, mock_session):
        """测试查看订阅列表"""
        from app.subscription.handler import list_subscriptions

        sub1 = MagicMock()
        sub1.vendor = "OpenAI"
        sub2 = MagicMock()
        sub2.vendor = "DeepSeek"

        mock_db = MagicMock()
        mock_db.query().filter_by().all.return_value = [sub1, sub2]
        mock_session.return_value = mock_db

        result = list_subscriptions("chat_001")
        assert result == ["OpenAI", "DeepSeek"]

    @patch("app.db.sql_repositories.SessionLocal")
    def test_get_subscribers(self, mock_session):
        """测试获取某厂商的所有订阅者"""
        from app.subscription.handler import get_subscribers

        sub1 = MagicMock()
        sub1.chat_id = "chat_001"
        sub1.conversation_id = ""
        sub2 = MagicMock()
        sub2.chat_id = "chat_002"
        sub2.conversation_id = ""

        mock_db = MagicMock()
        mock_db.query().filter_by().all.return_value = [sub1, sub2]
        mock_session.return_value = mock_db

        result = get_subscribers("OpenAI")
        assert result == ["chat_001", "chat_002"]

    @patch("app.db.sql_repositories.SessionLocal")
    def test_has_any_subscription_true(self, mock_session):
        """测试有订阅记录时返回 True"""
        from app.subscription.handler import has_any_subscription

        mock_db = MagicMock()
        mock_db.query().filter_by().count.return_value = 3
        mock_session.return_value = mock_db

        assert has_any_subscription("chat_001") is True

    @patch("app.db.sql_repositories.SessionLocal")
    def test_has_any_subscription_false(self, mock_session):
        """测试无订阅记录时返回 False"""
        from app.subscription.handler import has_any_subscription

        mock_db = MagicMock()
        mock_db.query().filter_by().count.return_value = 0
        mock_session.return_value = mock_db

        assert has_any_subscription("chat_001") is False


class TestPushFiltering:
    """推送过滤逻辑测试

    注意：由于 send_feishu 模块通过 from import 导入了 handler 的函数，
    需要 patch send_feishu 模块的本地引用才能生效。
    _resolve_targets 从 chat_registry 获取活跃 chat。
    """

    @patch("app.chat.lifecycle.get_active_chat_ids")
    @patch("app.graph.nodes.send_feishu.has_any_subscription")
    @patch("app.graph.nodes.send_feishu.get_subscribers")
    def test_resolve_targets_no_preferences(self, mock_get_subs, mock_has_any, mock_active):
        """测试无偏好的 chat 默认接收全部"""
        from app.graph.nodes.send_feishu import _resolve_targets

        mock_active.return_value = ["chat_001", "chat_002"]
        mock_get_subs.return_value = []
        mock_has_any.return_value = False

        targets = _resolve_targets("OpenAI")
        assert targets == ["chat_001", "chat_002"]

    @patch("app.chat.lifecycle.get_active_chat_ids")
    @patch("app.graph.nodes.send_feishu.has_any_subscription")
    @patch("app.graph.nodes.send_feishu.get_subscribers")
    def test_resolve_targets_filtered(self, mock_get_subs, mock_has_any, mock_active):
        """测试有偏好的 chat 按偏好过滤"""
        from app.graph.nodes.send_feishu import _resolve_targets

        mock_active.return_value = ["chat_001", "chat_002"]
        mock_get_subs.return_value = ["chat_001"]
        mock_has_any.side_effect = lambda cid: True

        targets = _resolve_targets("OpenAI")
        assert targets == ["chat_001"]

    @patch("app.chat.lifecycle.get_active_chat_ids")
    @patch("app.graph.nodes.send_feishu.has_any_subscription")
    @patch("app.graph.nodes.send_feishu.get_subscribers")
    def test_resolve_targets_mixed(self, mock_get_subs, mock_has_any, mock_active):
        """测试混合场景：部分有偏好，部分无偏好"""
        from app.graph.nodes.send_feishu import _resolve_targets

        mock_active.return_value = ["chat_001", "chat_002", "chat_003"]
        mock_get_subs.return_value = ["chat_001"]
        mock_has_any.side_effect = lambda cid: {
            "chat_001": True, "chat_002": True, "chat_003": False,
        }[cid]

        targets = _resolve_targets("OpenAI")
        assert targets == ["chat_001", "chat_003"]


class TestSubscriptionCards:
    """订阅卡片构建测试"""

    def test_build_subscription_reply_subscribe(self):
        """测试订阅确认卡片"""
        from app.feishu.card_builder import build_subscription_reply

        card = build_subscription_reply("subscribe", "OpenAI")
        assert card["header"]["title"]["content"] == "订阅确认"
        assert "已订阅" in str(card)

    def test_build_subscription_reply_unsubscribe(self):
        """测试退订确认卡片"""
        from app.feishu.card_builder import build_subscription_reply

        card = build_subscription_reply("unsubscribe", "OpenAI")
        assert card["header"]["title"]["content"] == "退订确认"
        assert "已退订" in str(card)

    def test_build_subscription_list_card_with_subs(self):
        """测试有订阅时的列表卡片"""
        from app.feishu.card_builder import build_subscription_list_card

        card = build_subscription_list_card(["OpenAI", "DeepSeek"])
        assert "OpenAI" in str(card)
        assert "DeepSeek" in str(card)
        assert "2 个厂商" in str(card)

    def test_build_subscription_list_card_empty(self):
        """测试无订阅时的列表卡片"""
        from app.feishu.card_builder import build_subscription_list_card

        card = build_subscription_list_card([])
        assert "没有订阅" in str(card)

    def test_build_welcome_card(self):
        """测试欢迎卡片"""
        from app.feishu.card_builder import build_welcome_card

        card = build_welcome_card()
        assert "欢迎" in str(card)
        assert "OpenAI" in str(card)
        assert "订阅" in str(card)

    def test_build_news_card_has_unsubscribe_button(self):
        """测试新闻卡片包含退订按钮"""
        from app.feishu.card_builder import build_news_card

        card = build_news_card(
            title="Test Article",
            vendor="OpenAI",
            summary_points=["Point 1", "Point 2", "Point 3"],
            raw_url="https://example.com",
        )
        # 卡片的 actions 应包含退订按钮
        card_str = str(card)
        assert "退订 OpenAI" in card_str
        assert "unsubscribe" in card_str and "OpenAI" in card_str


class TestSettingsCommandDetection:
    """设置命令检测测试"""

    def test_settings_panel(self):
        from app.subscription.handler import detect_command
        assert detect_command("设置") == ("settings", None)
        assert detect_command("推送设置") == ("settings", None)

    def test_set_time_9am(self):
        from app.subscription.handler import detect_command
        cmd = detect_command("设置推送时间 早上9点")
        assert cmd == ("set_time", "09:00")

    def test_set_time_12pm(self):
        from app.subscription.handler import detect_command
        cmd = detect_command("设置推送时间 中午12点")
        assert cmd == ("set_time", "12:00")

    def test_set_time_6pm(self):
        from app.subscription.handler import detect_command
        cmd = detect_command("设置推送时间 下午6点")
        assert cmd == ("set_time", "18:00")

    def test_set_time_short(self):
        from app.subscription.handler import detect_command
        assert detect_command("推送时间 9:00") == ("set_time", "09:00")

    def test_set_freq_daily(self):
        from app.subscription.handler import detect_command
        cmd = detect_command("设置推送频率 每天")
        assert cmd == ("set_freq", "daily")

    def test_set_freq_weekdays(self):
        from app.subscription.handler import detect_command
        cmd = detect_command("设置推送频率 工作日")
        assert cmd == ("set_freq", "weekdays")

    def test_set_freq_weekly(self):
        from app.subscription.handler import detect_command
        cmd = detect_command("设置推送频率 每周一")
        assert cmd == ("set_freq", "weekly_monday")

    def test_set_freq_short(self):
        from app.subscription.handler import detect_command
        assert detect_command("推送频率 仅工作日") == ("set_freq", "weekdays")


class TestPreferences:
    """偏好 CRUD 测试"""

    def setup_method(self):
        """每个测试前清除缓存，避免跨测试污染"""
        from app.core.cache import chat_pref_cache
        chat_pref_cache.clear()

    @patch("app.db.sql_repositories.SessionLocal")
    def test_get_preference_default(self, mock_session):
        """无记录时返回默认值"""
        from app.subscription.handler import get_preference

        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = None
        mock_session.return_value = mock_db

        pref = get_preference("chat_001")
        assert pref == {"push_time": "09:00", "frequency": "daily"}

    @patch("app.db.sql_repositories.SessionLocal")
    def test_get_preference_existing(self, mock_session):
        """有记录时返回存储值"""
        from app.subscription.handler import get_preference

        mock_pref = MagicMock()
        mock_pref.push_time = "18:00"
        mock_pref.frequency = "weekly_monday"
        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = mock_pref
        mock_session.return_value = mock_db

        pref = get_preference("chat_001")
        assert pref == {"push_time": "18:00", "frequency": "weekly_monday"}

    @patch("app.db.sql_repositories.SessionLocal")
    def test_set_push_time_update(self, mock_session):
        """更新已有偏好的推送时间"""
        from app.subscription.handler import set_push_time

        mock_pref = MagicMock()
        mock_pref.push_time = "09:00"
        mock_pref.frequency = "daily"
        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = mock_pref
        mock_session.return_value = mock_db

        pref = set_push_time("chat_001", "18:00")
        assert mock_pref.push_time == "18:00"
        assert pref["push_time"] == "18:00"

    @patch("app.db.sql_repositories.SessionLocal")
    def test_set_frequency_update(self, mock_session):
        """更新已有偏好的频率"""
        from app.subscription.handler import set_frequency

        mock_pref = MagicMock()
        mock_pref.push_time = "09:00"
        mock_pref.frequency = "daily"
        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = mock_pref
        mock_session.return_value = mock_db

        pref = set_frequency("chat_001", "weekdays")
        assert mock_pref.frequency == "weekdays"
        assert pref["frequency"] == "weekdays"


class TestFrequencyLogic:
    """频率判断逻辑测试"""

    @patch("app.subscription.handler.date")
    def test_daily_always_true(self, mock_date):
        from app.subscription.handler import is_today_in_frequency
        mock_date.today.return_value = MagicMock(weekday=lambda: 6)  # Sunday
        assert is_today_in_frequency("daily") is True

    @patch("app.subscription.handler.date")
    def test_weekdays_monday_true(self, mock_date):
        from app.subscription.handler import is_today_in_frequency
        mock_date.today.return_value = MagicMock(weekday=lambda: 0)  # Monday
        assert is_today_in_frequency("weekdays") is True

    @patch("app.subscription.handler.date")
    def test_weekdays_saturday_false(self, mock_date):
        from app.subscription.handler import is_today_in_frequency
        mock_date.today.return_value = MagicMock(weekday=lambda: 5)  # Saturday
        assert is_today_in_frequency("weekdays") is False

    @patch("app.subscription.handler.date")
    def test_weekly_monday_true(self, mock_date):
        from app.subscription.handler import is_today_in_frequency
        mock_date.today.return_value = MagicMock(weekday=lambda: 0)  # Monday
        assert is_today_in_frequency("weekly_monday") is True

    @patch("app.subscription.handler.date")
    def test_weekly_tuesday_false(self, mock_date):
        from app.subscription.handler import is_today_in_frequency
        mock_date.today.return_value = MagicMock(weekday=lambda: 1)  # Tuesday
        assert is_today_in_frequency("weekly_monday") is False


class TestSettingsCard:
    """设置面板卡片测试"""

    def test_settings_card_shows_current_time(self):
        from app.feishu.card_builder import build_settings_card

        card = build_settings_card(["OpenAI"], push_time="12:00", frequency="daily")
        card_str = str(card)
        assert "中午 12:00" in card_str

    def test_settings_card_shows_current_freq(self):
        from app.feishu.card_builder import build_settings_card

        card = build_settings_card(["OpenAI"], push_time="09:00", frequency="weekdays")
        card_str = str(card)
        assert "仅工作日" in card_str

    def test_settings_card_shows_subscriptions(self):
        from app.feishu.card_builder import build_settings_card

        card = build_settings_card(["OpenAI", "DeepSeek"], push_time="09:00", frequency="daily")
        card_str = str(card)
        assert "OpenAI" in card_str
        assert "DeepSeek" in card_str

    def test_settings_card_no_subs(self):
        from app.feishu.card_builder import build_settings_card

        card = build_settings_card([], push_time="09:00", frequency="daily")
        card_str = str(card)
        assert "尚未订阅" in card_str

    def test_news_card_has_settings_button(self):
        from app.feishu.card_builder import build_news_card

        card = build_news_card(
            title="Test", vendor="OpenAI",
            summary_points=["A"], raw_url="https://x.com",
        )
        card_str = str(card)
        assert "⚙️" in card_str


class TestChatLifecycle:
    """Chat 生命周期测试"""

    @patch("app.db.sql_repositories.SessionLocal")
    def test_register_new_chat(self, mock_session):
        """测试首次注册"""
        from app.chat.lifecycle import register_chat

        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = None
        mock_session.return_value = mock_db

        is_new = register_chat("chat_001", "group")
        assert is_new is True

    @patch("app.db.sql_repositories.SessionLocal")
    def test_register_existing_chat(self, mock_session):
        """测试重新激活已有 chat"""
        from app.chat.lifecycle import register_chat

        mock_entry = MagicMock()
        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = mock_entry
        mock_session.return_value = mock_db

        is_new = register_chat("chat_001", "group")
        assert is_new is False
        assert mock_entry.is_active is True

    @patch("app.db.sql_repositories.SessionLocal")
    def test_deactivate_chat(self, mock_session):
        """测试标记 chat 为 inactive"""
        from app.chat.lifecycle import deactivate_chat

        mock_entry = MagicMock()
        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = mock_entry
        mock_session.return_value = mock_db

        deactivate_chat("chat_001")
        assert mock_entry.is_active is False

    @patch("app.db.sql_repositories.SessionLocal")
    def test_is_new_chat_true(self, mock_session):
        from app.chat.lifecycle import is_new_chat

        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = None
        mock_session.return_value = mock_db
        assert is_new_chat("chat_new") is True

    @patch("app.db.sql_repositories.SessionLocal")
    def test_get_active_chat_ids(self, mock_session):
        from app.chat.lifecycle import get_active_chat_ids

        e1, e2 = MagicMock(), MagicMock()
        e1.conversation_id, e2.conversation_id = "chat_001", "chat_002"
        e1.chat_type, e2.chat_type = "group", "user"
        e1.platform = e2.platform = "feishu"
        mock_db = MagicMock()
        mock_db.query().filter().filter().all.return_value = [e1, e2]
        mock_session.return_value = mock_db

        ids = get_active_chat_ids()
        assert ids == ["chat_001", "chat_002"]

    def test_get_active_chats_filters_by_platform(self):
        """活跃会话必须按平台隔离

        历史缺陷：get_active_chats 不带 platform 过滤，send_feishu 节点会把
        Discord 频道 ID / Telegram chat ID 一并取出交给 FeishuClient 发送。
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.models import Base, ChatRegistry
        import app.db.sql_repositories as repo_mod
        from app.db.sql_repositories import SqlChatRegistryRepository

        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine)

        db = SessionFactory()
        for platform, conv in [
            ("feishu", "oc_feishu_1"),
            ("feishu", "oc_feishu_2"),
            ("discord", "555000111"),
            ("telegram", "-100200300"),
        ]:
            db.add(ChatRegistry(platform=platform, conversation_id=conv,
                                chat_id=conv, chat_type="group", is_active=True))
        # 非活跃会话不应出现在任何平台的结果里
        db.add(ChatRegistry(platform="feishu", conversation_id="oc_dead",
                            chat_id="oc_dead", chat_type="group", is_active=False))
        db.commit()
        db.close()

        with patch.object(repo_mod, "SessionLocal", SessionFactory):
            repo = SqlChatRegistryRepository()
            assert sorted(repo.get_active_chat_ids(platform="feishu")) == [
                "oc_feishu_1", "oc_feishu_2"
            ]
            assert repo.get_active_chat_ids(platform="discord") == ["555000111"]
            assert repo.get_active_chat_ids(platform="telegram") == ["-100200300"]
            # platform=None 显式跨平台，且带回 platform 字段供调用方分流
            all_chats = repo.get_active_chats(platform=None)
            assert len(all_chats) == 4
            assert {c["platform"] for c in all_chats} == {"feishu", "discord", "telegram"}

    def test_send_feishu_targets_exclude_other_platforms(self):
        """send_feishu 节点只应拿到飞书会话"""
        from app.graph.nodes import send_feishu

        with patch("app.chat.lifecycle.get_active_chat_ids") as mock_ids, \
             patch.object(send_feishu, "get_subscribers", return_value=[]), \
             patch.object(send_feishu, "has_any_subscription", return_value=False):
            mock_ids.return_value = ["oc_feishu_1"]
            send_feishu._resolve_targets("OpenAI")
            mock_ids.assert_called_once_with(platform="feishu")


class TestPermissions:
    """权限控制测试"""

    def test_can_manage_user_chat(self):
        """私聊中用户总是有权限"""
        from app.chat.lifecycle import can_manage_subscription

        with patch("app.chat.lifecycle.get_chat_type", return_value="user"):
            assert can_manage_subscription("ou_123", "ou_123") is True

    def test_can_manage_group_owner(self):
        """群主有权限"""
        from app.chat.lifecycle import can_manage_subscription

        with patch("app.chat.lifecycle.get_chat_type", return_value="group"), \
             patch("app.chat.lifecycle.get_owner_id", return_value="ou_owner"):
            assert can_manage_subscription("oc_group", "ou_owner") is True

    def test_cannot_manage_group_non_owner(self):
        """非群主没权限"""
        from app.chat.lifecycle import can_manage_subscription

        with patch("app.chat.lifecycle.get_chat_type", return_value="group"), \
             patch("app.chat.lifecycle.get_owner_id", return_value="ou_owner"):
            assert can_manage_subscription("oc_group", "ou_random") is False

    def test_can_manage_unknown_owner_fail_open(self):
        """无法确认群主时放行"""
        from app.chat.lifecycle import can_manage_subscription

        with patch("app.chat.lifecycle.get_chat_type", return_value="group"), \
             patch("app.chat.lifecycle.get_owner_id", return_value=None):
            assert can_manage_subscription("oc_group", "ou_anyone") is True


class TestFirstMessageFix:
    """首条消息修复：私聊首条订阅命令应执行，而非发引导卡片"""

    def test_is_new_chat_detect(self):
        """is_new_chat 检测"""
        from app.chat.lifecycle import is_new_chat

        with patch("app.db.sql_repositories.SessionLocal") as mock_sess:
            mock_db = MagicMock()
            mock_db.query().filter_by().first.return_value = None
            mock_sess.return_value = mock_db
            assert is_new_chat("new_chat") is True


class TestWelcomeCards:
    """入驻卡片测试"""

    def test_group_welcome_card(self):
        from app.feishu.card_builder import build_group_welcome_card

        card = build_group_welcome_card()
        card_str = str(card)
        assert "已就位" in card_str
        assert "默认订阅" in card_str
        assert "退订" in card_str
        assert "settings" in card_str

    def test_welcome_card_has_subscribe_guide(self):
        """私聊欢迎卡片应包含订阅引导"""
        from app.feishu.card_builder import build_welcome_card

        card = build_welcome_card()
        card_str = str(card)
        assert "订阅" in card_str
        assert "OpenAI" in card_str
