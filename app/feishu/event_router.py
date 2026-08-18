"""WebSocket 事件分发器

将飞书长连接推送的各类事件路由到对应的业务处理函数。
事件通过线程池异步处理：WS 线程仅负责接收事件和提交任务，立即返回不阻塞。
Worker 线程并行处理事件（LLM 调用、DB 操作、卡片发送）。

并发模型：Producer-Consumer
- Producer: WS daemon 线程 → executor.submit()
- Consumer: ThreadPoolExecutor(max_workers=5) → handler 函数

事件类型：
- im.message.receive_v1 — @Bot 消息（文本/卡片回调）
- im.chat.member.bot.added_v1 — Bot 被拉入群
- im.chat.member.bot.deleted_v1 — Bot 被移出群
"""
import json
import logging
from collections import OrderedDict

import lark_oapi as lark
from app.core.config import Settings
from app.core.query_executor import submit as query_submit, QuerySubmitStatus
from app.subscription.handler import detect_command

logger = logging.getLogger(__name__)

# ========== 事件处理：统一派发到共享查询池 ==========
# 并发模型（Producer-Consumer，见 app/core/query_executor.py）：
# - Producer: WS 线程 → query_executor.submit() 立即返回，不阻塞
# - Consumer: ThreadPoolExecutor(QUERY_MAX_WORKERS) 执行业务逻辑
# 有界队列打满或用户触发限流时丢弃，消息路径回「系统繁忙」卡片提示。


def _dispatch_async(fn, *args, user_id=None, **kwargs):
    """将事件处理提交到共享查询池（fire-and-forget）

    WS 线程调用此函数后立即返回。
    异常在 worker 内部捕获并记录日志，不传播到 WS 线程导致断连。
    """

    def _safe():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception(f"Unhandled error in event worker: {fn.__name__}")

    return query_submit(_safe, user_id=user_id)


def shutdown_executor(wait: bool = True) -> None:
    """优雅关闭查询池（由 main.py lifespan 调用）

    Args:
        wait: True 等待正在处理的请求完成后再关闭
    """
    from app.core.query_executor import shutdown as query_shutdown
    query_shutdown(wait=wait)
    logger.info("Event executor shut down")


def _dispatch_message_event(e, feishu) -> None:
    """派发 @Bot 消息到查询池；被丢弃时回「系统繁忙」卡片

    限流 / 队列打满时用户会立即收到一条提示卡片，而非消息无声消失。
    """
    chat_id = ""
    sender_id = ""
    try:
        if e.event.message:
            chat_id = e.event.message.chat_id or ""
        if e.event.sender and e.event.sender.sender_id:
            sender_id = e.event.sender.sender_id.open_id or ""
    except Exception:
        pass

    status = query_submit(lambda: handle_message(e, feishu), user_id=sender_id)
    if status is not QuerySubmitStatus.ACCEPTED:
        _send_busy_card(feishu, chat_id)


def _send_busy_card(feishu, chat_id: str) -> None:
    """发送「系统繁忙」提示卡片（查询被丢弃时）"""
    if not chat_id or not feishu:
        return
    try:
        busy_card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text", "content": "⛔ 系统繁忙"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "当前查询人数较多，请稍后再试。"}},
            ],
        }
        feishu.send_card(chat_id, busy_card)
    except Exception as e:
        logger.warning(f"Failed to send busy card to {chat_id}: {e}")

# 消息去重缓存（避免 WS 重连导致的重复事件）
_seen_messages: OrderedDict[str, bool] = OrderedDict()
_MAX_DEDUP = 2000


def _dedup(message_id: str) -> bool:
    """检查 message_id 是否已处理过。返回 True 表示重复。"""
    if not message_id:
        return False
    if message_id in _seen_messages:
        return True
    _seen_messages[message_id] = True
    if len(_seen_messages) > _MAX_DEDUP:
        _seen_messages.popitem(last=False)
    return False


# ========== 事件 Handler ==========


