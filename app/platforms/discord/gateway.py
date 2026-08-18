"""Discord 网关事件接收（daemon 线程 + 独立 asyncio loop）

与飞书 ws_client.py 同构：后台线程运行 discord.py Client，
网关事件在独立 event loop 中处理，不影响 FastAPI 主循环。

并发模型（Producer-Consumer，镜像 feishu/event_router.py）：
- Producer: 网关 loop 收到事件 → executor.submit()
- Consumer: ThreadPoolExecutor(max_workers=5) 执行业务逻辑（BotQueryGraph 等）

关键：业务逻辑（同步 LLM 调用 1-30s）绝不阻塞网关 loop，
否则心跳无法发送导致断连，且所有 Discord 用户会串行排队。

事件类型：
- on_message        → 频道消息（仅处理 @Bot 触发）→ _on_message 回调
- on_interaction    → 按钮交互（component）→ _on_callback 回调
- on_guild_join     → Bot 被加入服务器 → onboard（注册+自动订阅+欢迎）
- on_guild_remove   → Bot 被移出服务器 → deactivate
- on_ready          → 对已存在的服务器做 onboard（覆盖重启场景）
"""

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Optional

from app.core.query_executor import submit as query_submit, QuerySubmitStatus
from app.platforms.adapter import MessageCallback, CallbackActionHandler
from app.platforms.message_model import IncomingMessage, CallbackData

logger = logging.getLogger(__name__)

# 尝试导入 discord.py（可选依赖，未安装时平台静默降级）
try:
    import discord
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False
    discord = None
    logger.warning("discord.py not installed. Discord gateway will be unavailable.")


# ========== 事件处理：统一派发到共享查询池 ==========
# 并发模型（Producer-Consumer，见 app/core/query_executor.py）：
# - Producer: 网关 loop → query_executor.submit() 立即返回，不阻塞
# - Consumer: ThreadPoolExecutor(QUERY_MAX_WORKERS) 执行业务逻辑
# 关键：业务逻辑（同步 LLM 调用 1-30s）绝不阻塞网关 loop，
# 否则心跳无法发送导致断连，且所有 Discord 用户会串行排队。

# 消息去重缓存（避免网关重连导致的重复事件）
_seen_messages: OrderedDict[str, bool] = OrderedDict()
_MAX_DEDUP = 2000

# guild_id → 默认频道 id（加群时选定，退群时据此 deactivate）
_guild_default_channel: dict[int, int] = {}

# 网关全局状态
_client: Optional["discord.Client"] = None
_thread: Optional[threading.Thread] = None
_settings = None

# 业务回调（由 main.py lifespan 通过 configure() 设置）
_on_message: Optional[MessageCallback] = None
_on_callback: Optional[CallbackActionHandler] = None


# ========== 配置与回调 ==========

def configure(settings, on_message: MessageCallback, on_callback: CallbackActionHandler) -> None:
    """配置网关回调处理器

    在 main.py startup 时调用，注册消息和按钮回调的处理函数。

    Args:
        settings: 应用配置
        on_message: 收到文本消息时的处理回调
        on_callback: 收到按钮回调时的处理回调
    """
    global _settings, _on_message, _on_callback
    _settings = settings
    _on_message = on_message
    _on_callback = on_callback
    logger.info("Discord gateway callbacks configured")


# ========== 去重 ==========

def _dedup(message_id: str) -> bool:
    """检查消息是否已处理过。返回 True 表示重复。"""
    if not message_id:
        return False
    if message_id in _seen_messages:
        return True
    _seen_messages[message_id] = True
    if len(_seen_messages) > _MAX_DEDUP:
        _seen_messages.popitem(last=False)
    return False


# ========== 派发到共享查询池 ==========

def _submit_query(fn, *args, user_id=None, chat_id=None) -> bool:
    """将任务卸载到共享查询池（网关 loop 立即返回，不阻塞）

    Returns:
        True 表示已接受；False 表示被丢弃（队列满 / 限流），
        此时向用户回一条提示消息。
    """
    status = query_submit(fn, *args, user_id=user_id)
    if status is not QuerySubmitStatus.ACCEPTED:
        _notify_dropped(chat_id, status)
        return False
    return True


