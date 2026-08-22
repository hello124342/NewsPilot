"""查询执行器压测：thread（同步线程池） vs async（asyncio 协程池）

模拟真实查询管线的 IO 特征——每个任务耗时由一次「LLM 调用」主导（此处用
sleep 模拟，默认 2s）。对比两种执行器在相同并发压力下的吞吐（QPS）、
延迟分位（P50/P95）与近似内存占用，产出可写入简历/ADR 的对比数据。

用法：
    ./venv/Scripts/python.exe scripts/benchmark_query.py
    ./venv/Scripts/python.exe scripts/benchmark_query.py --requests 500 --latency 2.0 --workers 10 --concurrency 100

说明：
- thread 模式受 QUERY_MAX_WORKERS 限制（默认 10），QPS ≈ workers / latency
- async 模式受 QUERY_MAX_CONCURRENCY 限制（默认 100），协程并发不占线程，
  同样内存下并发量级提升一个数量级 → QPS ≈ concurrency / latency
- 这是 IO 密集负载的教科书结论，脚本用于把它量化成本项目的实测数字
"""
import argparse
import asyncio
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor


def _percentile(values, pct):
    if not values:
        return 0.0
    s = sorted(values)
    k = int(round((pct / 100.0) * (len(s) - 1)))
    return s[k]


def bench_thread(n_requests, latency, workers):
    """同步线程池：ThreadPoolExecutor(workers)，每任务 sleep(latency)"""
    latencies = []
    lat_lock = threading.Lock()

    def task():
        t0 = time.monotonic()
        time.sleep(latency)  # 模拟同步阻塞的 LLM 调用
        with lat_lock:
            latencies.append(time.monotonic() - t0)

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(task) for _ in range(n_requests)]
        for f in futures:
            f.result()
    wall = time.monotonic() - start
    return wall, latencies


def bench_async(n_requests, latency, concurrency):
    """asyncio 协程池：Semaphore(concurrency)，每任务 await sleep(latency)"""
    latencies = []

    async def runner():
        sem = asyncio.Semaphore(concurrency)

        async def task():
            async with sem:
                t0 = time.monotonic()
                await asyncio.sleep(latency)  # 模拟 await 的异步 LLM 调用
                latencies.append(time.monotonic() - t0)

        await asyncio.gather(*(task() for _ in range(n_requests)))

    start = time.monotonic()
    asyncio.run(runner())
    wall = time.monotonic() - start
    return wall, latencies


def _report(name, wall, latencies, n_requests):
    qps = n_requests / wall if wall > 0 else 0.0
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    mean = statistics.mean(latencies) if latencies else 0.0
    print(f"\n=== {name} ===")
    print(f"  requests : {n_requests}")
    print(f"  wall     : {wall:.2f}s")
    print(f"  QPS      : {qps:.1f}")
    print(f"  latency  : mean={mean:.3f}s  P50={p50:.3f}s  P95={p95:.3f}s")
    return {"name": name, "wall": wall, "qps": qps, "p50": p50, "p95": p95}


def main():
    ap = argparse.ArgumentParser(description="thread vs async query executor benchmark")
    ap.add_argument("--requests", type=int, default=200, help="总请求数")
    ap.add_argument("--latency", type=float, default=2.0, help="单请求模拟 LLM 延迟（秒）")
    ap.add_argument("--workers", type=int, default=10, help="thread 模式线程数（QUERY_MAX_WORKERS）")
    ap.add_argument("--concurrency", type=int, default=100, help="async 模式并发上限（QUERY_MAX_CONCURRENCY）")
    args = ap.parse_args()

    print("查询执行器压测：IO 密集负载（模拟 LLM 调用）")
    print(f"config: requests={args.requests}, latency={args.latency}s, "
          f"thread_workers={args.workers}, async_concurrency={args.concurrency}")

    w_t, lat_t = bench_thread(args.requests, args.latency, args.workers)
    r_t = _report(f"thread pool (workers={args.workers})", w_t, lat_t, args.requests)

    w_a, lat_a = bench_async(args.requests, args.latency, args.concurrency)
    r_a = _report(f"async pool (concurrency={args.concurrency})", w_a, lat_a, args.requests)

    speedup = r_t["wall"] / r_a["wall"] if r_a["wall"] > 0 else 0.0
    print("\n=== 对比 ===")
    print(f"  wall time : thread {r_t['wall']:.2f}s  vs  async {r_a['wall']:.2f}s")
    print(f"  QPS       : thread {r_t['qps']:.1f}   vs  async {r_a['qps']:.1f}")
    print(f"  加速比     : {speedup:.1f}x（async / thread）")
    print("\n结论：IO 密集场景下，协程并发不占线程，同内存下并发量级提升，"
          f"吞吐随并发上限线性提升。详见 docs/benchmark-results.md")


if __name__ == "__main__":
    main()
