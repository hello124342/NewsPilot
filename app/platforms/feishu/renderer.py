"""飞书 Card 渲染器

将平台无关的 RichMessage 转换为飞书 Interactive Card JSON。
飞书卡片结构：
  - config.wide_screen_mode = True
  - header: {template: color, title: {tag: plain_text, content: title}}
  - elements: [div(lark_md)+hr+action(buttons)+note(footer)]
"""

from app.platforms.message_model import RichMessage, ActionButton

# 颜色映射：color_hint → 飞书 header template
_COLOR_MAP = {
    "info": "blue",
    "success": "green",
    "warning": "red",
}

# 按钮样式映射：style → 飞书 button type
_BUTTON_STYLE_MAP = {
    "primary": "primary",
    "default": "default",
    "danger": "default",  # 飞书无 danger 样式，用 default
}


def render_card(message: RichMessage) -> dict:
    """将 RichMessage 渲染为飞书 Interactive Card JSON

    Args:
        message: 平台无关的富文本消息

    Returns:
        飞书 Interactive Card JSON (dict)
    """
    elements: list[dict] = []

    # Body 正文 → lark_md div
    if message.body:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": message.body},
        })

    # 按钮组 → action
    if message.buttons:
        if elements:
            elements.append({"tag": "hr"})
        actions = []
        for btn in message.buttons:
            feishu_btn: dict = {
                "tag": "button",
                "text": {"tag": "plain_text", "content": btn.label},
                "type": _BUTTON_STYLE_MAP.get(btn.style, "default"),
            }
            if btn.action == "url" and btn.value:
                feishu_btn["url"] = btn.value
            elif btn.action == "callback":
                import json
                try:
                    params = json.loads(btn.value) if btn.value else {}
                except (json.JSONDecodeError, TypeError):
                    params = {"value": btn.value}
                feishu_btn["value"] = params
            actions.append(feishu_btn)
        elements.append({"tag": "action", "actions": actions})

    # Footer → note
    if message.footer:
        if elements:
            elements.append({"tag": "hr"})
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": message.footer}],
        })

    # Header
    header_template = _COLOR_MAP.get(message.color_hint or "", "blue")
    card: dict = {
        "config": {"wide_screen_mode": True},
        "elements": elements,
    }

    if message.title:
        card["header"] = {
            "template": header_template,
            "title": {"tag": "plain_text", "content": message.title},
        }

    return card
