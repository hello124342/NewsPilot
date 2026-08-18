"""Discord 平台适配器测试

覆盖：配置、注册表、渲染器（Embed/按钮）、适配器（发送/权限）、
权限模型分支、网关辅助函数（艾特剥离/去重/线程池安全包装）。
"""

import pytest
from unittest.mock import patch, MagicMock

from app.platforms.message_model import RichMessage, ActionButton, CallbackData


class TestDiscordConfig:
    """Discord 配置测试"""

    def test_discord_configured_true(self):
        from app.core.config import Settings
        s = Settings(DISCORD_BOT_TOKEN="token_abc")
        assert s.discord_configured is True

    def test_discord_configured_false(self):
        from app.core.config import Settings
        s = Settings(DISCORD_BOT_TOKEN="")
        assert s.discord_configured is False


class TestDiscordRegistry:
    """注册表测试"""

    def test_registry_includes_discord(self):
        from app.platforms.registry import list_all_platforms, list_available_platforms
        assert "discord" in list_all_platforms()

        from app.core.config import Settings
        # 未配置时不出现在可用平台
        s = Settings(DISCORD_BOT_TOKEN="")
        assert "discord" not in list_available_platforms(s)

        # 配置后出现
        s2 = Settings(DISCORD_BOT_TOKEN="token")
        assert "discord" in list_available_platforms(s2)

    def test_get_platform_adapter_discord(self):
        from app.platforms.registry import get_platform_adapter
        from app.core.config import Settings
        s = Settings(DISCORD_BOT_TOKEN="token")
        adapter = get_platform_adapter("discord", s)
        assert adapter is not None
        assert adapter.get_platform_name() == "discord"
        assert adapter.get_platform_label() == "Discord"


class TestDiscordRenderer:
    """渲染器测试：RichMessage → Embed + 按钮组件"""

    @pytest.fixture
    def message(self):
        return RichMessage(
            title="🤖 OpenAI",
            body="**核心要点**\n1. 第一条\n2. 第二条",
            color_hint="success",
            footer="📅 2026-08-18",
        )

    def test_embed_basic(self, message):
        from app.platforms.discord.renderer import render_embed
        embed = render_embed(message)
        assert embed["type"] == "rich"
        assert embed["title"] == "🤖 OpenAI"
        assert embed["description"].startswith("**核心要点**")
        assert embed["footer"] == {"text": "📅 2026-08-18"}

    def test_embed_color_mapping(self):
        from app.platforms.discord.renderer import render_embed
        # success → 绿色 0x57F287
        assert render_embed(RichMessage(body="x", color_hint="success"))["color"] == 0x57F287
        # warning → 黄色 0xFEE75C
        assert render_embed(RichMessage(body="x", color_hint="warning"))["color"] == 0xFEE75C
        # info / 默认 → blurple 0x5865F2
        assert render_embed(RichMessage(body="x", color_hint="info"))["color"] == 0x5865F2
        assert render_embed(RichMessage(body="x"))["color"] == 0x5865F2

    def test_embed_truncation(self):
        from app.platforms.discord.renderer import render_embed
        long_title = "T" * 500
        long_body = "B" * 5000
        embed = render_embed(RichMessage(title=long_title, body=long_body))
        assert len(embed["title"]) <= 256
        assert len(embed["description"]) <= 4096
        assert embed["description"].endswith("...")

    def test_components_url_button(self):
        from app.platforms.discord.renderer import render_components
        msg = RichMessage(body="x", buttons=[
            ActionButton(label="📖 阅读原文", action="url", value="https://a.com", style="primary"),
        ])
        comps = render_components(msg)
        assert comps[0]["style"] == 5  # link
        assert comps[0]["url"] == "https://a.com"
        assert "custom_id" not in comps[0]

    def test_components_callback_button_roundtrip(self):
        from app.platforms.discord.renderer import render_components
        msg = RichMessage(body="x", buttons=[
            ActionButton(label="🔕 退订 OpenAI", action="callback",
                         value='{"action":"unsubscribe","vendor":"OpenAI"}', style="danger"),
        ])
        comps = render_components(msg)
        assert comps[0]["style"] == 4  # danger
        # custom_id 可被 CallbackData.from_json 还原
        cb = CallbackData.from_json(comps[0]["custom_id"])
        assert cb.action == "unsubscribe"
        assert cb.params == {"vendor": "OpenAI"}

    def test_components_max_buttons(self):
        from app.platforms.discord.renderer import render_components
        buttons = [ActionButton(label=f"b{i}", action="url", value=f"https://a.com/{i}") for i in range(8)]
        comps = render_components(RichMessage(body="x", buttons=buttons))
        assert len(comps) == 5  # Discord 每行最多 5 个