def handle_card_action_trigger(
    event,
    feishu_client_obj,
) -> None:
    """处理卡片按钮点击事件（card.action.trigger）

    WebSocket 模式下按钮点击通过此事件类型接收，与 im.message.receive_v1 不同。

    事件结构：
      event.event.action.value  → 按钮 value（如 "unsubscribe:OpenAI"）
      event.event.context.open_chat_id → chat_id
      event.event.operator.open_id     → 点击者
    """
    event_data = event.event
    if not event_data:
        return

    # 提取 chat_id
    ctx = event_data.context
    if ctx:
        chat_id = ctx.open_chat_id or ""
    else:
        chat_id = ""

    # 提取操作者
    operator = event_data.operator
    if operator:
        sender_id = operator.open_id or ""
    else:
        sender_id = ""

    # 提取 action value
    action = event_data.action
    if not action:
        logger.warning("card_action_trigger: no action in event")
        return

    action_value = action.value
    if not action_value:
        logger.warning("card_action_trigger: no action value")
        return

    # 兼容 dict 格式（新卡片）和字符串格式（旧卡片/向后兼容）
    if isinstance(action_value, dict):
        # 新格式：{"action": "unsubscribe", "vendor": "OpenAI"} 或 {"action": "settings"}
        action_dict = action_value
    elif isinstance(action_value, str):
        # 旧格式字符串 "unsubscribe:OpenAI" / "settings:open" / "set_time:09:00"
        action_dict = action_value
    else:
        logger.warning(f"card_action_trigger: unexpected value type: {type(action_value)}")
        return

    logger.info(f"Card action: chat_id={chat_id}, user={sender_id}, action={action_dict}")
    _handle_card_action_with_sender(chat_id, sender_id, action_dict, feishu_client_obj)


def handle_message(
    event: lark.im.v1.P2ImMessageReceiveV1,
    feishu_client_obj,
) -> None:
    """处理 @Bot 文本消息 + 卡片 action（WebSocket 模式）

    卡片 action 在 WebSocket 模式中通过消息通道接收（msg_type="interactive"），
    此处统一处理文本消息和卡片回调。
    """
    # 从 SDK 事件对象提取字段
    msg = event.event.message
    message_id = msg.message_id
    chat_id = msg.chat_id
    msg_type = msg.message_type
    sender_id = (
        event.event.sender.sender_id.open_id
        if event.event.sender and event.event.sender.sender_id
        else ""
    )

    # 去重（仅对 message 事件，card action 无 message_id 则不过滤）
    if message_id and _dedup(message_id):
        return

    from app.chat.lifecycle import is_new_chat, register_chat

    # --- 处理卡片 action（msg_type == "interactive"） ---
    if msg_type == "interactive":
        _handle_card_action_ws(msg, chat_id, sender_id, feishu_client_obj)
        return

    # --- 处理文本消息 ---
    if msg_type != "text":
        return

    # 解析消息文本
    content_raw = msg.content
    try:
        content_obj = json.loads(content_raw)
        query_text = content_obj.get("text", "").strip()
    except (json.JSONDecodeError, TypeError):
        query_text = content_raw.strip()

    if not query_text:
        return

    # 首次私聊检测
    if is_new_chat(chat_id):
        cmd = detect_command(query_text)
        if cmd:
            register_chat(chat_id, chat_type="user")
            _dispatch_subscription_command(cmd, chat_id, sender_id, feishu_client_obj)
            return

        # 非命令 → 注册 + 欢迎卡片，然后继续处理查询（不 return）
        register_chat(chat_id, chat_type="user")
        from app.feishu.card_builder import build_welcome_card
        try:
            card = build_welcome_card()
            feishu_client_obj.send_card(chat_id, card)
            logger.info(f"User onboarded: {chat_id}, guide card sent")
        except Exception as e:
            logger.error(f"Failed to send user guide card: {e}")
        # 继续走到下方的 BotQueryGraph 处理用户的查询

    # 已注册 chat → 正常处理
    cmd = detect_command(query_text)
    if cmd:
        _dispatch_subscription_command(cmd, chat_id, sender_id, feishu_client_obj)
        return

    # 非订阅命令 → BotQueryGraph
    from app.graph.state import QueryState
    from app.graph.bot_query_graph import build_query_graph

    state: QueryState = {
        "platform": "feishu",
        "user_id": sender_id,
        "chat_id": chat_id,
        "user_query": query_text,
    }
    graph = build_query_graph()
    result = graph.invoke(state)
    query_type = result.get("query_type", "list")
    if query_type == "qa":
        rag = result.get("rag_answer", {})
        logger.info(
            f"Query (qa): '{query_text[:50]}' → {len(rag.get('sources', []))} sources"
        )
    else:
        logger.info(
            f"Query (list): '{query_text[:50]}' → {len(result.get('query_results', []))} results"
        )


