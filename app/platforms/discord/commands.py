"""Discord 命令模板

频道文本命令（@Bot 触发），命令识别复用 subscription/handler.py 的正则，
此处仅提供 Discord 风格的欢迎/帮助消息与厂商别名解析。

命令示例（@ 前缀省略）：
  @Bot 订阅 OpenAI / @Bot 订阅所有 / @Bot 退订 DeepSeek
  @Bot 订阅列表 / @Bot 设置 / @Bot 设置推送时间 18:00 / @Bot 设置推送频率 工作日
"""

import logging
from typing import Optional

from app.platforms.message_model import RichMessage, ActionButton
from app.subscription.handler import ALL_VENDORS, VENDOR_ALIASES

logger = logging.getLogger(__name__)


def resolve_vendor(text: str) -> Optional[str]:
    """将用户输入的厂商名（可能为别名）解析为标准名称

    直接使用 subscription/handler.py 的权威别名表，避免第四份拷贝。
    """
    if not text:
        return None
    text_lower = text.strip().lower()
    if text_lower in VENDOR_ALIASES:
        return VENDOR_ALIASES[text_lower]
    for v in ALL_VENDORS:
        if v.lower() == text_lower:
            return v
    return None


def build_welcome_message() -> RichMessage:
    """构建欢迎消息（Discord 风格）"""
    vendors_list = "、".join(ALL_VENDORS)
    body = (
        "👋 **欢迎使用 AI 新闻 Bot！**\n\n"
        "我每天 **9:00** 自动推送 AI 厂商最新动态到本频道。\n\n"
        f"📌 **已默认订阅：** {vendors_list}\n\n"
        "**群管理命令（@我 + 命令）：**\n"
        "  `@Bot 订阅 OpenAI` — 订阅厂商\n"
        "  `@Bot 订阅所有` — 订阅全部\n"
        "  `@Bot 退订 DeepSeek` — 退订厂商\n"
        "  `@Bot 订阅列表` — 查看订阅\n"
        "  `@Bot 设置` — 查看推送设置\n"
        "  `@Bot 设置推送时间 18:00` — 设置推送时间\n"
        "  `@Bot 设置推送频率 工作日` — 设置推送频率\n\n"
        "**所有成员：**\n"
        "  直接 @我 提问，如「GPT-5 什么时候发布？」"
    )
    return RichMessage(
        title="🤖 AI 新闻 Bot",
        body=body,
        color_hint="success",
        buttons=[
            ActionButton(label="📋 查看订阅", action="callback", value='{"action":"list"}'),
            ActionButton(label="⚙️ 推送设置", action="callback", value='{"action":"settings"}'),
        ],
    )


def build_help_message() -> RichMessage:
    """构建帮助消息（Discord 风格）"""
    body = (
        "🤖 **AI 新闻 Bot 使用帮助**\n\n"
        "**订阅管理（@我 + 命令）：**\n"
        "  `@Bot 订阅 OpenAI` — 订阅某个厂商\n"
        "  `@Bot 订阅所有` — 订阅全部\n"
        "  `@Bot 退订 DeepSeek` — 退订某个厂商\n"
        "  `@Bot 订阅列表` — 查看当前订阅\n\n"
        "**推送设置：**\n"
        "  `@Bot 设置` — 查看当前设置\n"
        "  `@Bot 设置推送时间 18:00` — 设为下午6点推送\n"
        "  `@Bot 设置推送频率 工作日` — 仅工作日推送\n\n"
        "**智能问答：**\n"
        "  直接 @我 提问，如「GPT-5 什么时候发布？」\n"
        "  或「OpenAI 最近有什么新闻？」\n\n"
        "**可用频率：** daily（每天）/ weekdays（工作日）/ weekly_monday（每周一）\n"
        "**可用时间：** 09:00 / 12:00 / 18:00"
    )
    return RichMessage(
        title="📖 帮助",
        body=body,
        color_hint="info",
    )