class TestDiscordAdapter:
    """适配器测试：发送与权限"""

    @pytest.fixture
    def settings(self):
        from app.core.config import Settings
        return Settings(DISCORD_BOT_TOKEN="test_token")

    def test_send_message_success(self, settings):
        from app.platforms.discord.adapter import DiscordAdapter
        mock_channel = MagicMock()
        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel
        sent = MagicMock()
        sent.id = 12345
        fut = MagicMock()
        fut.result.return_value = sent

        with patch("app.platforms.discord.gateway.get_client", return_value=mock_client), \
             patch("app.platforms.discord.adapter.asyncio.run_coroutine_threadsafe", return_value=fut):
            adapter = DiscordAdapter(settings)
            result = adapter.send_message("111", RichMessage(title="t", body="b"))

        assert result["success"] is True
        assert result["message_id"] == "12345"
        mock_channel.send.assert_called_once()
        # 跨线程投递到网关 loop
        fut.result.assert_called_once()

    def test_send_message_channel_not_found(self, settings):
        from app.platforms.discord.adapter import DiscordAdapter
        mock_client = MagicMock()
        mock_client.get_channel.return_value = None
        with patch("app.platforms.discord.gateway.get_client", return_value=mock_client):
            adapter = DiscordAdapter(settings)
            with pytest.raises(RuntimeError, match="not found"):
                adapter.send_message("111", RichMessage(body="b"))

    def test_send_message_gateway_not_started(self, settings):
        from app.platforms.discord.adapter import DiscordAdapter
        with patch("app.platforms.discord.gateway.get_client", return_value=None):
            adapter = DiscordAdapter(settings)
            with pytest.raises(RuntimeError, match="not started"):
                adapter.send_message("111", RichMessage(body="b"))

    def test_send_message_api_failure_raises(self, settings):
        from app.platforms.discord.adapter import DiscordAdapter
        mock_channel = MagicMock()
        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel
        fut = MagicMock()
        fut.result.side_effect = RuntimeError("boom")
        with patch("app.platforms.discord.gateway.get_client", return_value=mock_client), \
             patch("app.platforms.discord.adapter.asyncio.run_coroutine_threadsafe", return_value=fut):
            adapter = DiscordAdapter(settings)
            with pytest.raises(RuntimeError, match="boom"):
                adapter.send_message("111", RichMessage(body="b"))

    def test_is_admin_administrator(self, settings):
        from app.platforms.discord.adapter import DiscordAdapter
        mock_guild = MagicMock()
        mock_channel = MagicMock()
        mock_channel.guild = mock_guild
        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel
        member = MagicMock()
        member.guild_permissions.administrator = True
        member.guild_permissions.manage_guild = False
        fut = MagicMock()
        fut.result.return_value = member
        with patch("app.platforms.discord.gateway.get_client", return_value=mock_client), \
             patch("app.platforms.discord.adapter.asyncio.run_coroutine_threadsafe", return_value=fut):
            adapter = DiscordAdapter(settings)
            assert adapter.is_admin("111", "222") is True

    def test_is_admin_regular_member(self, settings):
        from app.platforms.discord.adapter import DiscordAdapter
        mock_channel = MagicMock()
        mock_channel.guild = MagicMock()
        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel
        member = MagicMock()
        member.guild_permissions.administrator = False
        member.guild_permissions.manage_guild = False
        fut = MagicMock()
        fut.result.return_value = member
        with patch("app.platforms.discord.gateway.get_client", return_value=mock_client), \
             patch("app.platforms.discord.adapter.asyncio.run_coroutine_threadsafe", return_value=fut):
            adapter = DiscordAdapter(settings)
            assert adapter.is_admin("111", "222") is False

    def test_is_admin_dm_channel(self, settings):
        from app.platforms.discord.adapter import DiscordAdapter
        mock_channel = MagicMock()
        mock_channel.guild = None  # 私聊 DM
        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel
        with patch("app.platforms.discord.gateway.get_client", return_value=mock_client):
            adapter = DiscordAdapter(settings)
            assert adapter.is_admin("111", "222") is False

    def test_is_configured(self, settings):
        from app.platforms.discord.adapter import DiscordAdapter
        assert DiscordAdapter(settings).is_configured() is True