def handle_bot_added(
    event: lark.im.v1.P2ImChatMemberBotAddedV1,
    feishu_client_obj,
) -> None:
    """Bot 被拉入群聊 — 自动注册 + 默认订阅全部 + 欢迎卡片"""
    chat_id = event.event.chat_id
    if not chat_id:
        logger.warning("bot_added_to_chat: chat_id missing")
        return

    from app.chat.lifecycle import register_chat, set_owner_id
    from app.subscription.handler import subscribe, ALL_VENDORS
    from app.feishu.card_builder import build_group_welcome_card

    is_new = register_chat(chat_id, chat_type="group")

    if is_new:
        for vendor in ALL_VENDORS:
            subscribe(chat_id, vendor)

        try:
            info = feishu_client_obj.get_chat_info(chat_id)
            if info and info.get("owner_id"):
                set_owner_id(chat_id, info["owner_id"])
                logger.info(f"Group owner cached: {chat_id} → {info['owner_id']}")
        except Exception as e:
            logger.warning(f"Failed to fetch group owner: {e}")
        logger.info(f"Group onboarded: {chat_id}, auto-subscribed all vendors")

    try:
        card = build_group_welcome_card()
        feishu_client_obj.send_card(chat_id, card)
        logger.info(f"Group welcome card sent: {chat_id}")
    except Exception as e:
        logger.error(f"Failed to send group welcome card: {e}")


def handle_bot_removed(
    event: lark.im.v1.P2ImChatMemberBotDeletedV1,
) -> None:
    """Bot 被移出群聊 — 标记 inactive"""
    chat_id = event.event.chat_id
    if not chat_id:
        return

    from app.chat.lifecycle import deactivate_chat
    deactivate_chat(chat_id)
    logger.info(f"Bot removed from chat: {chat_id}")


# ========== 内部辅助 ==========


