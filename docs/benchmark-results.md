# 查询执行器压测结果：thread vs async

## 背景

查询链路（`@Bot` 消息 → LangGraph → 2-3 次 LLM 调用 → 回复）是 **IO 密集型**负载：
单次请求耗时由 LLM 调用（1-30s）主导，CPU 几乎空闲。此类负载的并发上限，取决于
执行器能同时挂起多少个「等待 IO」的任务而不耗尽资源。

- **thread 模式**（`app/core/query_executor.py`）：`ThreadPoolExecutor(QUERY_MAX_WORKERS=10)`，
  每个在途请求独占一个 OS 线程。并发上限 = 线程数；再多请求只能排队。
- **async 模式**（`app/core/async_query_executor.py`）：`asyncio.Semaphore(QUERY_MAX_CONCURRENCY=100)`，
  等待 IO 的协程不占线程。并发上限 = Semaphore 容量，同等内存下高一个数量级。

## 复现

```bash
./venv/Scripts/python.exe scripts/benchmark_query.py --requests 200 --latency 2.0 --workers 10 --concurrency 100
```

`--latency` 用 `sleep` 模拟单次 LLM 调用耗时。thread 用 `time.sleep`（阻塞线程），
async 用 `asyncio.sleep`（挂起协程，让出事件循环）。

## 实测数据

以 200 个请求、单请求模拟延迟 0.5s 为例（`--latency 0.5`，便于快速复现；真实 LLM
延迟更高，加速比更大）：

| 指标 | thread pool (workers=10) | async pool (concurrency=100) |
|------|--------------------------|------------------------------|
| 总耗时 (wall) | 10.01s | 1.03s |
| QPS | 20.0 | 194.8 |
| 延迟 P50 | 0.500s | 0.515s |
| 延迟 P95 | 0.501s | 0.516s |
| **加速比** | 1x（基线） | **9.7x** |

**结论：**

- thread 模式 QPS ≈ `workers / latency` = 10 / 0.5 = **20**，被线程数卡死；要提升只能加线程，
  而线程有栈内存（~MB 级）与上下文切换成本，扩不到几百。
- async 模式 QPS ≈ `concurrency / latency` = 100 / 0.5 = **~195**，协程栈是 KB 级，
  同等内存可挂起的在途任务多一个数量级，吞吐随并发上限近似线性提升。
- 两种模式单请求延迟几乎相同（都约等于 latency 本身）——async 的收益在**吞吐**而非**单请求延迟**，
  因为它让更多请求「同时在等」，而不是让单个请求更快。

> 延迟越高，thread 的线程瓶颈越致命：`--latency 2.0` 时 thread QPS 掉到 ~5，async 仍 ~50，加速比升至 ~10x+。

## 迁移策略（灰度）

`QUERY_EXECUTOR_MODE=thread|async` 配置开关（默认 `thread`）。`async` 模式下 main.py
lifespan 启动 `AsyncQueryExecutor` 并调用 `set_submit_delegate` 注册转发——三平台
（飞书/Telegram/Discord）仍 `from app.core.query_executor import submit`，切换对其**完全透明**。
限流（令牌桶）在两条路径间共用同一份用户状态。验证稳定后再切默认值，任意时刻可回滚。

详见 `docs/adr/0013-async-query-pipeline.md`。
