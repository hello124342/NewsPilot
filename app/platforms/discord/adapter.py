"""Discord 平台适配器

DiscordAdapter 基于 discord.py 实现 PlatformAdapter 接口。
- 发送侧：通过 gateway.py 网关线程中的 discord.Client 投递 Embed + Button。
  （client 运行在独立 asyncio loop，跨线程用 asyncio.run_coroutine_threadsafe）
- 接收侧：由 gateway.py 的网关处理（on_message / on_interaction / guild 生命周期）。

事件接收无需公网 URL（网关模式），与飞书 WebSocket 同构。
"""

import asyncio
import logging

from app.core.config import Settings
from app.core.metrics import track_platform_send
from app.platforms.adapter import PlatformAdapter
from app.platforms.message_model import RichMessage, ConversationInfo
from app.platforms.discord.renderer import render_embed, render_components

logger = logging.getLogger(__name__)

# 尝试导入 discord.py（可选依赖，未安装时平台静默降级）
try:
    import discord
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False
    discord = None
    logger.warning("discord.py not installed. Discord platform will be unavailable.")


def _build_view(button_specs: list[dict]):
    """将 renderer 产出的按钮组件 spec 构建为 discord.ui.View

    Args:
        button_specs: render_components() 的返回值

    Returns:
        discord.ui.View 或 None（无按钮时）
    """
    if discord is None or not button_specs:
        return None

    view = discord.ui.View()
    style_map = {
        1: discord.ButtonStyle.primary,
        2: discord.ButtonStyle.secondary,
        4: discord.ButtonStyle.danger,
    }
    for spec in button_specs:
        if spec.get("url"):
            btn = discord.ui.Button(
                style=discord.ButtonStyle.link,
                label=spec["label"],
                url=spec["url"],
            )
        else:
            btn = discord.ui.Button(
                style=style_map.get(spec.get("style", 2), discord.ButtonStyle.secondary),
                label=spec["label"],
                custom_id=spec.get("custom_id") or None,
            )
        view.add_item(btn)
    return view


class DiscordAdapter(PlatformAdapter):
    """Discord 平台适配器

    构造：registry 调用 `DiscordAdapter(settings)`。
    发送前需先由 main.py lifespan 启动网关（gateway.start()），
    否则 get_client() 返回 None，send_message 抛 RuntimeError。
    """

    def __init__(self, settings: Settings):
        """初始化 Discord 适配器

        Args:
            settings: 应用配置（需要 DISCORD_BOT_TOKEN）
        """
        self._settings = settings

        if not DISCORD_AVAILABLE:
            logger.error("Cannot initialize DiscordAdapter: discord.py not installed")
            return
        if not settings.DISCORD_BOT_TOKEN:
            logger.warning("DISCORD_BOT_TOKEN is empty, Discord adapter will not function")
            return
        logger.info("DiscordAdapter initialized")

    # ========== 平台元信息 ==========

    def get_platform_name(self) -> str:
        return "discord"

    def get_platform_label(self) -> str:
        return "Discord"

    # ========== 消息发送 ==========

    @track_platform_send("discord")
    def send_message(self, conversation_id: str, message: RichMessage) -> dict:
        """发送富文本消息到 Discord 频道

        RichMessage → Embed + Button 组件 → discord.TextChannel.send。
        client 运行在网关线程的独立 loop 中，用 run_coroutine_threadsafe 投递。

        Args:
            conversation_id: Discord channel_id（snowflake 字符串）
            message: 平台无关的 RichMessage

        Returns:
            {"success": True, "message_id": str}

        Raises:
            RuntimeError: 网关未启动 / 频道不存在 / API 调用失败
        """
        if discord is None:
            raise RuntimeError("Discord adapter not properly initialized (discord.py missing)")

        # 延迟导入避免循环依赖（gateway 依赖 adapter 的回调类型）
        from app.platforms.discord.gateway import get_client

        client = get_client()
        if client is None:
            raise RuntimeError("Discord gateway not started, cannot send message")

        try:
            channel = client.get_channel(int(conversation_id))
        except (TypeError, ValueError):
            channel = None
        if channel is None:
            raise RuntimeError(f"Discord channel not found: {conversation_id}")

        embed = discord.Embed.from_dict(render_embed(message))
        view = _build_view(render_components(message))

        try:
            coro = channel.send(embed=embed, view=view)
            sent = asyncio.run_coroutine_threadsafe(coro, client.loop).result(timeout=10)
        except Exception as e:
            logger.error(f"Discord send_message failed to {conversation_id}: {e}")
            raise RuntimeError(f"Discord send_message failed: {e}") from e

        logger.debug(f"Discord message {sent.id} sent to {conversation_id}")
        return {"success": True, "message_id": str(sent.id)}

    # ========== 对话信息 ==========

    def get_conversation_info(self, conversation_id: str) -> ConversationInfo | None:
        """查询 Discord 频道信息

        Args:
            conversation_id: Discord channel_id

        Returns:
            ConversationInfo，失败返回 None
        """
        from app.platforms.discord.gateway import get_client

        client = get_client()
        if client is None:
            return None
        try:
            channel = client.get_channel(int(conversation_id))
        except (TypeError, ValueError):
            channel = None
        if channel is None:
            return None

        guild = getattr(channel, "guild", None)
        return ConversationInfo(
            id=str(channel.id),
            name=getattr(channel, "name", None) or str(channel.id),
            owner_id="",  # Discord 不直接暴露频道/服务器 owner，走 is_admin 判定
            is_group=guild is not None,
        )

    # ========== 权限判断（群管理员）==========

    def is_admin(self, conversation_id: str, user_id: str) -> bool:
        """检查用户在频道所属服务器是否为管理员

        Discord 权限模型：拥有 administrator 或 manage_guild 权限即视为管理员。
        conversation_id 是 channel_id，先解析出 guild，再查询成员权限。
        用 fetch_member（API 调用）而非 get_member（缓存），无需开启 Members Intent。

        Args:
            conversation_id: Discord channel_id
            user_id: Discord user_id

        Returns:
            True 表示用户是服务器管理员/拥有者
        """
        from app.platforms.discord.gateway import get_client

        client = get_client()
        if client is None:
            return False
        try:
            channel = client.get_channel(int(conversation_id))
        except (TypeError, ValueError):
            channel = None
        if channel is None:
            return False

        guild = getattr(channel, "guild", None)
        if guild is None:
            return False  # 私聊 DM，无管理员概念

        try:
            member = asyncio.run_coroutine_threadsafe(
                guild.fetch_member(int(user_id)), client.loop
            ).result(timeout=10)
        except Exception as e:
            logger.warning(
                f"Discord fetch_member failed for {conversation_id}/{user_id}: {e}"
            )
            return False

        if member is None:
            return False
        return bool(member.guild_permissions.administrator or member.guild_permissions.manage_guild)

    # ========== 配置检查 ==========

    def is_configured(self) -> bool:
        """检查 Discord 凭证是否已配置"""
        return self._settings.discord_configured
