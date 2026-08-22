# ADR 0013: 异步查询管线（线程池 → asyncio 协程池，feature-flag 灰度）

**Date:** 2026-08-22
**Status:** Accepted
**Supersedes (partial):** ADR-0006（线程池 over 全异步）——在其结论边界被业务规模突破后，本 ADR 重新评估并给出可回滚的演进路径。

**Context:** ADR-0006 当时的结论是「线程池足够好，不做全异步」，理由是业务规模「至多数十个活跃群」，
远未触及线程池上限。该结论在当时正确。但现状：统一查询池 `QUERY_MAX_WORKERS=10`，每个在途查询独占
一个 OS 线程，而单查询耗时由 LLM 调用（1-30s）主导——**并发上限被死死卡在 10**。要支撑更高并发只能加线程，
而线程栈是 MB 级、上下文切换有成本，扩不到几百。这正是 ADR-0006 自己标注的「扩展上限」风险项。

## Decision

引入 asyncio 协程池执行器 `app/core/async_query_executor.py`，通过 **`QUERY_EXECUTOR_MODE=thread|async`
配置开关（默认 `thread`）** 灰度切换。async 模式下并发上限由 `asyncio.Semaphore(QUERY_MAX_CONCURRENCY=100)`
控制，等待 IO 的协程不占线程，同等内存下并发量级提升一个数量级。

## Rationale

### 为什么现在推翻 ADR-0006 的结论？

ADR-0006 没有错——它明确写了「当前业务场景远未触及此上限」并把「扩展上限」列为已知负面。
本 ADR 不是否定它，而是**在规模增长后兑现它预留的演进空间**。IO 密集负载下，协程并发是教科书答案：
线程在 IO 等待时虽释放 GIL，但仍占着一整个线程；协程在 `await` 点让出事件循环，一个线程能挂起成百上千个在途任务。

实测（`scripts/benchmark_query.py`，200 请求 × 0.5s 模拟延迟）：thread QPS 20 → async QPS 195，**9.7x**。
延迟越高差距越大。详见 `docs/benchmark-results.md`。

### 为什么用 feature flag 灰度而非直接替换？

全异步改造面大（DB/Redis/LLM/graph 节点都要动），一次性切换风险高、不可回滚。采用可灰度策略：

- `QUERY_EXECUTOR_MODE=thread`（默认）：走原 `ThreadPoolExecutor`，**旧路径零改动、零回归**。
- `QUERY_EXECUTOR_MODE=async`：main.py lifespan 启动 `AsyncQueryExecutor`，调用
  `query_executor.set_submit_delegate()` 注册转发——三平台仍 `from app.core.query_executor import submit`，
  **切换对平台层完全透明**。
- 任意时刻改配置即可回滚。验证稳定后再切默认值。

### 执行器设计：为什么独立事件循环线程？

与飞书 WS（ADR-0003）/ Discord 网关一致的模型——独立 daemon 线程跑一个 asyncio 事件循环，
`submit()` 通过 `asyncio.run_coroutine_threadsafe()` 把协程派发到该 loop。这样：

- FastAPI 主 loop 不被查询任务占用（查询在专属 loop 上跑）。
- `submit()` 接口与同步版**逐字一致**（`submit(fn, *args, user_id=..., **kwargs) -> QuerySubmitStatus`），
  平台调用点不改。
- 背压保留：`Semaphore` 满 → `submit` 返回 `QUEUE_FULL`（语义与线程池「队列满」一致）。
- `asyncio.wait_for(timeout=QUERY_TASK_TIMEOUT_SECONDS=120)` 每任务兜底，防协程泄漏。

### 执行器同时支持同步与原生异步闭包

`_run_task` 用 `asyncio.iscoroutinefunction(fn)` 分流：

- 原生异步闭包（未来的 `graph.ainvoke`）：直接在事件循环上 `await`，真正协程并发。
- 同步闭包（当前的 `graph.invoke`）：`asyncio.to_thread(fn)` 放到默认线程池，不阻塞事件循环。

这让执行器骨架**先落地可灰度**，节点逐步异步化后无需再改执行器——迁移面被切成可控的小步。

### 限流为什么两条路径共用？

限流（令牌桶）是**提交前的纯内存判断**，与执行模型无关。async 执行器直接复用
`query_executor._allow_user`，两条路径共享同一份用户令牌桶状态——切换模式不影响限流行为。

### 什么不改

- **NewsPushGraph（5AM 批处理）不改**：低频批任务，同步够用，控制改造范围。
- **推送消费者**：Phase 3 的 Redis Stream 消费者是独立线程池，与查询执行器正交，本 ADR 不动。
- 查询任务**不经过 Redis Stream**（ADR-0011 决策：队列只用于推送链路），协程池直接执行。

## Consequences

**Positive:**
- async 模式并发上限 10 → 100+，IO 密集负载吞吐量级提升（实测 9.7x）。
- feature flag 让迁移每步可回滚，旧路径零回归。
- 平台层零改动——透明切换是 delegate 转发的直接收益。

**Negative:**
- 两套执行器并存增加维护面（但 thread 版稳定、改动冻结）。
- 完整收益需节点逐步异步化（`graph.ainvoke` + `llm.ainvoke`）；当前骨架下 async 模式对同步闭包只是线程offload，未榨干协程红利。
- asyncio 的调试与异常追踪比同步栈更绕。

**Risks:**
- 独立事件循环线程若被某个未让出的同步调用阻塞，会拖累该 loop 上所有协程（缓解：同步闭包一律走 `to_thread`）。
- 默认仍 `thread`，async 为可选路径——真正切默认前需线上灰度验证。

## Alternatives Considered

### 维持线程池，加大 max_workers（rejected）
线程栈 MB 级，扩到几百会吃光内存且上下文切换成本陡增。治标不治本。

### 一次性全异步重写（rejected）
面大不可回滚，违背项目一贯的渐进兼容策略（参考 ADR-0009 的 chat_id 渐进迁移）。

### 多进程（rejected）
解决 CPU 密集，但本负载是 IO 密集，多进程徒增内存和 IPC 成本，不对症。

## 相关
- ADR-0006: 线程池 over 全异步（本 ADR 在其预留的演进空间上推进）
- ADR-0011: Redis Stream 推送队列（查询链路有意不上队列）
- `scripts/benchmark_query.py` / `docs/benchmark-results.md`: thread vs async 实测
- `docs/concurrency-model.md`: 并发模型全景
