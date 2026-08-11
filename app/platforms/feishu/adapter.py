"""飞书平台适配器

FeishuAdapter 包装现有 FeishuClient + renderer，实现 PlatformAdapter 接口。
保持与原有 app/feishu/ 代码的兼容性 — 现有代码继续直接使用 FeishuClient，
新代码通过 PlatformAdapter 接口使用。渐进式解耦。
"""

import logging

from app.core.config import Settings
from app.platforms.adapter import PlatformAdapter
from app.platforms.message_model import RichMessage, ConversationInfo
from app.platforms.feishu.renderer import render_card

logger = logging.getLogger(__name__)


class FeishuAdapter(PlatformAdapter):
    """飞书平台适配器

    内部委托给现有的 FeishuClient（lark-oapi SDK）和 renderer。
    此适配器是 Phase 2 的产物：为现有的飞书功能提供一个 PlatformAdapter 脸面，
    后续 Phase 5 会将 graph 节点逐步迁移到使用此适配器。
    """

    def __init__(self, settings: Settings):
        """初始化飞书适配器

        Args:
            settings: 应用配置（需要 FEISHU_APP_ID 和 FEISHU_APP_SECRET）
        """
        self._settings = settings
        # 延迟导入避免循环依赖（FeishuClient 在 app.feishu.client 中）
        from app.feishu.client import FeishuClient
        self._client = FeishuClient(settings)

    # ========== 平台元信息 ==========

    def get_platform_name(self) -> str:
        return "feishu"

    def get_platform_label(self) -> str:
        return "飞书"

    # ========== 消息发送 ==========

    def send_message(self, conversation_id: str, message: RichMessage) -> dict:
        """发送富文本消息到飞书对话

        将 RichMessage 渲染为飞书 Interactive Card JSON，
        然后通过 FeishuClient.send_card() 发送。

        Args:
            conversation_id: 飞书 chat_id（oc_xxx 或 ou_xxx）
            message: 平台无关的 RichMessage

        Returns:
            {"success": True, "message_id": ...}

        Raises:
            RuntimeError: 飞书 API 调用失败
        """
        card_json = render_card(message)
        result = self._client.send_card(conversation_id, card_json)
        return {
            "success": True,
            "message_id": result.get("msg", ""),
            "raw": result,
        }

    # ========== 对话信息 ==========

    def get_conversation_info(self, conversation_id: str) -> ConversationInfo | None:
        """查询飞书对话信息

        委托给 FeishuClient.get_chat_info()。

        Args:
            conversation_id: 飞书 chat_id

        Returns:
            ConversationInfo，失败返回 None
        """
        info = self._client.get_chat_info(conversation_id)
        if info is None:
            return None

        return ConversationInfo(
            id=info.get("chat_id", conversation_id),
            name=info.get("name", ""),
            owner_id=info.get("owner_id", ""),
            is_group=True,  # 飞书 get_chat_info 通常用于群聊
        )

    # ========== 配置检查 ==========

    def is_configured(self) -> bool:
        """检查飞书凭证是否已配置"""
        return self._settings.feishu_configured

    # ========== 访问底层 client（过渡期使用） ==========

    @property
    def native_client(self):
        """返回底层 FeishuClient 实例

        用于过渡期：当 graph 节点仍需要直接调用 FeishuClient 方法时，
        可以通过此属性访问。Phase 5 完成后移除此属性。
        """
        return self._client
