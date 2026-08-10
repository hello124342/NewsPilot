"""飞书 WebSocket 长连接客户端（独立线程 + 独立 event loop）

lark-oapi 的 WsClient 在 import 时捕获 asyncio.get_event_loop()，
与 uvicorn 的 event loop 冲突。解决方案：在 daemon 线程中创建独立 loop。

特性：
- 独立线程 + 独立 event loop（不与 FastAPI/uvicorn 冲突）
- 断线自动重连（指数退避 1s → 60s）
- daemon 线程，程序退出时自动清理
"""
import asyncio
import logging
import threading
import time

import lark_oapi as lark
import lark_oapi.ws.client as _lark_ws

logger = logging.getLogger(__name__)


def run_ws_client(
    app_id: str,
    app_secret: str,
    event_handler,
) -> None:
    """在独立线程中运行 WebSocket 长连接客户端（带自动重连）

    创建独立的 asyncio event loop，patch SDK 模块级 loop 变量，
    然后以阻塞方式启动 WsClient。

    Args:
        app_id: 飞书应用 App ID
        app_secret: 飞书应用 App Secret
        event_handler: lark.EventDispatcherHandler 实例
    """
    # 创建独立 event loop
    ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ws_loop)

    # Patch SDK 模块级 loop（SDK 在 import 时捕获了 FastAPI 的 loop）
    _lark_ws.loop = ws_loop

    retry_delay = 1  # 初始重连间隔 1s
    max_delay = 60   # 最大重连间隔 60s

    while True:
        try:
            ws_client = lark.ws.Client(
                app_id=app_id,
                app_secret=app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
            )
            logger.info("WebSocket client connecting to Feishu...")
            _emit_ws_metric(connected=True)
            ws_client.start()  # blocking
        except Exception as e:
            logger.warning(
                f"WebSocket disconnected: {e}, retrying in {retry_delay}s"
            )
            _emit_ws_metric(connected=False)

        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, max_delay)


def start_ws_thread(
    app_id: str,
    app_secret: str,
    event_handler,
) -> threading.Thread:
    """启动 WebSocket 长连接的后台线程

    Args:
        app_id: 飞书应用 App ID
        app_secret: 飞书应用 App Secret
        event_handler: lark.EventDispatcherHandler 实例

    Returns:
        已启动的 daemon 线程
    """
    thread = threading.Thread(
        target=run_ws_client,
        args=(app_id, app_secret, event_handler),
        daemon=True,
        name="feishu-ws-client",
    )
    thread.start()
    logger.info(f"WebSocket thread started (daemon): {thread.name}")
    return thread


def _emit_ws_metric(connected: bool) -> None:
    """将 WebSocket 连接状态推送到 Prometheus（延迟导入避免循环依赖）"""
    try:
        from app.core.metrics import ws_connection_status, ws_disconnect_total
        if connected:
            ws_connection_status.set(1)
        else:
            ws_connection_status.set(0)
            ws_disconnect_total.inc()
    except ImportError:
        pass
