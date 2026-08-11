"""Telegram Bot 命令处理器

支持的命令：
  /start          — 欢迎消息 + 功能引导
  /subscribe      — 订阅厂商（/subscribe OpenAI）
  /unsubscribe    — 退订厂商（/unsubscribe OpenAI）
  /list           — 查看当前订阅列表
  /settings       — 查看推送设置
  /settime        — 设置推送时间（/settime 09:00）
  /setfrequency   — 设置推送频率（/setfrequency weekdays）
  /help           — 帮助信息

命令注册：通过 python-telegram-bot 的 Application.add_handler() 注册。
"""

import logging
from typing import Optional

from app.platforms.message_model import RichMessage, ActionButton

logger = logging.getLogger(__name__)

# 厂商别名字典（与 subscription/handler.py 中的定义同步）
VENDOR_ALIASES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "claude": "Anthropic",
    "google": "Google DeepMind",
    "deepmind": "Google DeepMind",
    "谷歌": "Google DeepMind",
    "deepseek": "DeepSeek",
    "kimi": "Kimi (Moonshot)",
    "moonshot": "Kimi (Moonshot)",
    "z.ai": "Z.ai / 智谱",
    "智谱": "Z.ai / 智谱",
    "zhipu": "Z.ai / 智谱",
}

ALL_VENDORS = [
    "OpenAI", "Anthropic", "Google DeepMind",
    "DeepSeek", "Kimi (Moonshot)", "Z.ai / 智谱",
]

FREQUENCY_ALIASES = {
    "daily": "每天",
    "weekdays": "仅工作日",
    "weekly_monday": "每周一汇总",
    "每天": "daily",
    "工作日": "weekdays",
    "每周": "weekly_monday",
}

TIME_ALIASES = {
    "09:00": "早上9点",
    "12:00": "中午12点",
    "18:00": "下午6点",
    "9:00": "09:00",
    "12:00": "12:00",
    "18:00": "18:00",
}


def resolve_vendor(text: str) -> Optional[str]:
    """将用户输入的厂商名（可能为别名）解析为标准名称"""
    if not text:
        return None
    text_lower = text.strip().lower()
    if text_lower in VENDOR_ALIASES:
        return VENDOR_ALIASES[text_lower]
    # 也检查是否有匹配的标准名
    for v in ALL_VENDORS:
        if v.lower() == text_lower:
            return v
    return None


def build_welcome_message() -> RichMessage:
    """构建欢迎消息"""
    vendors_list = "、".join(ALL_VENDORS)
    body = (
        "👋 **欢迎使用 AI 新闻 Bot！**\n\n"
        "我每天 **9:00** 推送 AI 厂商的最新动态。\n\n"
        f"📌 **可订阅的厂商：** {vendors_list}\n\n"
        "**命令列表：**\n"
        "  /subscribe OpenAI — 订阅厂商\n"
        "  /subscribe all — 订阅全部\n"
        "  /unsubscribe OpenAI — 退订厂商\n"
        "  /list — 查看订阅列表\n"
        "  /settings — 查看推送设置\n"
        "  /settime 09:00 — 设置推送时间\n"
        "  /setfrequency weekdays — 设置推送频率\n\n"
        "💬 也可以直接问我：OpenAI 最近有什么新闻？"
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
    """构建帮助消息"""
    body = (
        "🤖 **AI 新闻 Bot 使用帮助**\n\n"
        "**订阅管理：**\n"
        "  /subscribe OpenAI — 订阅某个厂商\n"
        "  /subscribe all — 订阅全部\n"
        "  /unsubscribe DeepSeek — 退订某个厂商\n"
        "  /list — 查看当前订阅\n\n"
        "**推送设置：**\n"
        "  /settings — 查看当前设置\n"
        "  /settime 18:00 — 设为下午6点推送\n"
        "  /setfrequency weekdays — 仅工作日推送\n\n"
        "**智能问答：**\n"
        "  直接发送问题，如「GPT-5 什么时候发布？」\n"
        "  或「OpenAI 最近有什么新闻？」\n\n"
        "**可用频率：** daily（每天）/ weekdays（工作日）/ weekly_monday（每周一）\n"
        "**可用时间：** 09:00 / 12:00 / 18:00"
    )
    return RichMessage(
        title="📖 帮助",
        body=body,
        color_hint="info",
    )


def build_unknown_command_message() -> RichMessage:
    """构建未知命令提示"""
    return RichMessage(
        body="❓ 未知命令。发送 /help 查看可用命令。",
        color_hint="warning",
    )
