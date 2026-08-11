"""平台适配器抽象接口 (Platform Adapter ABC)

定义了跨 IM 平台的统一消息接口。每个平台（飞书、Telegram 等）需实现此接口。

设计参考：app/llm/provider.py 的 Factory 模式 — 通过 registry 注册各平台适配器。
"""

from abc import ABC, abstractmethod
from typing import Protocol

from app.platforms.message_model import (
    RichMessage,
    ConversationInfo,
    CallbackData,
    IncomingMessage,
)


class PlatformAdapter(ABC):
    """IM 平台适配器抽象基类

    每个具体平台实现（FeishuAdapter, TelegramAdapter）负责：
      1. 将 RichMessage 渲染为平台原生消息格式并发送
      2. 查询对话信息（群主、成员等）
      3. 管理平台特有的事件监听生命周期

    子类只需要实现 send_message 和 get_conversation_info；
    get_platform_name / get_platform_label 是配置方法。
    """

    # ========== 平台元信息 ==========

    @abstractmethod
    def get_platform_name(self) -> str:
        """返回平台标识符，如 'feishu', 'telegram'

        用作数据库中的 platform 字段值，必须全小写无空格。
        """
        ...

    @abstractmethod
    def get_platform_label(self) -> str:
        """返回平台显示名称，如 '飞书', 'Telegram'

        用于日志和运维界面展示。
        """
        ...

    # ========== 消息发送 ==========

    @abstractmethod
    def send_message(self, conversation_id: str, message: RichMessage) -> dict:
        """向指定对话发送富文本消息

        Args:
            conversation_id: 平台原生的对话/频道/用户 ID
            message: 平台无关的 RichMessage

        Returns:
            发送结果 dict，至少包含 {"success": True/False, "message_id": str}

        Raises:
            RuntimeError: 平台 API 调用失败时
        """
        ...

    # ========== 对话信息 ==========

    @abstractmethod
    def get_conversation_info(self, conversation_id: str) -> ConversationInfo | None:
        """查询对话信息（群主、名称、是否为群聊等）

        Args:
            conversation_id: 平台原生的对话 ID

        Returns:
            ConversationInfo，失败返回 None
        """
        ...

    # ========== 生命周期（可选覆盖） ==========

    async def start(self) -> None:
        """启动平台事件监听（WebSocket / Webhook / Polling）

        默认不操作。需要事件监听的平台（飞书 WebSocket、Telegram Webhook）
        覆盖此方法。
        """
        pass

    async def stop(self) -> None:
        """停止平台事件监听

        默认不操作。需要清理资源的平台覆盖此方法。
        """
        pass

    def is_configured(self) -> bool:
        """检查平台凭证是否已配置

        默认返回 True。平台适配器应覆盖此方法来检查 API Key/Token 等。
        """
        return True


# ========== 回调处理器协议 ==========


class MessageCallback(Protocol):
    """消息处理回调协议（用于事件监听器向业务逻辑分派消息）

    平台适配器的事件监听器在收到消息后调用此回调，
    将平台事件翻译为 IncomingMessage 后传递给业务逻辑。
    """

    def __call__(self, message: IncomingMessage) -> None: ...


class CallbackActionHandler(Protocol):
    """按钮回调处理协议（用于事件监听器向业务逻辑分派按钮点击）

    平台适配器的事件监听器在收到按钮点击后调用此回调。
    """

    def __call__(self, callback: CallbackData, chat_id: str, sender_id: str) -> None: ...
