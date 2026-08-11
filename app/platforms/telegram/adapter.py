"""Telegram 平台适配器

TelegramAdapter 基于 python-telegram-bot 库实现 PlatformAdapter 接口。
消息通过 Telegram Bot API 发送，事件通过 Webhook 接收。
"""

import logging
import asyncio
from typing import Optional

from app.core.config import Settings
from app.platforms.adapter import PlatformAdapter
from app.platforms.message_model import RichMessage, ConversationInfo
from app.platforms.telegram.renderer import render_message_chunks

logger = logging.getLogger(__name__)

# 尝试导入 python-telegram-bot（可选依赖）
try:
    from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
    from telegram.constants import ParseMode
    from telegram.error import TelegramError
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Bot = None  # type: ignore
    InlineKeyboardMarkup = None  # type: ignore
    InlineKeyboardButton = None  # type: ignore
    ParseMode = None  # type: ignore
    TelegramError = Exception  # type: ignore
    logger.warning("python-telegram-bot not installed. Telegram platform will be unavailable.")


class TelegramAdapter(PlatformAdapter):
    """Telegram 平台适配器

    使用 python-telegram-bot 的 Bot 实例进行 API 调用。
    事件接收通过 Webhook（FastAPI endpoint）而非长轮询。
    """

    def __init__(self, settings: Settings):
        """初始化 Telegram 适配器

        Args:
            settings: 应用配置（需要 TELEGRAM_BOT_TOKEN）
        """
        self._settings = settings
        self._bot: Optional[Bot] = None

        if not TELEGRAM_AVAILABLE:
            logger.error("Cannot initialize TelegramAdapter: python-telegram-bot not installed")
            return

        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN is empty, Telegram adapter will not function")
            return

        self._bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        logger.info("TelegramAdapter initialized")

    # ========== 平台元信息 ==========

    def get_platform_name(self) -> str:
        return "telegram"

    def get_platform_label(self) -> str:
        return "Telegram"

    # ========== 消息发送 ==========

    def send_message(self, conversation_id: str, message: RichMessage) -> dict:
        """发送富文本消息到 Telegram 对话

        RichMessage → HTML 文本 + InlineKeyboardMarkup → Telegram API。
        超长消息自动分段。

        Args:
            conversation_id: Telegram chat_id（整数或负数格式的字符串）
            message: 平台无关的 RichMessage

        Returns:
            {"success": True, "message_id": str}

        Raises:
            RuntimeError: Telegram API 调用失败
        """
        if self._bot is None:
            raise RuntimeError("Telegram adapter not properly initialized")

        chunks = render_message_chunks(message)
        last_message_id = ""

        for i, (text, keyboard) in enumerate(chunks):
            reply_markup = None
            if keyboard:
                inline_keyboard = []
                for row in keyboard:
                    btn_row = []
                    for btn_dict in row:
                        if "url" in btn_dict:
                            btn_row.append(InlineKeyboardButton(
                                text=btn_dict["text"],
                                url=btn_dict["url"],
                            ))
                        elif "callback_data" in btn_dict:
                            btn_row.append(InlineKeyboardButton(
                                text=btn_dict["text"],
                                callback_data=btn_dict["callback_data"],
                            ))
                    inline_keyboard.append(btn_row)
                reply_markup = InlineKeyboardMarkup(inline_keyboard)

            try:
                sent = self._bot.send_message(
                    chat_id=conversation_id,
                    text=text,
                    parse_mode=ParseMode.HTML if ParseMode else None,
                    reply_markup=reply_markup,
                    disable_web_page_preview=False,
                )
                last_message_id = str(sent.message_id)
                logger.debug(f"Telegram message {last_message_id} sent to {conversation_id}")
            except Exception as e:
                logger.error(f"Telegram send_message failed to {conversation_id}: {e}")
                num_chunks = len(chunks)
                raise RuntimeError(
                    f"Telegram send_message failed (chunk {i+1}/{num_chunks}): {e}"
                ) from e

        return {"success": True, "message_id": last_message_id}

    # ========== 对话信息 ==========

    def get_conversation_info(self, conversation_id: str) -> ConversationInfo | None:
        """查询 Telegram 对话信息

        通过 get_chat() API 获取对话名称、类型等信息。

        Args:
            conversation_id: Telegram chat_id

        Returns:
            ConversationInfo，失败返回 None
        """
        if self._bot is None:
            return None

        try:
            chat = self._bot.get_chat(chat_id=conversation_id)
        except Exception as e:
            logger.error(f"Telegram get_chat failed for {conversation_id}: {e}")
            return None

        is_group = chat.type in ("group", "supergroup")

        return ConversationInfo(
            id=str(chat.id),
            name=chat.title or chat.username or chat.first_name or str(chat.id),
            owner_id="",  # Telegram get_chat 不直接返回 owner，需另外查询
            is_group=is_group,
        )

    def is_admin(self, conversation_id: str, user_id: str) -> bool:
        """检查用户在群聊中是否为管理员

        Args:
            conversation_id: Telegram chat_id
            user_id: Telegram user_id

        Returns:
            True 表示用户是管理员/创建者
        """
        if self._bot is None:
            return False

        try:
            member = self._bot.get_chat_member(
                chat_id=conversation_id,
                user_id=int(user_id),
            )
            return member.status in ("creator", "administrator")
        except Exception as e:
            logger.warning(
                f"Telegram get_chat_member failed for {conversation_id}/{user_id}: {e}"
            )
            return False

    # ========== 配置检查 ==========

    def is_configured(self) -> bool:
        """检查 Telegram 凭证是否已配置"""
        return self._settings.telegram_configured

    # ========== Bot 实例访问 ==========

    @property
    def bot(self) -> Optional[Bot]:
        """返回底层 python-telegram-bot Bot 实例

        供 webhook handler 使用以处理 incoming updates。
        """
        return self._bot