def _handle_card_action_with_sender(
    chat_id: str,
    operator_id: str,
    action_value,
    feishu_client_obj,
) -> None:
    """处理卡片按钮点击（内部函数）

    Args:
        action_value: dict，如 {"action": "unsubscribe", "vendor": "OpenAI"}
                      或 {"action": "settings"} / {"action": "set_time", "time": "09:00"}

    优化：所有 DB 操作复用同一个 session，读操作优先走内存缓存。
    """
    if not chat_id:
        logger.warning("card_action: chat_id not found")
        return

    from app.db.database import SessionLocal
    from app.subscription.handler import (
        unsubscribe,
        list_subscriptions,
        get_preference,
        set_push_time,
        set_frequency,
    )
    from app.feishu.card_builder import (
        build_subscription_reply,
        build_settings_card,
    )
    from app.chat.lifecycle import can_manage_subscription

    # 兼容旧格式（字符串）和新格式（dict）
    if isinstance(action_value, str):
        action_type = action_value.split(":", 1)[0] if ":" in action_value else action_value
        action_data = {}
        if action_value.startswith("unsubscribe:"):
            action_type = "unsubscribe"
            action_data = {"vendor": action_value.split(":", 1)[1]}
        elif action_value.startswith("set_time:"):
            action_type = "set_time"
            action_data = {"time": action_value.split(":", 1)[1]}
        elif action_value.startswith("set_freq:"):
            action_type = "set_freq"
            action_data = {"freq": action_value.split(":", 1)[1]}
    else:
        action_type = action_value.get("action", "")
        action_data = action_value

    # 打开一个共享 DB session（用于权限检查和读操作）
    db = SessionLocal()
    try:
        # 权限检查（走缓存 + 共享 session，不再重复开 session）
        modify_actions = {"unsubscribe", "set_time", "set_freq", "settings", "subs:manage"}
        if action_type in modify_actions:
            if not can_manage_subscription(chat_id, operator_id, db=db):
                logger.info(
                    f"Card action denied: operator={operator_id}, chat={chat_id}, action={action_type}"
                )
                return

        if action_type == "unsubscribe":
            vendor = action_data.get("vendor", "")
            if vendor:
                unsubscribe(chat_id, vendor, db=db)
                card = build_subscription_reply("unsubscribe", vendor)
                feishu_client_obj.send_card(chat_id, card)
                logger.info(f"Card unsubscribe: chat_id={chat_id}, vendor={vendor}")

        elif action_type == "subs:manage":
            subs = list_subscriptions(chat_id, db=db)
            from app.feishu.card_builder import build_subscription_list_card
            card = build_subscription_list_card(subs)
            feishu_client_obj.send_card(chat_id, card)

        elif action_type == "settings":
            pref = get_preference(chat_id, db=db)
            subs = list_subscriptions(chat_id, db=db)
            card = build_settings_card(subs, pref["push_time"], pref["frequency"])
            feishu_client_obj.send_card(chat_id, card)
            logger.info(f"Settings card sent: chat_id={chat_id}")

        elif action_type == "set_time":
            push_time = action_data.get("time", "09:00")
            pref = set_push_time(chat_id, push_time, db=db)
            subs = list_subscriptions(chat_id, db=db)
            card = build_settings_card(subs, pref["push_time"], pref["frequency"])
            feishu_client_obj.send_card(chat_id, card)
            logger.info(f"Push time updated: chat_id={chat_id}, time={push_time}")

        elif action_type == "set_freq":
            freq = action_data.get("freq", "daily")
            pref = set_frequency(chat_id, freq, db=db)
            subs = list_subscriptions(chat_id, db=db)
            card = build_settings_card(subs, pref["push_time"], pref["frequency"])
            feishu_client_obj.send_card(chat_id, card)
            logger.info(f"Frequency updated: chat_id={chat_id}, freq={freq}")

    except Exception as e:
        logger.error(f"Card action failed for action={action_type}: {e}")
    finally:
        db.close()


# ---- 临时的 WS 消息级 card action 处理适配 ----
# 在 handle_message 中检测到 interactive 消息时调用

def _handle_card_action_ws(
    msg,
    chat_id: str,
    sender_id: str,
    feishu_client_obj,
) -> None:
    """WebSocket 通道的卡片 action — 从 msg 解析 action 信息并分发

    飞书卡片回调格式：
    {
      "action": {
        "value": "unsubscribe:OpenAI",
        "tag": "button",
        ...
      },
      "open_id": "ou_xxx",
      "open_chat_id": "oc_xxx",
      ...
    }
    """
    try:
        raw = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
    except (json.JSONDecodeError, TypeError):
        return

    # action 是一个 dict，value 在内层
    action_obj = raw.get("action", {})
    action_value = ""
    if isinstance(action_obj, dict):
        action_value = action_obj.get("value", "")
    elif isinstance(action_obj, str):
        action_value = action_obj

    if not action_value:
        return

    _handle_card_action_with_sender(
        chat_id, sender_id, action_value, feishu_client_obj
    )