def _notify_dropped(channel_id: Optional[str], status: QuerySubmitStatus) -> None:
    """查询被丢弃时给用户回一条提示（队列满 / 限流）"""
    if not channel_id:
        return
    try:
        from app.platforms.registry import get_platform_adapter
        from app.platforms.message_model import RichMessage

        adapter = get_platform_adapter("discord", _settings)
        if adapter is None:
            return
        body = "🐌 操作太频繁，请稍后再试。" if status is QuerySubmitStatus.RATE_LIMITED else "⛔ 系统繁忙，请稍后再试。"
        adapter.send_message(channel_id, RichMessage(body=body, color_hint="warning"))
    except Exception as e:
        logger.warning(f"Failed to send busy message to {channel_id}: {e}")


# ========== 消息过滤与解析 ==========

def _is_bot_mentioned(message, client) -> bool:
    """判断消息是否 @ 了 bot（按内容中的 mention 串判断，不依赖 Members Intent）"""
    content = message.content or ""
    uid = str(client.user.id)
    if f"<@{uid}>" in content or f"<@!{uid}>" in content:
        return True
    if message.mentions and any(getattr(u, "id", None) == client.user.id for u in message.mentions):
        return True
    return False


def _strip_mention(content: str, user_id: int) -> str:
    """剥离消息中的 @bot 前缀，返回剩余命令文本"""
    text = content.replace(f"<@{user_id}>", "").replace(f"<@!{user_id}>", "")
    return text.strip()


# ========== 服务器 onboarding ==========

def _pick_default_channel(guild):
    """选择默认接收频道

    规则：DISCORD_GUILD_ID 限定单服务器；system_channel 优先，
    否则第一个 bot 可发送消息的文本频道。
    """
    if _settings and _settings.DISCORD_GUILD_ID:
        if str(guild.id) != str(_settings.DISCORD_GUILD_ID).strip():
            return None
    if guild.me is None:
        return None

    channels = list(guild.text_channels)
    if guild.system_channel in channels:
        channels.remove(guild.system_channel)
        channels.insert(0, guild.system_channel)

    for ch in channels:
        try:
            if ch.permissions_for(guild.me).send_messages:
                return ch
        except Exception:
            continue
    return None


def _onboard_guild(guild) -> None:
    """Bot 被加入服务器（或启动时发现已有服务器）：注册 + 自动订阅 + 欢迎"""
    channel = _pick_default_channel(guild)
    if channel is None:
        logger.warning(f"Discord onboard: no sendable channel in guild {guild.id}")
        return

    channel_id = str(channel.id)
    _guild_default_channel[guild.id] = channel.id

    try:
        from app.chat.lifecycle import register_chat, is_new_chat
        from app.subscription.handler import subscribe, ALL_VENDORS

        is_new = is_new_chat(channel_id, platform="discord")
        register_chat(channel_id, chat_type="group", platform="discord")
        if is_new:
            for vendor in ALL_VENDORS:
                subscribe(channel_id, vendor, platform="discord")
            logger.info(f"Discord guild onboarded: {channel_id}, auto-subscribed all vendors")

        # 发送欢迎消息
        try:
            from app.platforms.registry import get_platform_adapter
            from app.platforms.discord.commands import build_welcome_message

            adapter = get_platform_adapter("discord", _settings)
            if adapter:
                adapter.send_message(channel_id, build_welcome_message())
                logger.info(f"Discord guild welcome sent: {channel_id}")
        except Exception as e:
            logger.error(f"Failed to send Discord guild welcome: {e}")

    except Exception as e:
        logger.error(f"Discord guild onboarding failed: {e}", exc_info=True)


def _on_guild_removed(guild) -> None:
    """Bot 被移出服务器：deactivate 已注册的默认频道"""
    channel_id = _guild_default_channel.pop(guild.id, None)
    if channel_id is None:
        return
    try:
        from app.chat.lifecycle import deactivate_chat
        deactivate_chat(str(channel_id), platform="discord")
        logger.info(f"Discord guild removed, deactivated channel: {channel_id}")
    except Exception as e:
        logger.error(f"Discord guild removal deactivation failed: {e}")