class TestDiscordPermission:
    """权限模型：can_manage_subscription 的 discord 分支"""

    def _adapter(self, is_admin_result):
        adapter = MagicMock()
        adapter.is_admin.return_value = is_admin_result
        return adapter

    def test_group_admin_allowed(self):
        from app.chat import lifecycle
        with patch("app.chat.lifecycle.get_chat_type", return_value="group"):
            assert lifecycle.can_manage_subscription(
                "111", "222", platform="discord", platform_adapter=self._adapter(True)
            ) is True

    def test_group_non_admin_denied(self):
        from app.chat import lifecycle
        with patch("app.chat.lifecycle.get_chat_type", return_value="group"):
            assert lifecycle.can_manage_subscription(
                "111", "222", platform="discord", platform_adapter=self._adapter(False)
            ) is False

    def test_is_admin_exception_fail_open(self):
        from app.chat import lifecycle
        adapter = MagicMock()
        adapter.is_admin.side_effect = Exception("boom")
        with patch("app.chat.lifecycle.get_chat_type", return_value="group"):
            assert lifecycle.can_manage_subscription(
                "111", "222", platform="discord", platform_adapter=adapter
            ) is True

    def test_dm_always_allowed(self):
        from app.chat import lifecycle
        with patch("app.chat.lifecycle.get_chat_type", return_value="user"):
            assert lifecycle.can_manage_subscription("111", "222", platform="discord") is True

    def test_repo_layer_discord_branch(self):
        """sql_repositories 层的权限副本同样支持 discord"""
        from app.db.sql_repositories import SqlChatRegistryRepository
        repo = SqlChatRegistryRepository()
        adapter = self._adapter(True)
        with patch.object(repo, "get_type", return_value="group"):
            assert repo.can_manage_subscription("111", "222", platform="discord", platform_adapter=adapter) is True


class TestDiscordGateway:
    """网关辅助函数测试"""

    def test_strip_mention(self):
        from app.platforms.discord.gateway import _strip_mention
        assert _strip_mention("<@12345> 订阅 OpenAI", 12345) == "订阅 OpenAI"
        assert _strip_mention("<@!12345>  订阅列表", 12345) == "订阅列表"
        assert _strip_mention("你好 <@12345>", 12345) == "你好"

    def test_is_bot_mentioned(self):
        from app.platforms.discord.gateway import _is_bot_mentioned
        client = MagicMock()
        client.user.id = 12345

        msg = MagicMock()
        msg.content = "<@12345> 你好"
        msg.mentions = []
        assert _is_bot_mentioned(msg, client) is True

        # 昵称格式提及
        msg.content = "<@!12345> 你好"
        assert _is_bot_mentioned(msg, client) is True

        # 未提及 bot
        msg.content = "普通消息"
        assert _is_bot_mentioned(msg, client) is False

    def test_dedup(self):
        from app.platforms.discord.gateway import _dedup
        assert _dedup("m1") is False  # 首次不重复
        assert _dedup("m1") is True   # 重复
        assert _dedup("m2") is False  # 新消息

    def test_start_not_configured_returns_none(self, monkeypatch):
        from app.platforms.discord import gateway
        from app.core.config import Settings
        monkeypatch.setattr(gateway, "_settings", Settings(DISCORD_BOT_TOKEN=""))
        monkeypatch.setattr(gateway, "_thread", None)
        assert gateway.start() is None


