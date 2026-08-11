"""平台无关的消息与对话模型

定义了跨所有 IM 平台通用的数据结构，各平台适配器负责：
  - 将平台原生事件 → 本层模型（输入）
  - 将本层模型 → 平台原生消息格式（输出）
"""

from dataclasses import dataclass, field


# ========== 消息模型 ==========


@dataclass
class ActionButton:
    """平台无关的交互按钮

    同时支持两种动作：
      action="url"      → 点击打开外部链接（value 为 URL）
      action="callback" → 点击触发回调（value 为 callback_data JSON 字符串）
    """
    label: str                     # 按钮显示文字
    action: str = "callback"       # "url" | "callback"
    value: str = ""                # URL 或 callback_data
    style: str = "default"         # "primary" | "default" | "danger"


@dataclass
class RichMessage:
    """平台无关的富文本消息

    各平台渲染规则：
      飞书 → Interactive Card JSON（header + lark_md elements + action buttons）
      Telegram → Markdown 文本 + InlineKeyboardMarkup
      Slack → Block Kit JSON
      Discord → Embed JSON

    字段说明：
      title: 卡片/消息标题（可选，null 时不显示标题栏）
      body: 正文内容（Markdown 格式，Telegram/Slack 原生支持，飞书转为 lark_md）
      buttons: 交互按钮列表（水平排列）
      color_hint: 颜色提示 "info" | "success" | "warning" — 各平台自行映射
      footer: 底部提示文字（可选）
    """
    body: str = ""                 # Markdown 正文
    title: str | None = None       # 可选标题
    buttons: list[ActionButton] = field(default_factory=list)
    color_hint: str | None = None  # info / success / warning
    footer: str | None = None      # 底部提示


# ========== 输入模型 ==========


@dataclass
class CallbackData:
    """按钮回调数据（平台无关）

    飞书：button.value 中的 JSON → CallbackData
    Telegram：callback_query.data → CallbackData
    统一格式: {"action": "subscribe", "vendor": "OpenAI", ...}
    """
    action: str = ""
    params: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: str | dict | None) -> "CallbackData":
        """从 JSON 字符串或 dict 解析回调数据"""
        import json
        if data is None:
            return cls()
        if isinstance(data, dict):
            return cls(action=data.get("action", ""), params={k: v for k, v in data.items() if k != "action"})
        if isinstance(data, str):
            try:
                d = json.loads(data)
                return cls.from_json(d)
            except (json.JSONDecodeError, TypeError):
                return cls(action=data)
        return cls()


@dataclass
class ConversationInfo:
    """平台无关的对话/会话信息"""
    id: str = ""                   # 平台原生 conversation/chat ID
    name: str = ""                 # 对话名称（群名 或 用户名）
    owner_id: str = ""             # 群主/管理员 ID（私聊为空）
    is_group: bool = False         # 是否为群聊


@dataclass
class IncomingMessage:
    """平台无关的入站消息事件

    由各平台的 event handler 将原生事件转换为本结构，再分派给业务逻辑。
    """
    platform: str = ""             # 平台标识：feishu / telegram
    chat_id: str = ""              # 来源对话的 ID
    sender_id: str = ""            # 发送者 ID（平台原生格式）
    text: str = ""                 # 消息正文（纯文本，已去除 @mention 等）
    is_bot_added: bool = False     # Bot 被添加到群聊
    is_bot_removed: bool = False   # Bot 被移出群聊
    raw_payload: dict = field(default_factory=dict)  # 平台原生事件原始数据（用于调试）
