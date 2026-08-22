"""异步查询执行器（asyncio 协程池）

与同步 query_executor 对等的实现，供 QUERY_EXECUTOR_MODE=async 时启用。模型与
飞书 WS / Discord 网关线程一致：独立 daemon 线程跑一个 asyncio 事件循环，平台层
submit() 后立即返回，协程在该 loop 上并发执行。

为什么协程池优于线程池：查询管线是 IO 密集（LLM 1-30s、DB、Redis、ChromaDB），
线程池并发受限于 worker 数（默认 10），协程池仅受 Semaphore 与内存制约，同样内存
下并发量级可提升一个数量级。背压语义保留：Semaphore 满 → submit 返回 QUEUE_FULL。

限流复用同步执行器的令牌桶实现（`query_executor._allow_user`）——限流是提交前的
纯内存判断，与执行模型无关，两条路径共用同一份用户令牌桶状态。

提交进来的 fn 是同步的业务闭包（内部 graph.invoke）。async 模式下用
`asyncio.to_thread(fn)` 把它放到默认线程池执行，避免阻塞事件循环；真正的收益在
Phase 4.2 节点异步化后（fn 内改走 graph.ainvoke）体现，此处先落地可灰度的执行器骨架。
"""
import asyncio
import logging
import threading
import time
from typing import Callable, Optional

from app.core.config import Settings
from app.core.metrics import (
    query_queue_depth,
    query_workers_busy,
    query_dropped_total,
    query_processed_total,
    query_queue_wait_seconds,
)
# 复用同步执行器的枚举与令牌桶限流，避免两套限流状态
from app.core.query_executor import QuerySubmitStatus, _allow_user

logger = logging.getLogger(__name__)


class AsyncQueryExecutor:
    """asyncio 协程池执行器（独立事件循环线程）"""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or Settings()  # type: ignore[call-arg]
        self._max_concurrency = max(1, self._settings.QUERY_MAX_CONCURRENCY)
        self._task_timeout = max(1.0, self._settings.QUERY_TASK_TIMEOUT_SECONDS)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._sem: Optional[asyncio.Semaphore] = None
        self._pending = 0
        self._active = 0
        self._counter_lock = threading.Lock()
        self._started = False

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._started:
            return
        self._thread = threading.Thread(target=self._run_loop, name="async-query-loop", daemon=True)
        self._thread.start()
        # 等待 loop 就绪
        while self._loop is None:
            time.sleep(0.01)
        self._started = True
        logger.info(
            f"AsyncQueryExecutor started: max_concurrency={self._max_concurrency}, "
            f"task_timeout={self._task_timeout}s"
        )

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._sem = asyncio.Semaphore(self._max_concurrency)
        self._loop = loop
        try:
            loop.run_forever()
        finally:
            loop.close()

    def shutdown(self, wait: bool = True) -> None:
        if not self._started or self._loop is None:
            return
        loop = self._loop
        self._started = False
        loop.call_soon_threadsafe(loop.stop)
        if wait and self._thread is not None:
            self._thread.join(timeout=5.0)
        logger.info("AsyncQueryExecutor shut down")

    # ---- 计数 / 指标 ----

    def _mark_pending(self, delta: int) -> None:
        with self._counter_lock:
            self._pending += delta
            query_queue_depth.set(self._pending)

    def _mark_active(self, delta: int) -> None:
        with self._counter_lock:
            self._active += delta
            query_workers_busy.set(max(0, self._active))

    # ---- 提交 ----

    def submit(self, fn: Callable, *args, user_id: Optional[str] = None, **kwargs) -> QuerySubmitStatus:
        """派发任务到协程池（fire-and-forget），接口与同步执行器一致"""
        if not self._started or self._loop is None or self._sem is None:
            return QuerySubmitStatus.QUEUE_FULL

        # 每用户令牌桶限流（复用同步执行器状态）
        if not _allow_user(user_id):
            query_dropped_total.labels(reason="rate_limited").inc()
            return QuerySubmitStatus.RATE_LIMITED

        # 背压：Semaphore 满即拒绝（非阻塞判断）
        if self._sem.locked() or self._pending >= self._max_concurrency:
            query_dropped_total.labels(reason="queue_full").inc()
            logger.warning(f"async_query_executor: at capacity, dropping {getattr(fn, '__name__', fn)}")
            return QuerySubmitStatus.QUEUE_FULL

        t_submit = time.monotonic()
        self._mark_pending(1)
        asyncio.run_coroutine_threadsafe(
            self._run_task(fn, args, kwargs, t_submit), self._loop
        )
        return QuerySubmitStatus.ACCEPTED

    async def _run_task(self, fn: Callable, args: tuple, kwargs: dict, t_submit: float) -> None:
        assert self._sem is not None
        async with self._sem:
            wait = time.monotonic() - t_submit
            query_queue_wait_seconds.observe(wait)
            self._mark_active(1)
            try:
                if asyncio.iscoroutinefunction(fn):
                    # 原生异步闭包（graph.ainvoke）：直接在事件循环上 await，真正并发
                    await asyncio.wait_for(fn(*args, **kwargs), timeout=self._task_timeout)
                else:
                    # 同步业务闭包（graph.invoke）：放到线程池执行，避免阻塞事件循环
                    await asyncio.wait_for(
                        asyncio.to_thread(fn, *args, **kwargs), timeout=self._task_timeout
                    )
                query_processed_total.inc()
            except asyncio.TimeoutError:
                logger.error(f"async_query_executor: task timeout ({self._task_timeout}s): {getattr(fn, '__name__', fn)}")
            except Exception:
                logger.exception(f"async_query_executor: unhandled error in {getattr(fn, '__name__', fn)}")
            finally:
                self._mark_active(-1)
                self._mark_pending(-1)

    def active_count(self) -> int:
        with self._counter_lock:
            return self._active

    def pending_count(self) -> int:
        with self._counter_lock:
            return self._pending


# ========== 模块级单例 ==========

_executor: Optional[AsyncQueryExecutor] = None


def start_async_executor(settings: Optional[Settings] = None) -> AsyncQueryExecutor:
    global _executor
    if _executor is None:
        _executor = AsyncQueryExecutor(settings)
    _executor.start()
    return _executor


def get_async_executor() -> Optional[AsyncQueryExecutor]:
    return _executor


def shutdown_async_executor(wait: bool = True) -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None
