"""Discord 渲染器：RichMessage → Discord Embed JSON + Button 组件

纯函数，不依赖 discord.py SDK（便于测试），与 feishu/renderer.py、telegram/renderer.py 同构。

渲染规则：
  - title → embed title（上限 256 字符）
  - body  → embed description（上限 4096 字符，超长截断）
  - footer→ embed footer（上限 2048 字符）
  - color_hint → embed color（info/success/warning → blurple/green/yellow）
  - buttons → ActionRow 按钮（每行最多 5 个）：
      action="url"      → 链接按钮（style=5）
      action="callback" → 按钮，callback_data JSON 编码进 custom_id（上限 100 字符）
"""

import json
import logging

from app.platforms.message_model import RichMessage, CallbackData

logger = logging.getLogger(__name__)

# color_hint → Discord Embed color（十六进制 int）
_COLOR_MAP = {
    "info": 0x5865F2,      # blurple（Discord 品牌色）
    "success": 0x57F287,    # 绿色
    "warning": 0xFEE75C,    # 黄色
}
_DEFAULT_COLOR = 0x5865F2

# Discord Embed / Button 长度上限
_MAX_TITLE = 256
_MAX_DESCRIPTION = 4096
_MAX_FOOTER = 2048
_MAX_BUTTON_LABEL = 80
_MAX_CUSTOM_ID = 100
_MAX_BUTTONS = 5

# ActionButton.style → Discord ButtonStyle（1=primary, 2=secondary, 4=danger；5=link 留给 url 按钮）
_BUTTON_STYLE_MAP = {
    "primary": 1,
    "default": 2,
    "danger": 4,
}


def _truncate(text: str, max_len: int) -> str:
    """按字符截断，超长加省略号"""
    if text is None:
        return ""
    text = str(text)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def render_embed(message: RichMessage) -> dict:
    """RichMessage → Discord Embed 字典（兼容 discord.Embed.from_dict）

    Returns:
        {"type": "rich", "title": ..., "description": ..., "color": int, "footer": {...}}
    """
    embed: dict = {
        "type": "rich",
        "color": _COLOR_MAP.get(message.color_hint, _DEFAULT_COLOR),
    }

    if message.title:
        embed["title"] = _truncate(message.title, _MAX_TITLE)

    body = message.body or ""
    if body:
        embed["description"] = _truncate(body, _MAX_DESCRIPTION)

    if message.footer:
        embed["footer"] = {"text": _truncate(message.footer, _MAX_FOOTER)}

    return embed


def _encode_callback(value: str) -> str:
    """将 callback_data JSON 编码为 Discord custom_id（≤100 字符）

    btn.value 已经是 {"action": "...", ...} 的 JSON 字符串，
    这里压缩成紧凑 JSON 后作为 custom_id，交互回调时用 CallbackData.from_json 还原。
    """
    if not value:
        return ""
    try:
        cb = CallbackData.from_json(value)
        compact = json.dumps(
            {"action": cb.action, **cb.params},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception:
        compact = value
    return compact[:_MAX_CUSTOM_ID]


def render_components(message: RichMessage) -> list[dict]:
    """RichMessage → Discord Button 组件列表

    Returns:
        [{"type": "button", "style": int, "label": str, "url": str | None, "custom_id": str | None}, ...]
        每行最多 5 个，超出丢弃。
    """
    specs = []
    for btn in message.buttons[: _MAX_BUTTONS]:
        label = _truncate(btn.label, _MAX_BUTTON_LABEL)
        if not label:
            continue

        if btn.action == "url" and btn.value:
            specs.append({
                "type": "button",
                "style": 5,  # link
                "label": label,
                "url": btn.value,
            })
        else:
            specs.append({
                "type": "button",
                "style": _BUTTON_STYLE_MAP.get(btn.style, 2),
                "label": label,
                "custom_id": _encode_callback(btn.value),
            })
    return specs
