# ADR 0012: LLM 熔断 + 降级 + 令牌桶限流

**Date:** 2026-08-22
**Status:** Accepted
**Context:** 查询链路的每个请求都调 2-3 次 LLM。LLM 供应商故障/限流/超时是常态而非例外。
原实现里 LLM 调用有节点层重试（最多 3 次，每次退避），一旦供应商挂了：每个请求都要重试满
3 次 × 超时时间才失败，**占死查询池 worker**——worker 全被卡住后，所有用户只收到「系统繁忙」，
而进程和 `/health` 仍显示正常。缺一层「快速失败」的保护。同时原限流是固定窗口（2s 内 1 条），
会误杀正常连问 2-3 个问题的用户。

## Decision

三件套服务治理：
1. **LLM 熔断**：复用 `app/core/resilience.py:CircuitBreaker`，新建 `llm_circuit_breaker`（`app/llm/provider.py`），OPEN 时快速失败。
2. **降级兜底**：LLM 不可用时走关键词/文章列表 fallback，而非直接报错。
3. **令牌桶限流**：替换固定窗口，允许短突发、平滑限制持续刷屏。

## Rationale

### 熔断：为什么快速失败优于重试？

供应商已经挂了的时候，重试是**反效果**——每次重试都占用一个 worker 满一个超时周期，
故障期间 worker 被大量卡死的请求耗尽，健康请求也进不来。

熔断器三态机：连续失败 `failure_threshold=5` 次 → 转 **OPEN**，之后所有调用**立即抛
`CircuitBreakerOpenError`（<1ms）**而不真正调 LLM，worker 秒回可用；`recovery_timeout=30s` 后转
**HALF_OPEN** 放一个探针请求，成功则回 CLOSED，失败则继续 OPEN。**把「慢速失败」变成「快速失败」，
worker 不再被故障拖死。**

熔断器本身在项目里已成熟（原用于保护飞书 API，带 `cb_state` Prometheus 指标），直接复用于 LLM，labels 自动带 `cb_name="llm"`。

### 降级：LLM 挂了也要给用户一个有用的回复

熔断快速失败后不能直接甩错误给用户。三条降级链路：

- `intent_router` / `intent`：本就有**关键词启发式** fallback（如「新闻」→list、「什么时候」→qa），
  把 `CircuitBreakerOpenError` 也纳入触发条件——LLM 分类不了就用关键词猜，多数简单查询照样работает。
- `rag_answer`：LLM 生成不了答案时，**跳过生成，直接返回检索到的 top-K 文章列表**
  （「🤖 AI 生成服务暂时繁忙，以下是检索到的相关文章：」）。用户拿不到 AI 综述，但至少拿到相关文章——有损可用优于不可用。
- 新指标 `degraded_requests_total{path}` 统计各路径降级次数。

### 令牌桶：为什么替换固定窗口？

固定窗口（2s 内最多 1 条）**误杀正常用户**——群里连问 2-3 个合理问题就被限。

令牌桶（`capacity=3, refill_rate=0.5/s`）：桶里有 3 个令牌，允许**短时突发 3 条**；
之后每 2s 回填 1 个令牌，**持续刷屏仍被限**（令牌耗尽需等回填）。既容忍正常突发，又挡住恶意刷屏。
`threading.Lock` 保护，保留原 dict 的 prune 逻辑（回满的桶视为空闲用户，定期清理防无界增长）。
`QUERY_RATE_LIMIT_SECONDS=0` 时整体关闭限流（向后兼容）。

## Consequences

**Positive:**
- LLM 供应商故障时 worker 不再被拖死，查询池保持响应（回降级结果而非卡死）。
- 降级链路让「LLM 挂了」从「完全不可用」变成「有损可用」。
- 令牌桶不再误杀正常用户，同时仍挡刷屏。
- 熔断 + 限流 + 降级全部接入现有 Prometheus 指标体系。

**Negative:**
- 熔断引入「误熔断」可能：偶发抖动累积到阈值会短暂 OPEN，让本可成功的请求也降级 30s（缓解：阈值设 5，需连续失败）。
- 降级结果质量低于正常 LLM 输出，用户体验有落差（但优于报错）。
- 令牌桶状态是进程内的，多实例部署下各实例独立限流（当前单实例，不成问题）。

**Risks:**
- HALF_OPEN 探针若恰好命中另一次抖动会重新 OPEN，故障恢复后可能多等一个 recovery 周期。
- 令牌桶参数（burst=3, refill=0.5）是经验值，需根据线上刷屏模式调整。

## Alternatives Considered

### 只加重试次数上限，不熔断（rejected）
治标不治本——供应商长时间故障时，即便每请求只重试 1 次，仍在慢速失败占 worker。熔断才能快速失败。

### LLM 挂了直接回固定错误文案（rejected）
放弃了「检索结果仍可用」的价值。RAG 的文章列表 fallback 明显更有用。

### 滑动窗口限流（rejected）
比固定窗口好，但仍不如令牌桶直观地表达「允许突发 + 平滑速率」两个维度。令牌桶是限流的标准选择。
