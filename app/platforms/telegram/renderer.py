"""Telegram 消息渲染器

将平台无关的 RichMessage 转换为 Telegram 原生消息格式：
  - 文本：HTML 格式（支持 <b> <i> <a> <code> <pre>）
  - 按钮：InlineKeyboardMarkup

Telegram 消息限制：
  - 文本 ≤ 4096 字符（超长自动分段）
  - 按钮 ≤ 100 个 / 消息
  - callback_data ≤ 64 字节
"""

import re
import logging
from app.platforms.message_model import RichMessage, ActionButton

logger = logging.getLogger(__name__)

# Telegram 单条消息文本上限
_MAX_TEXT_LENGTH = 4000  # 留一些 buffer，实际限制 4096
# callback_data 上限
_MAX_CALLBACK_DATA = 64


def _markdown_to_html(text: str) -> str:
    """将基础 Markdown 转为 Telegram 兼容的 HTML

    支持的转换：
      **text**  → <b>text</b>
      *text*    → <i>text</i>（当不在单词中间时）
      [text](url) → <a href="url">text</a>
      换行保留

    注意：不处理嵌套格式。
    """
    # 先处理粗体 **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # 处理链接 [text](url)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
    # 处理斜体 *text*（避免匹配 ** 残余或列表标记）
    # 只在行内匹配，避免匹配列表中的 * 号
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    return text


def render_message(message: RichMessage) -> str:
    """将 RichMessage 渲染为 Telegram HTML 文本

    Args:
        message: 平台无关的富文本消息

    Returns:
        HTML 格式的文本字符串（不含按钮标记）
    """
    parts: list[str] = []

    # 标题 → 粗体
    if message.title:
        parts.append(f"<b>{_escape_html(message.title)}</b>")
        parts.append("")  # 空行

    # Body → Markdown 转 HTML
    if message.body:
        parts.append(_markdown_to_html(message.body))

    # Footer → 斜体
    if message.footer:
        parts.append("")
        parts.append(f"<i>{_escape_html(message.footer)}</i>")

    return "\n".join(parts)


def render_keyboard(message: RichMessage) -> list[list[dict]] | None:
    """从 RichMessage 生成 Telegram InlineKeyboardMarkup

    Args:
        message: 富文本消息

    Returns:
        InlineKeyboardMarkup 的二维数组，无按钮时返回 None
    """
    if not message.buttons:
        return None

    # 每行最多 2 个按钮（Telegram 推荐布局）
    keyboard = []
    row = []
    for btn in message.buttons:
        btn_dict = {"text": btn.label[:40]}  # Telegram 按钮文字限制
        if btn.action == "url" and btn.value:
            btn_dict["url"] = btn.value
        elif btn.action == "callback":
            # callback_data 限制 64 字节
            data = btn.value.encode("utf-8")[:_MAX_CALLBACK_DATA].decode("utf-8", errors="ignore")
            btn_dict["callback_data"] = data
        else:
            # 无效按钮跳过
            continue
        row.append(btn_dict)
        if len(row) >= 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    return keyboard


def render_message_chunks(message: RichMessage) -> list[tuple[str, list[list[dict]] | None]]:
    """将长消息分段，每段不超过 Telegram 4096 字符限制

    第一段包含标题+正文+按钮，后续段只含正文。

    Args:
        message: 富文本消息

    Returns:
        [(text_chunk, keyboard_or_none), ...] 列表
    """
    full_text = render_message(message)
    keyboard = render_keyboard(message)

    if len(full_text) <= _MAX_TEXT_LENGTH:
        return [(full_text, keyboard)]

    # 超长分段
    chunks: list[tuple[str, list[list[dict]] | None]] = []
    lines = full_text.split("\n")
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > _MAX_TEXT_LENGTH:
            if current:
                chunks.append((current, None))
                current = ""
            # 如果单行就超过限制，硬截断
            if len(line) > _MAX_TEXT_LENGTH:
                line = line[:_MAX_TEXT_LENGTH - 50] + "...\n<i>(内容过长已截断)</i>"
            current = line
        else:
            if current:
                current += "\n" + line
            else:
                current = line

    if current:
        chunks.append((current, None))

    # 按钮只放在最后一段
    if chunks and keyboard:
        last_text, _ = chunks[-1]
        chunks[-1] = (last_text, keyboard)

    return chunks


def _escape_html(text: str) -> str:
    """转义 HTML 保留字符"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
