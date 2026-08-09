# ADR-0003: WebSocket 守护线程 + 独立 asyncio 事件循环

**状态：** 已采纳

**日期：** 2026-08-09

**决策者：** 项目作者

---

## 背景

`lark-oapi` SDK 的 `WsClient` 在模块导入时调用 `asyncio.get_event_loop()` 并将事件循环引用缓存在 `_lark_ws.loop` 模块变量中。当在 FastAPI/uvicorn 环境中使用时, uvicorn 的事件循环被 SDK 捕获，但 daemon 线程中没有运行中的事件循环，导致 SDK 无法正常工作。

直接在主线程启动 `WsClient` 会阻塞 uvicorn 的事件循环。将 `WsClient` 作为异步任务放在主事件循环中运行又与 FastAPI 的生命周期管理冲突。

## 决策

**创建独立的守护线程，在线程内创建全新的 asyncio 事件循环，并通过 monkey-patching 注入 SDK 的模块变量：**

```python
# app/feishu/ws_client.py（简化）
def _run_ws_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # monkey-patch SDK 模块级 loop 变量
    import lark_oapi.ws as lark_ws_module
    lark_ws_module._lark_ws.loop = loop
    
    client = lark.ws.Client(...)
    client.start()  # blocking

thread = threading.Thread(target=_run_ws_loop, daemon=True, name="feishu-ws-client")
thread.start()
```

关键设计点：
- **daemon=True**：程序退出时自动清理，无需显式 join
- **独立 loop**：与 uvicorn 主循环完全隔离，互不干扰
- **monkey-patch**：直接修改 SDK 模块变量，是解决第三方库设计缺陷的务实手段

## 后果

### 正面

- **解决 SDK 冲突**：`lark-oapi` 与 uvicorn 和平共存
- **线程安全隔离**：WS 线程和主线程通过 MySQL / Redis / TTL Cache（带 threading.Lock）共享状态，无 GIL 竞争
- **同步处理简化**：所有事件处理器（handle_message, handle_card_action 等）都是同步函数，不引入 async/await 传染
- **故障隔离**：WS 线程崩溃不导致 HTTP 服务不可用（FastAPI 仍正常响应 /health）

### 负面

- **Monkey-patching 脆弱性**：依赖 SDK 内部实现细节（`_lark_ws.loop`），SDK 版本升级可能破坏此方案
- **同步数据库调用**：无法利用 async SQLAlchemy，高并发场景下 DB 调用可能成为瓶颈（当前规模下可接受）
- **线程管理复杂度**：需要理解两个事件循环的生命周期和线程安全约束

## 备选方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| 仅使用 HTTP Webhook | 无线程/loop 冲突 | 需要公网 URL（见 ADR-0001） |
| **守护线程 + 独立 loop（选用）** | 解决冲突、保持 WS 模式 | monkey-patching 脆弱 |
| 使用 `asyncio.run()` 在主线程 | 代码最简 | 阻塞 uvicorn |
| Fork 子进程运行 WS | 隔离最彻底 | 进程间通信复杂 |

## 相关

- ADR-0001: WebSocket 长连接 over HTTP Webhook
- `app/feishu/ws_client.py`: 完整实现
- `docs/concurrency-model.md`: 并发模型全景图
