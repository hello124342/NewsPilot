# ADR 0011: Redis Stream 推送投递队列（仅用于推送链路）

**Date:** 2026-08-22
**Status:** Accepted
**Context:** `deliver_job` 在 09:00/12:00/18:00 给数百个 chat 群发新闻卡片，原实现是三层嵌套
内联同步发送（`遍历文章 × 平台 × chat → adapter.send_message()`）。进程半路崩溃 = 已发/未发状态全丢、
无法追溯、无法续投；单个 chat 发送失败只能跳过，无重试。这是一条**需要可靠投递**的链路。

## Decision

推送链路引入 **Redis Stream** 消息队列（`app/queue/stream_queue.py` + `deliver_consumer.py`）：
`deliver_job` 瘦身为纯生产者（查库 + 三层过滤 + 逐条 enqueue），N 个独立消费线程负责实际发送，
实现 at-least-once 投递 + 自动重试 + 死信队列 + 幂等去重。

**交互式查询链路有意不上队列**——见下。

## Rationale

### 为什么查询链路不上队列，推送链路上？（核心 trade-off）

| | 查询链路 | 推送链路 |
|---|---|---|
| 用户是否在等 | 在线等回复 | 不在场（定时批量） |
| 消息丢了怎么办 | 重问即可，代价小 | 漏发且无从知晓，代价大 |
| at-least-once 的副作用 | **重启后补发旧回复**——用户已走，收到过期答案，体验差 | 补发未送达的卡片——正是想要的 |
| 现有机制是否够 | 线程池 + 令牌桶已够 | 内联同步发送崩溃即丢，不够 |

结论：at-least-once 的「重启续投」对推送是刚需、对查询是负担。**不为了架构统一而给查询强加队列。**
这个有意的边界划分本身就是设计能力的体现。

### 为什么 Redis Stream 而非 RabbitMQ / Kafka？

- **已有 Redis**：项目已用 Redis 做去重缓存和 token 缓存，不引新组件、不加运维面。
- **消息量级**：每天 3 次 × 数百 chat = 千级消息/天，Stream 轻松扛住；Kafka 是给百万级吞吐设计的，杀鸡用牛刀。
- **Stream 原生具备所需能力**：consumer group（多消费者负载均衡）、XACK（确认）、
  XAUTOCLAIM（接管崩溃消费者的未确认消息）、XPENDING（积压可观测）——刚好覆盖需求，无需自己造。

### at-least-once 为什么必须配幂等去重？

at-least-once 意味着**同一条消息可能被投递多次**（消费者崩溃后 XAUTOCLAIM 重投）。
不去重 = 用户收到重复卡片。发送前抢幂等锁：`SET sent:{article_id}:{platform}:{conversation_id} NX EX 86400`，
抢到锁才发——重投的消息抢不到锁，直接跳过。at-most-once 的「重复」被幂等锁挡在发送之前。

### 消息体为什么只放 id 不放卡片 JSON？

消息体 = `{article_id, platform, conversation_id, push_time, retry_count, enqueued_at}`——纯数据、可序列化、瘦身。
消费侧按 `article_id` 查库现渲染 RichMessage。好处：消息小、Stream 内存省；渲染逻辑改了不影响在途消息。

### 重试与死信

`claim_stale`（min_idle 60s）周期性捞回未 ACK 的消息，`retry_count+1` 重投；超 `DELIVER_MAX_RETRY=3` 次
进死信 stream `deliver_dlq` + 告警日志。避免坏消息无限重投占死消费者。

### Redis 不可用时的降级

`get_stream_queue()` 在 Redis 不可用时返回 None，`deliver_job` **fallback 到原内联同步发送**
（`queue_fallback_total.inc()`）。Redis 挂了推送功能不挂——只是暂时失去可靠投递保证。消费者同理，
Redis 不可用时不启动，内联路径接管。

## Consequences

**Positive:**
- 进程崩溃零漏发：未 ACK 消息留在 stream，重启后消费者续投。
- 单 chat 发送失败自动重试，不再「跳过即永久丢失」。
- `deliver_job` 从「秒级阻塞群发」变成「秒级入队返回」，生产与消费解耦、削峰填谷。
- 新指标 `deliver_queue_depth` / `deliver_pending_messages` / `deliver_retry_total` / `deliver_dlq_total` 全链路可观测。

**Negative:**
- 引入 at-least-once 的复杂度：必须配幂等锁，漏了就重复发送。
- 消费者是独立线程池，增加了进程内的并发面和优雅关闭逻辑。
- URL 标记语义调整（「发送成功才 mark」→「入队成功即 mark」），可靠性责任转移给队列。

**Risks:**
- 幂等锁 TTL 86400s——若消息在锁过期后才重投（极端积压），可能重复发送。缓解：TTL 远大于正常投递窗口。
- DLQ 需要人工介入清理，无自动重放机制（当前规模可接受）。

## Alternatives Considered

### 查询 + 推送统一上队列（rejected）
架构统一但给查询带来「重启补发旧回复」的体验问题，见上表。有意不选。

### RabbitMQ / Kafka（rejected）
消息量级和运维成本不匹配，且需引入新组件。已有 Redis 的 Stream 足够。

### 数据库表当队列（rejected）
用 MySQL 表 + 状态列轮询模拟队列。轮询有延迟和空转开销，且缺 consumer group / XAUTOCLAIM 等原生语义，得自己造一堆轮子。
