"""飞书 Open API 客户端

基于 lark-oapi SDK 封装消息发送、群信息查询等飞书 API 调用。
SDK 自动管理 tenant_access_token（获取、缓存、续期），无需手动处理。

韧性模式（Circuit Breaker）：
- 可注入 CircuitBreaker 实例，为 send_card 提供熔断保护
- 未注入时直接调用（向后兼容）
"""
import json
import logging
import time as _time
from typing import Optional

import lark_oapi as lark
from app.core.config import Settings

logger = logging.getLogger(__name__)


def _track_feishu_api(method: str):
    """飞书 API 调用埋点 decorator，记录耗时和按错误码分类的错误"""
    import functools

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = _time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = _time.perf_counter() - start
                _emit_feishu_duration(method, elapsed)
                return result
            except Exception as e:
                elapsed = _time.perf_counter() - start
                _emit_feishu_duration(method, elapsed)
                _emit_feishu_error(method, type(e).__name__)
                raise
        return wrapper
    return decorator


def _emit_feishu_duration(method: str, elapsed: float) -> None:
    """发送 API 耗时到 Prometheus"""
    try:
        from app.core.metrics import feishu_api_duration_seconds
        feishu_api_duration_seconds.labels(method=method).observe(elapsed)
    except ImportError:
        pass


def _emit_feishu_error(method: str, code: str) -> None:
    """发送 API 错误到 Prometheus"""
    try:
        from app.core.metrics import feishu_api_errors_total
        feishu_api_errors_total.labels(method=method, code=code).inc()
    except ImportError:
        pass


class FeishuClient:
    """飞书 Open API 客户端（基于 lark-oapi SDK）"""

    def __init__(
        self,
        settings: Settings,
        circuit_breaker: Optional["CircuitBreaker"] = None,
    ):
        """初始化客户端

        Args:
            settings: 应用配置实例
            circuit_breaker: 可选的熔断器实例，用于 send_card 韧性保护
        """
        self.settings = settings
        self._circuit_breaker = circuit_breaker
        self._client = (
            lark.Client.builder()
            .app_id(settings.FEISHU_APP_ID)
            .app_secret(settings.FEISHU_APP_SECRET)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

    def get_tenant_access_token(self) -> str:
        """[已废弃] SDK 自动管理 token，此方法仅为兼容旧代码保留。

        实际不再需要手动获取 token，SDK 在所有 API 调用中自动注入。
        """
        logger.debug("get_tenant_access_token called — SDK manages tokens automatically")
        return ""

    def send_card(self, receive_id: str, card_json: dict) -> dict:
        """发送 Interactive Card 消息到单个目标

        Args:
            receive_id: 接收者 chat_id
            card_json: 飞书卡片 JSON

        Returns:
            API 响应 dict（含 code 和 msg）

        Raises:
            CircuitBreakerOpenError: 熔断器 OPEN 时快速失败
            RuntimeError: API 调用失败
        """
        request = (
            lark.im.v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                lark.im.v1.CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(json.dumps(card_json, ensure_ascii=False))
                .build()
            )
            .build()
        )

        # 熔断保护：如果注入 CB，通过 CB 调用；否则直接调用（向后兼容）
        if self._circuit_breaker is not None:
            response = self._circuit_breaker.call(self._send_card_impl, request)
        else:
            response = self._send_card_impl(request)

        logger.debug(f"Card sent to {receive_id}")
        return {"code": response.code, "msg": response.msg}

    @_track_feishu_api("send_card")
    def _send_card_impl(self, request) -> lark.BaseResponse:
        """send_card 的实际 API 调用（供熔断器包装）"""
        response = self._client.im.v1.message.create(request)
        if not response.success():
            logger.error(
                f"send_card failed: code={response.code}, msg={response.msg}"
            )
            raise RuntimeError(
                f"send_card failed: code={response.code}, msg={response.msg}"
            )
        return response

    def send_card_to_all(self, card_json: dict) -> list[dict]:
        """向所有配置的 chat_id 批量发送卡片（兼容遗留代码）

        注意：此方法依赖 FEISHU_CHAT_IDS 配置，已不再推荐使用。
        推送目标应由 chat_registry 动态管理。

        Returns:
            每个目标的 API 响应列表
        """
        chat_ids = self.settings.chat_ids
        if not chat_ids:
            return []

        results = []
        for cid in chat_ids:
            result = self.send_card(cid, card_json)
            results.append(result)
        logger.info(f"Card sent to {len(chat_ids)} chat(s)")
        return results

    def get_chat_info(self, chat_id: str) -> dict | None:
        """查询飞书群聊信息（含群主 open_id）

        API: GET /im/v1/chats/{chat_id}
        需要 im:chat:readonly 权限。

        Returns:
            {"chat_id": "oc_xxx", "owner_id": "ou_xxx", "name": "..."}
            失败返回 None
        """
        request = (
            lark.im.v1.GetChatRequest.builder()
            .chat_id(chat_id)
            .build()
        )

        try:
            response = self._client.im.v1.chat.get(request)
        except Exception as e:
            logger.error(f"get_chat_info exception for {chat_id}: {e}")
            return None

        if not response.success():
            logger.warning(
                f"get_chat_info failed for {chat_id}: code={response.code}, msg={response.msg}"
            )
            return None

        data = response.data
        return {
            "chat_id": getattr(data, "chat_id", chat_id),
            "owner_id": getattr(data, "owner_id", ""),
            "name": getattr(data, "name", ""),
        }
