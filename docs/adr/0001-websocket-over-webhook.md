# ADR-0001: WebSocket 长连接 over HTTP Webhook

**状态：** 已采纳

**日期：** 2026-08-09

**决策者：** 项目作者

---

## 背景

飞书 Bot 事件接收有两种方式：

1. **HTTP Webhook**：飞书服务器向 Bot 提供的公网 URL 发送 HTTP POST 请求。需要 Bot 部署在公网可达的服务器上，配置 TLS 证书，并在飞书开放平台填写 Webhook URL。
2. **WebSocket 长连接**：Bot 主动向飞书消息网关发起 WebSocket 连接，事件通过长连接推送。`lark-oapi` SDK 原生支持此模式。

本项目需要在本地开发环境运行并演示，无法提供公网域名和 TLS 证书。

## 决策

**选择 WebSocket 长连接模式**，使用 `lark-oapi` SDK 的 `WsClient`。

## 后果

### 正面

- **零部署依赖**：不需要公网 IP、域名、TLS 证书、反向代理
- **简化运维**：无需配置防火墙入站规则，无需 Webhook URL 验证签名（`app/core/security.py` 已废弃）
- **天然去重**：WebSocket 断线重连时可能收到重复消息，通过在事件路由层使用 `OrderedDict` LRU 缓存（2000 条目上限）去重
- **即插即用**：开发者 `pip install` 后直接启动即可接收事件

### 负面

- **单点长连接**：当前架构只维护一个 WS 连接，不支持水平扩展为多个 Bot 实例。如需扩展需引入连接调度机制
- **SDK 兼容性风险**：`lark-oapi` SDK 在模块导入时即调用 `asyncio.get_event_loop()`，与 uvicorn 的事件循环存在冲突（见 ADR-0003 的解决方案）
- **重连窗口期**：断线重连期间（指数退避 1s→60s）的事件会丢失，不适合对实时性要求极高的场景
- **无服务端推送保证**：飞书 WS 网关不保证 100% 消息投递，极端情况下可能丢消息

## 备选方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| HTTP Webhook | 无状态、水平扩展简单 | 需要公网 URL + TLS、验签复杂 |
| 轮询 API | 最简单 | 延迟高、API 额度消耗大、不支持实时事件 |
| **WebSocket（选用）** | 零公网依赖、实时性好 | 单点、扩展性受限 |

## 相关

- ADR-0003: WS 守护线程 + 独立 asyncio 事件循环
- `app/feishu/ws_client.py`: WebSocket 线程管理
- `app/feishu/event_router.py`: 消息去重实现
