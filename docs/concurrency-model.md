# 并发模型

## 全景图

```
┌─ FastAPI Main Thread ──────────────────────────────────────────────────┐
│  uvicorn asyncio event loop                                             │
│  GET /health  POST /admin/*                                             │
│                                                                         │
│  APScheduler (BackgroundScheduler, 独立线程池)                            │
│    05:00  process_rss_job()      fetch + summarize + store              │
│    09:00  deliver_job("09:00")   query → 3-layer filter → send cards    │
│    12:00  deliver_job("12:00")                                          │
│    18:00  deliver_job("18:00")                                          │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
                      共享状态（线程安全）
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
     ┌────────────┐         ┌────────────┐         ┌──────────────┐
     │   MySQL    │         │   Redis    │         │  TTL Cache   │
     │ (连接池)    │         │ (连接池)    │         │ (threading.  │
     │ pool_size=5│         │            │         │  Lock 保护)   │
     └────────────┘         └────────────┘         └──────────────┘
            ▲                       ▲                       ▲
            │                       │                       │
            └───────────────────────┼───────────────────────┘
                                    │
                      共享状态（线程安全）
                                    │
┌─ WS Daemon Thread ────────────────┼─────────────────────────────────────┐
│  独立 asyncio.new_event_loop()    │                                      │
│  lark.ws.Client (blocking mode)   │                                      │
│  接收事件，提交到线程池，立即返回    │                                      │
│                                   │                                      │
│  Auto-reconnect: exp backoff 1s→60s                                     │
│  Message dedup: OrderedDict LRU (2000 entries)                          │
└───────────────────────────────────┼─────────────────────────────────────┘
                                    │
                          executor.submit()
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
     ┌────────────┐         ┌────────────┐         ┌────────────┐
     │  Worker 1  │         │  Worker 2  │         │  Worker 3  │
     │ LLM intent │         │ DB write   │         │ DB read    │
     │ 1-3 sec    │         │ 50ms       │         │ 10ms       │
     └────────────┘         └────────────┘         └────────────┘
     ┌────────────┐         ┌────────────┐
     │  Worker 4  │         │  Worker 5  │
     │ card send  │         │  idle      │
     │ 200ms      │         │            │
     └────────────┘         └────────────┘

     ThreadPoolExecutor(max_workers=5, thread_name_prefix="event-worker")
```

## 三层并发

| 层 | 机制 | 用途 |
|----|------|------|
| **FastAPI 主线程** | uvicorn asyncio event loop | HTTP 请求处理（/health, /admin/*） |
| **APScheduler 线程池** | BackgroundScheduler 内置线程池 | 定时任务执行（process_rss, deliver） |
| **事件 Worker 池** | ThreadPoolExecutor(max_workers=5) | 飞书事件并行处理 |

## 线程安全分析

### 共享状态

| 共享资源 | 访问方式 | 线程安全性 |
|----------|---------|-----------|
| MySQL (SQLAlchemy) | 每次调用独立 `SessionLocal()` | ✅ 连接池线程安全，session 不跨线程 |
| Redis (redis-py) | 每次调用独立 `RedisClient()` | ✅ redis-py 连接池线程安全 |
| TTL Cache | `chat_meta_cache.get/set/delete` | ✅ `threading.Lock` 保护 |
| Message Dedup Cache | `_seen_messages` OrderedDict | ⚠️ 仅在 WS 线程访问（提交到线程池前已去重） |

### 为什么 FeishuClient 是线程安全的

```python
# 每次 send_card() 调用独立构建 lark SDK request 对象
# SDK 内部管理 HTTP 连接池，线程安全
def send_card(self, receive_id, card_json):
    request = lark.im.v1.CreateMessageRequest.builder()...build()  # 新对象
    response = self._client.im.v1.message.create(request)           # SDK 线程安全
```

### 为什么 DB Session 是线程安全的

```python
# 每个 handler 内部：
db = SessionLocal()   # 从连接池获取新连接
try:
    ...  # 仅在当前线程使用
finally:
    db.close()         # 归还连接池
```

Session 对象不跨线程传递。

## 瓶颈分析

### 当前瓶颈：LLM API 调用

```
单个 BotQueryGraph 请求耗时分解：
  LLM intent parsing:  1000-3000 ms  ← 占 85%+
  DB search:              10-50 ms
  Card build:              1-5 ms
  Card send:            100-500 ms
  ─────────────────────────────
  总计:                 ~1111-3555 ms
```

### 线程池吞吐估算

```
5 workers × 平均 2s/请求 = 2.5 请求/秒
                          = 150 请求/分钟
```

对于一个群聊 Bot（活跃群 < 50 个，每分钟查询远小于 150），完全够用。

### 如果未来需要扩容

1. **提高 max_workers**：如果没有 LLM rate limit 约束，可增至 10-20
2. **接入消息队列**：将事件持久化到 Redis Streams / RabbitMQ，worker 独立消费
3. **水平扩展**：多实例部署 + 飞书事件分发到不同实例（需飞书开放平台支持）

当前阶段不需要这些——保持简单。

## 优雅关闭

```python
# main.py lifespan shutdown:
_event_executor.shutdown(wait=True, cancel_futures=False)
# wait=True: 等待正在处理的请求完成
# cancel_futures=False: 已提交但未开始的不取消（尽力投递语义）
```
