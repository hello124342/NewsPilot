# ADR-0006: 线程池 over 全异步 for 事件处理并发

**状态：** 已采纳

**日期：** 2026-08-09

**决策者：** 项目作者

---

## 背景

WebSocket 守护线程（见 ADR-0003）以同步方式处理所有飞书事件。当 `handle_message()` 调用 `BotQueryGraph`，其中包含 LLM intent 解析（1-3 秒），这段时间内 WS 线程被阻塞，无法处理其他用户的事件。多用户并发场景下存在明显的排队延迟。

需要引入并发处理机制。两个候选方案：

1. **线程池（ThreadPoolExecutor）**：WS 线程将事件提交到线程池后立即返回，worker 线程并行处理
2. **全异步（asyncio + async/await）**：将所有 I/O 操作改为 async，利用协程并发

## 决策

**采用线程池（ThreadPoolExecutor）处理事件并发，不采用全异步改造。**

核心逻辑：

```
WS Thread (Producer)              Worker Pool (Consumers)
──────────────────               ──────────────────────
收到事件 → executor.submit()     [Worker 1: LLM intent 2s]
  ↑ 立即返回，不阻塞              [Worker 2: card action 50ms]
                                 [Worker 3: subscribe 10ms]
                                 [Worker 4: idle]
                                 [Worker 5: idle]
```

具体参数：
- `max_workers=5`：受 LLM API rate limit 约束，更多 worker 不会提升吞吐
- `thread_name_prefix="event-worker"`：便于日志和调试
- 异常在 worker 内部捕获并记录，不传播到 WS 线程导致断连

## 后果

### 正面

- **零侵入**：现有同步代码（handler、graph node、DB session、FeishuClient）完全不用改
- **I/O 并发有效**：Python 线程在 I/O 等待时释放 GIL，本项目 100% 是网络 I/O，线程池并发效率接近 asyncio
- **异常隔离**：worker 内崩溃不影响 WS 线程和其他 worker
- **代码一致性**：项目本就是同步风格，线程池保持了这种一致性

### 负面

- **线程安全需要验证**：共享的 TTL Cache 已有 `threading.Lock` 保护。DB session 每次独立创建。FeishuClient 每次调用独立构建 request。当前设计是线程安全的，但后续开发需保持警惕
- **扩展上限**：线程池受 GIL 和系统线程数限制，难以支撑数千并发。当前业务场景（群聊 Bot，至多数十个活跃群）远未触及此上限
- **不适用于 CPU 密集型**：如果未来需要本地推理或大量计算，线程池会因 GIL 退化。但当前所有重操作都是 LLM API 调用（网络 I/O）

## 备选方案

| 方案 | I/O 并发 | 改造成本 | CPU 并发 | 适用场景 |
|------|---------|---------|---------|---------|
| **线程池（选用）** | ✅ 好 | ✅ 零 | ❌ GIL 限制 | I/O 密集、已有同步代码 |
| 全异步 | ✅ 最好 | ❌ 重写全部 | ❌ 同线程池 | 数千+ 并发连接 |
| 单线程同步 | ❌ 串行 | ✅ 零 | ❌ | 单个/少数用户 |
| 多进程 | ✅ 好 | ❌ 重构 | ✅ 最好 | CPU 密集 + I/O 密集 |

## 为什么 Python 线程池在此场景下足够好

关键事实：**Python 的 GIL 只在 CPU 计算时持有，网络 I/O 操作（socket read/write）会释放 GIL。**

本项目的 worker 时间消耗分布：
- LLM API 等待：1-3 秒（网络 I/O，GIL 释放）
- MySQL 查询：10-50ms（网络 I/O，GIL 释放）
- Redis 操作：1-5ms（网络 I/O，GIL 释放）
- CPU 计算：<5ms（JSON 序列化/反序列化，GIL 持有但极短）

Worker 90%+ 的时间在等网络，GIL 完全不构成瓶颈。5 个线程的 I/O 并发实际效果接近 5 个 asyncio 协程。

## 相关

- ADR-0003: WS 守护线程 + 独立 event loop
- `app/feishu/event_router.py`: 事件分发 + 去重
- `docs/concurrency-model.md`: 并发模型全景图