def _dispatch_subscription_command(
    cmd: tuple,
    chat_id: str,
    sender_id: str,
    feishu_client_obj,
) -> None:
    """分发订阅/退订/列表/设置命令，回复相应卡片

    优化：所有 DB 操作复用同一个 session，读操作优先走内存缓存。
    """
    from app.db.database import SessionLocal
    from app.subscription.handler import (
        subscribe,
        unsubscribe,
        list_subscriptions,
        get_preference,
        set_push_time,
        set_frequency,
        ALL_VENDORS,
    )
    from app.feishu.card_builder import (
        build_subscription_reply,
        build_subscription_list_card,
        build_settings_card,
    )
    from app.chat.lifecycle import can_manage_subscription

    action, vendor = cmd

    db = SessionLocal()
    try:
        # 权限检查（走缓存 + 共享 session）
        _modify_actions = {"subscribe", "unsubscribe", "set_time", "set_freq", "settings"}
        if action in _modify_actions and action != "list":
            if not can_manage_subscription(chat_id, sender_id, db=db):
                try:
                    deny_card = {
                        "config": {"wide_screen_mode": True},
                        "header": {
                            "template": "red",
                            "title": {"tag": "plain_text", "content": "⛔ 权限不足"},
                        },
                        "elements": [
                            {
                                "tag": "div",
                                "text": {
                                    "tag": "lark_md",
                                    "content": "只有**群主**可以修改本群的订阅设置。\n\n你可以发送「**我的订阅**」查看群当前的订阅状态。",
                                },
                            }
                        ],
                    }
                    feishu_client_obj.send_card(chat_id, deny_card)
                except Exception:
                    pass
                return

        # 执行命令
        card = None
        if action == "subscribe":
            if vendor == "__ALL__":
                for v in ALL_VENDORS:
                    subscribe(chat_id, v, db=db)
                subscribed = list_subscriptions(chat_id, db=db)
                card = build_subscription_list_card(subscribed)
            else:
                subscribe(chat_id, vendor, db=db)
                card = build_subscription_reply("subscribe", vendor)

        elif action == "unsubscribe":
            if vendor == "__ALL__":
                for v in ALL_VENDORS:
                    unsubscribe(chat_id, v, db=db)
                subscribed = list_subscriptions(chat_id, db=db)
                card = build_subscription_list_card(subscribed)
            else:
                unsubscribe(chat_id, vendor, db=db)
                card = build_subscription_reply("unsubscribe", vendor)

        elif action == "list":
            subscribed = list_subscriptions(chat_id, db=db)
            card = build_subscription_list_card(subscribed)

        elif action == "settings":
            pref = get_preference(chat_id, db=db)
            subs = list_subscriptions(chat_id, db=db)
            card = build_settings_card(subs, pref["push_time"], pref["frequency"])

        elif action == "set_time":
            pref = set_push_time(chat_id, vendor, db=db)  # vendor slot holds time value
            subs = list_subscriptions(chat_id, db=db)
            card = build_settings_card(subs, pref["push_time"], pref["frequency"])

        elif action == "set_freq":
            pref = set_frequency(chat_id, vendor, db=db)  # vendor slot holds freq value
            subs = list_subscriptions(chat_id, db=db)
            card = build_settings_card(subs, pref["push_time"], pref["frequency"])
    finally:
        db.close()

    if card:
        try:
            feishu_client_obj.send_card(chat_id, card)
        except Exception as e:
            logger.error(f"Failed to send subscription reply: {e}")


# ========== 构建 EventDispatcherHandler ==========


def build_event_handler(feishu_client_obj):
    """构建 SDK 事件分发器，注册所有需要处理的事件类型

    Args:
        feishu_client_obj: FeishuClient 实例（用于在 handler 中发送消息）

    Returns:
        lark.EventDispatcherHandler 实例
    """
    # 闭包：将 feishu_client_obj 绑定到每个 handler
    feishu = feishu_client_obj

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(
            lambda e: _dispatch_message_event(e, feishu)
        )
        .register_p2_im_chat_member_bot_added_v1(
            lambda e: _dispatch_async(handle_bot_added, e, feishu)
        )
        .register_p2_im_chat_member_bot_deleted_v1(
            lambda _: _dispatch_async(handle_bot_removed, _)
        )
        .register_p2_card_action_trigger(
            lambda e: _dispatch_async(handle_card_action_trigger, e, feishu)
        )
        .build()
    )
