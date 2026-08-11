"""订阅管理模块（Facade + 领域逻辑）

领域逻辑（命令检测、频率判断）在此模块中直接实现。
数据访问（CRUD）委托给 SubscriptionRepository 接口实现。
"""
import logging
import re
from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from app.db.sql_repositories import get_subscription_repo

logger = logging.getLogger(__name__)

# ========== 模块级常量 ==========

ALL_VENDORS = [
    "OpenAI", "Anthropic", "Google DeepMind",
    "DeepSeek", "Kimi (Moonshot)", "Z.ai / 智谱",
]

VENDOR_ALIASES: dict[str, str] = {
    "openai": "OpenAI", "anthropic": "Anthropic", "claude": "Anthropic",
    "google": "Google DeepMind", "deepmind": "Google DeepMind",
    "google deepmind": "Google DeepMind", "deepseek": "DeepSeek",
    "deep seek": "DeepSeek", "kimi": "Kimi (Moonshot)",
    "moonshot": "Kimi (Moonshot)", "kimi (moonshot)": "Kimi (Moonshot)",
    "z.ai": "Z.ai / 智谱", "智谱": "Z.ai / 智谱",
    "z.ai / 智谱": "Z.ai / 智谱", "智谱ai": "Z.ai / 智谱",
}

PUSH_TIMES = {"09:00": "早上 9:00", "12:00": "中午 12:00", "18:00": "下午 6:00"}
FREQUENCIES = {"daily": "每天", "weekdays": "仅工作日", "weekly_monday": "每周一汇总"}

TIME_ALIASES: dict[str, str] = {
    "早上9点": "09:00", "早上9:00": "09:00", "9点": "09:00", "9:00": "09:00",
    "上午9点": "09:00", "上午9:00": "09:00",
    "中午12点": "12:00", "中午12:00": "12:00", "12点": "12:00", "12:00": "12:00",
    "下午6点": "18:00", "下午6:00": "18:00", "晚上6点": "18:00", "6点": "18:00",
    "18点": "18:00", "18:00": "18:00", "傍晚6点": "18:00",
}

FREQ_ALIASES: dict[str, str] = {
    "每天": "daily", "每日": "daily", "天天": "daily",
    "工作日": "weekdays", "仅工作日": "weekdays", "上班日": "weekdays",
    "每周": "weekly_monday", "每周一": "weekly_monday",
    "每周一汇总": "weekly_monday", "周一": "weekly_monday",
}

# 预编译正则
_SUBSCRIBE_PATTERN = re.compile(r"^(订阅|訂閱)\s*(.+)$")
_UNSUBSCRIBE_PATTERN = re.compile(r"^(取消订阅|取消訂閱|退订|退訂)\s*(.+)$")
_LIST_PATTERN = re.compile(r"^(我的订阅|我的訂閱|订阅列表|訂閱列表|订阅状态|訂閱狀態)$")
_SETTINGS_PATTERN = re.compile(r"^(设置|推送设置|偏好设置)$")
_SET_TIME_PATTERN = re.compile(r"^(设置推送时间|推送时间|设置时间)\s*(.+)$")
_SET_FREQ_PATTERN = re.compile(r"^(设置推送频率|推送频率|设置频率)\s*(.+)$")


# ========== 领域逻辑（纯函数，无 DB 依赖）==========

def _resolve_vendor(text: str) -> str | None:
    """将用户输入的厂商名解析为标准名称"""
    text = text.strip().lower()
    if not text:
        return None
    if text in VENDOR_ALIASES:
        return VENDOR_ALIASES[text]
    for std_name in ALL_VENDORS:
        if text in std_name.lower():
            return std_name
    return None


def detect_command(
    text: str,
) -> tuple[Literal["subscribe", "unsubscribe", "list", "settings", "set_time", "set_freq"], str | None] | None:
    """检测用户消息是否为订阅命令"""
    text = text.strip()

    if text in ("订阅所有", "訂閱所有"):
        return ("subscribe", "__ALL__")
    if text in ("取消订阅所有", "取消訂閱所有", "退订所有", "退訂所有"):
        return ("unsubscribe", "__ALL__")
    if _LIST_PATTERN.match(text):
        return ("list", None)

    m = _SUBSCRIBE_PATTERN.match(text)
    if m:
        vendor = _resolve_vendor(m.group(2))
        return ("subscribe", vendor) if vendor else None

    m = _UNSUBSCRIBE_PATTERN.match(text)
    if m:
        vendor = _resolve_vendor(m.group(2))
        return ("unsubscribe", vendor) if vendor else None

    result = _detect_settings_command(text)
    return result if result else None


def _detect_settings_command(text: str) -> tuple | None:
    text = text.strip()

    if _SETTINGS_PATTERN.match(text):
        return ("settings", None)

    m = _SET_TIME_PATTERN.match(text)
    if m:
        alias = m.group(2).strip().lower()
        time_val = TIME_ALIASES.get(alias)
        return ("set_time", time_val) if time_val else None

    m = _SET_FREQ_PATTERN.match(text)
    if m:
        alias = m.group(2).strip().lower()
        freq_val = FREQ_ALIASES.get(alias)
        return ("set_freq", freq_val) if freq_val else None

    return None


def is_today_in_frequency(frequency: str) -> bool:
    """判断今天是否在指定频率范围内"""
    today = date.today()
    weekday = today.weekday()
    if frequency == "daily":
        return True
    if frequency == "weekdays":
        return weekday < 5
    if frequency == "weekly_monday":
        return weekday == 0
    return True


# ========== Facade：数据访问委托给 Repository ==========

def subscribe(chat_id: str, vendor: str, db: Session | None = None,
              platform: str = "feishu") -> str:
    return get_subscription_repo().subscribe(chat_id, vendor, db, platform=platform)


def unsubscribe(chat_id: str, vendor: str, db: Session | None = None,
                platform: str = "feishu") -> str:
    return get_subscription_repo().unsubscribe(chat_id, vendor, db, platform=platform)


def list_subscriptions(chat_id: str, db: Session | None = None,
                       platform: str = "feishu") -> list[str]:
    return get_subscription_repo().list_active(chat_id, db, platform=platform)


def get_subscribers(vendor: str, platform: str = "feishu") -> list[str]:
    return get_subscription_repo().get_subscribers(vendor, platform=platform)


def has_any_subscription(chat_id: str, platform: str = "feishu") -> bool:
    return get_subscription_repo().has_any(chat_id, platform=platform)


def get_preference(chat_id: str, db: Session | None = None,
                   platform: str = "feishu") -> dict:
    return get_subscription_repo().get_preference(chat_id, db, platform=platform)


def set_push_time(chat_id: str, push_time: str, db: Session | None = None,
                  platform: str = "feishu") -> dict:
    return get_subscription_repo().set_push_time(chat_id, push_time, db, platform=platform)


def set_frequency(chat_id: str, frequency: str, db: Session | None = None,
                  platform: str = "feishu") -> dict:
    return get_subscription_repo().set_frequency(chat_id, frequency, db, platform=platform)