class TestDiscordGatewayOnMessage:
    """网关 on_message 回归测试

    历史 bug：on_message 中引用了未赋值的局部变量 sender_id（只在 IncomingMessage
    的关键字参数里出现过），导致每条 @Bot 消息都抛 NameError，Discord 查询全线不可用。
    该分支此前无任何测试覆盖。
    """

    def _fake_message(self, bot_uid=12345):
        msg = MagicMock()
        msg.author.bot = False
        msg.author.id = 777
        msg.id = 999
        msg.channel.id = 555
        msg.guild = MagicMock()  # 非 DM
        msg.content = f"<@{bot_uid}> 订阅 OpenAI"
        msg.mentions = []
        return msg

    def _build_client_with_user(self, bot_uid=12345):
        """构建 client 并伪造 client.user（Client.user 是只读 property）"""
        import discord
        from unittest.mock import PropertyMock
        from app.platforms.discord.gateway import _build_client

        client = _build_client()
        fake_user = MagicMock()
        fake_user.id = bot_uid
        patcher = patch.object(type(client), "user", new_callable=PropertyMock,
                               return_value=fake_user)
        patcher.start()
        return client, patcher

    def test_on_message_submits_with_sender_id(self, monkeypatch):
        """@Bot 消息应被解析并派发到查询池，且带上 sender_id 用于限流"""
        pytest.importorskip("discord")
        import asyncio
        from app.platforms.discord import gateway

        monkeypatch.setattr(gateway, "_on_message", lambda incoming: None)
        monkeypatch.setattr(gateway, "_seen_messages", gateway.OrderedDict())

        captured = {}

        def fake_submit(fn, *args, user_id=None, chat_id=None):
            captured["args"] = args
            captured["user_id"] = user_id
            captured["chat_id"] = chat_id
            return True

        monkeypatch.setattr(gateway, "_submit_query", fake_submit)

        client, patcher = self._build_client_with_user()
        try:
            asyncio.run(client.on_message(self._fake_message()))
        finally:
            patcher.stop()

        assert captured, "on_message 未派发任何任务（历史上此处抛 NameError）"
        assert captured["user_id"] == "777"   # 限流按发送者维度
        assert captured["chat_id"] == "555"   # 繁忙提示回到原频道

        incoming = captured["args"][0]
        assert incoming.platform == "discord"
        assert incoming.sender_id == "777"
        assert incoming.chat_id == "555"
        assert incoming.text == "订阅 OpenAI"   # @提及已剥离
        assert incoming.raw_payload["is_dm"] is False

    def test_on_message_ignores_bot_and_non_mention(self, monkeypatch):
        """机器人自己的消息、未 @Bot 的消息都不应进入查询池"""
        pytest.importorskip("discord")
        import asyncio
        from app.platforms.discord import gateway

        monkeypatch.setattr(gateway, "_on_message", lambda incoming: None)
        monkeypatch.setattr(gateway, "_seen_messages", gateway.OrderedDict())
        calls = []
        monkeypatch.setattr(gateway, "_submit_query",
                            lambda fn, *a, **kw: calls.append(a) or True)

        client, patcher = self._build_client_with_user()
        try:
            bot_msg = self._fake_message()
            bot_msg.author.bot = True
            asyncio.run(client.on_message(bot_msg))

            plain = self._fake_message()
            plain.content = "普通聊天，没有提及"
            asyncio.run(client.on_message(plain))
        finally:
            patcher.stop()

        assert calls == []