# ========== 网关客户端构建 ==========

def _build_client() -> "discord.Client":
    """构建并配置 discord.py Client（事件处理器为闭包，捕获 client 引用）"""
    intents = discord.Intents.default()
    intents.message_content = True  # 需在 Discord Developer Portal 开启 Message Content Intent
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info(f"Discord gateway ready: {client.user} (id={client.user.id})")
        # 重启后对已存在的服务器做 onboard（幂等，已注册则跳过）
        for guild in client.guilds:
            query_submit(_onboard_guild, guild)

    @client.event
    async def on_message(message):
        if message.author.bot:
            return
        if _on_message is None:
            return
        if not _is_bot_mentioned(message, client):
            return
        if _dedup(str(message.id)):
            return

        text = _strip_mention(message.content or "", client.user.id)
        if not text:
            return

        sender_id = str(message.author.id)
        channel_id = str(message.channel.id)
        incoming = IncomingMessage(
            platform="discord",
            chat_id=channel_id,
            sender_id=sender_id,
            text=text,
            raw_payload={
                "message_id": str(message.id),
                "is_dm": message.guild is None,  # 私聊 DM 走 user 类型注册
            },
        )
        _submit_query(_on_message, incoming, user_id=sender_id, chat_id=channel_id)

    @client.event
    async def on_interaction(interaction):
        if _on_callback is None:
            return
        if interaction.type != discord.InteractionType.component:
            return

        data = interaction.data
        custom_id = getattr(data, "custom_id", None) if data is not None else None
        if not custom_id:
            return

        chat_id = str(interaction.channel_id) if interaction.channel_id else ""
        sender_id = str(interaction.user.id) if interaction.user else ""
        if not chat_id or not sender_id:
            return

        cb = CallbackData.from_json(custom_id)

        # 先 ack（defer），防止 3 秒交互超时；具体处理在 worker 线程完成
        try:
            await interaction.response.defer()
        except Exception:
            pass

        _submit_query(_on_callback, cb, chat_id, sender_id, user_id=sender_id, chat_id=chat_id)

    @client.event
    async def on_guild_join(guild):
        query_submit(_onboard_guild, guild)

    @client.event
    async def on_guild_remove(guild):
        query_submit(_on_guild_removed, guild)

    return client


# ========== 生命周期 ==========

def _run_gateway() -> None:
    """在 daemon 线程中运行网关客户端（阻塞）"""
    global _client
    try:
        client = _build_client()
        _client = client
        logger.info("Discord gateway connecting...")
        # discord.py 内置断线重连/恢复机制，run() 在内部自建 event loop
        client.run(_settings.DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.error(f"Discord gateway crashed: {e}", exc_info=True)


def start() -> Optional[threading.Thread]:
    """启动 Discord 网关后台线程（幂等）

    Returns:
        已启动的 daemon 线程，或 None（依赖/配置缺失）
    """
    global _thread
    if _thread and _thread.is_alive():
        return _thread
    if not DISCORD_AVAILABLE:
        logger.error("Cannot start Discord gateway: discord.py not installed")
        return None
    if _settings is None or not _settings.DISCORD_BOT_TOKEN:
        logger.error("Cannot start Discord gateway: DISCORD_BOT_TOKEN not configured")
        return None

    _thread = threading.Thread(
        target=_run_gateway,
        daemon=True,
        name="discord-gateway",
    )
    _thread.start()
    logger.info(f"Discord gateway thread started (daemon): {_thread.name}")
    return _thread


def stop() -> None:
    """优雅关闭网关（在网关 loop 上调度 close）"""
    global _client
    if _client is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(_client.close(), _client.loop).result(timeout=5)
    except Exception as e:
        logger.warning(f"Discord gateway stop failed: {e}")
    _client = None
    logger.info("Discord gateway stopped")


# ========== 状态访问（供 adapter / health 使用） ==========

def get_client():
    """返回运行中的 discord.Client（未启动返回 None）"""
    return _client


def is_running() -> bool:
    """检查网关线程是否存活"""
    return _thread is not None and _thread.is_alive()
