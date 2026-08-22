"""统一有界查询执行器

单 bot 多用户并发查询的统一处理池。所有平台（飞书 / Telegram / Discord）的
事件处理统一派发到此，实现：

- **有界队列**：BoundedSemaphore 限制队列容量，打满后丢弃新请求，防止突发时
  内存/延迟无界恶化（过载策略：丢弃 + 平台层回「系统繁忙」提示）
- **每用户限流**：令牌桶（capacity=3, refill=0.5/s），允许短突发但持续刷屏仍被限
- **不阻塞事件循环**：调用方 submit() 后立即返回，Webhook loop / WS 线程 /
  网关线程只做派发
- **可观测**：队列深度、worker 占用、丢弃计数、处理计数、等待耗时指标

并发模型（Producer-Consumer）：
- Producer: WS 线程 / webhook loop / 网关线程 → submit() 立即返回
- Consumer: ThreadPoolExecutor(max_workers=QUERY_MAX_WORKERS) 执行业务逻辑

核心事实：查询管线是 IO 密集的同步代码（LangGraph invoke → 2-3 次 LLM 调用，
1-30s），线程池是天然匹配；并发上限受 LLM rate limit 制约。
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from enum import Enum
from typing import Callable, Optional

from app.core.config import Settings
from app.core.metrics import (
    query_queue_depth,
    query_workers_busy,
    query_dropped_total,
    query_processed_total,
    query_queue_wait_seconds,
)
from app.core.resilience import TokenBucket

logger = logging.getLogger(__name__)


class QuerySubmitStatus(Enum):
    """submit() 的返回值，调用方据此决定是否回繁忙提示"""

    ACCEPTED = "accepted"
    QUEUE_FULL = "queue_full"
    RATE_LIMITED = "rate_limited"


# ========== 全局状态（由 _init 初始化）==========

_executor: Optional[ThreadPoolExecutor] = None
_semaphore: Optional[threading.BoundedSemaphore] = None

_max_workers = 10
_max_queue = 50
_queue_timeout = 0.5
_rate_limit_seconds = 2.0
_rate_burst = 3
_rate_refill = 0.5

# 已接受但未完成的任务数（排队中 + 执行中）
_pending_lock = threading.Lock()
_pending = 0

# 正在执行的 worker 数
_active_lock = threading.Lock()
_active = 0

# 每用户限流：user_id → TokenBucket（令牌桶，支持短突发 + 平滑限流）
_rate_lock = threading.Lock()
_rate_buckets: dict[str, TokenBucket] = {}
_MAX_RATE_ENTRIES = 5000

# 执行器委托：QUERY_EXECUTOR_MODE=async 时 main.py 注册异步执行器的 submit，
# 平台层无感切换（三平台仍 import 本模块的 submit）。None 时走同步线程池。
_submit_delegate: Optional[Callable[..., "QuerySubmitStatus"]] = None


# ========== 初始化 / 重建 ==========

def _init() -> None:
    """从 Settings 读取配置并构建执行器（模块加载时调用一次）"""
    global _executor, _semaphore, _max_workers, _max_queue, _queue_timeout, _rate_limit_seconds
    global _rate_burst, _rate_refill
    settings = Settings()  # type: ignore[call-arg]
    _max_workers = max(1, settings.QUERY_MAX_WORKERS)
    _max_queue = max(1, settings.QUERY_MAX_QUEUE)
    _queue_timeout = max(0.0, settings.QUERY_QUEUE_TIMEOUT_SECONDS)
    _rate_limit_seconds = max(0.0, settings.QUERY_RATE_LIMIT_SECONDS)
    _rate_burst = max(1, settings.QUERY_RATE_BURST)
    _rate_refill = max(0.01, settings.QUERY_RATE_REFILL)
    _executor = ThreadPoolExecutor(max_workers=_max_workers, thread_name_prefix="query-worker")
    _semaphore = threading.BoundedSemaphore(_max_queue)
    logger.info(
        f"QueryExecutor initialized: workers={_max_workers}, "
        f"max_queue={_max_queue}, queue_timeout={_queue_timeout}s, "
        f"rate_limit={_rate_limit_seconds}s (token bucket: "
        f"burst={_rate_burst}, refill={_rate_refill}/s)"
    )


def _reconfigure(
    max_workers: int = 10,
    max_queue: int = 50,
    queue_timeout: float = 0.5,
    rate_limit_seconds: float = 2.0,
    rate_burst: int = 3,
    rate_refill: float = 0.5,
) -> None:
    """重建执行器（仅供测试使用）"""
    global _executor, _semaphore, _max_workers, _max_queue, _queue_timeout, _rate_limit_seconds
    global _rate_burst, _rate_refill
    shutdown(wait=False)
    _max_workers = max(1, int(max_workers))
    _max_queue = max(1, int(max_queue))
    _queue_timeout = max(0.0, float(queue_timeout))
    _rate_limit_seconds = max(0.0, float(rate_limit_seconds))
    _rate_burst = max(1, int(rate_burst))
    _rate_refill = max(0.01, float(rate_refill))
    _executor = ThreadPoolExecutor(max_workers=_max_workers, thread_name_prefix="query-worker")
    _semaphore = threading.BoundedSemaphore(_max_queue)
    with _rate_lock:
        _rate_buckets.clear()  # 重建即视为新池子，清除跨测试/跨配置残留的限流状态


_init()


# ========== 内部计数 / 指标 ==========

def _mark_pending(delta: int) -> None:
    global _pending
    with _pending_lock:
        _pending += delta
        query_queue_depth.set(_pending)


def _mark_active(delta: int) -> None:
    global _active
    with _active_lock:
        _active += delta
        query_workers_busy.set(max(0, _active))


# ========== 每用户限流 ==========

def _allow_user(user_id: Optional[str]) -> bool:
    """检查 user_id 是否允许提交（令牌桶：桶空返回 False）

    相比固定窗口（N 秒内 1 条），令牌桶允许用户短时连问 burst 条合理问题，
    持续刷屏仍被限（令牌耗尽需等待回填）。QUERY_RATE_LIMIT_SECONDS<=0 时关闭限流。
    """
    if not user_id or _rate_limit_seconds <= 0:
        return True
    with _rate_lock:
        bucket = _rate_buckets.get(user_id)
        if bucket is None:
            bucket = TokenBucket(capacity=_rate_burst, refill_rate=_rate_refill)
            _rate_buckets[user_id] = bucket
            if len(_rate_buckets) > _MAX_RATE_ENTRIES:
                _prune_rate_buckets()
    return bucket.allow()


def _prune_rate_buckets() -> None:
    """清理已回满（长期未活动）的令牌桶，防止 dict 无界增长（需持 _rate_lock）"""
    for uid in list(_rate_buckets):
        bucket = _rate_buckets[uid]
        # 桶已回满即视为空闲用户，可安全丢弃（丢弃后重建等价于满桶）
        with bucket._lock:
            bucket._refill()
            if bucket.tokens >= bucket.capacity:
                del _rate_buckets[uid]


# ========== worker 执行 ==========

def _run(fn: Callable, args: tuple, kwargs: dict, t_submit: float) -> None:
    """worker 中执行：记录等待耗时，异常隔离"""
    wait = time.monotonic() - t_submit
    query_queue_wait_seconds.observe(wait)

    _mark_active(1)
    try:
        fn(*args, **kwargs)
        query_processed_total.inc()
    except Exception:
        logger.exception(f"Unhandled error in query worker: {getattr(fn, '__name__', fn)}")
    finally:
        _mark_active(-1)


def _on_done(semaphore: threading.BoundedSemaphore, future: Future) -> None:
    """任务完成：释放队列信号量、更新深度

    注意捕获 submit 时的 semaphore 实例，避免并发重建后释放错信号量。
    """
    semaphore.release()
    _mark_pending(-1)


# ========== 对外 API ==========

def submit(
    fn: Callable,
    *args,
    user_id: Optional[str] = None,
    **kwargs,
) -> QuerySubmitStatus:
    """将处理任务派发到查询池（fire-and-forget）

    Args:
        fn: 要执行的函数
        user_id: 发送者 ID（用于每用户限流，可为 None 表示不限流）
        *args / **kwargs: 传给 fn 的参数

    Returns:
        ACCEPTED   — 已接受，worker 异步执行
        QUEUE_FULL — 队列打满，已丢弃（调用方应回「系统繁忙」）
        RATE_LIMITED — 用户触发限流，已丢弃（调用方应回「请勿刷屏」）
    """
    # 异步模式：委托给 async 执行器（限流在其内部复用 _allow_user，语义一致）
    delegate = _submit_delegate
    if delegate is not None:
        return delegate(fn, *args, user_id=user_id, **kwargs)

    executor = _executor
    semaphore = _semaphore
    if executor is None or semaphore is None:
        return QuerySubmitStatus.QUEUE_FULL

    # 每用户限流（先于入队，避免排队占位）
    if not _allow_user(user_id):
        query_dropped_total.labels(reason="rate_limited").inc()
        logger.debug(f"query_executor: rate limited user_id={user_id}")
        return QuerySubmitStatus.RATE_LIMITED

    # 有界队列：等待空位，超时则丢弃
    if not semaphore.acquire(timeout=_queue_timeout):
        query_dropped_total.labels(reason="queue_full").inc()
        logger.warning(
            f"query_executor: queue full, dropping task {getattr(fn, '__name__', fn)}"
        )
        return QuerySubmitStatus.QUEUE_FULL

    t_submit = time.monotonic()
    _mark_pending(1)

    def _wrapped() -> None:
        _run(fn, args, kwargs, t_submit)

    try:
        future = executor.submit(_wrapped)
    except Exception as e:
        # executor 已关闭等：释放占位
        logger.error(f"query_executor submit failed: {e}")
        _mark_pending(-1)
        semaphore.release()
        return QuerySubmitStatus.QUEUE_FULL

    future.add_done_callback(lambda f, sem=semaphore: _on_done(sem, f))
    return QuerySubmitStatus.ACCEPTED


def set_submit_delegate(delegate: Optional[Callable[..., QuerySubmitStatus]]) -> None:
    """注册/注销异步执行器的 submit 委托（main.py lifespan 按 QUERY_EXECUTOR_MODE 调用）。

    注册后本模块 submit() 转发到异步执行器；传 None 恢复同步线程池路径。
    平台层始终 import 本模块的 submit，切换对其透明。
    """
    global _submit_delegate
    _submit_delegate = delegate


def shutdown(wait: bool = True) -> None:
    """优雅关闭查询池（由 main.py lifespan 在应用退出时调用）"""
    global _executor
    if _executor is None:
        return
    executor, _executor = _executor, None  # 先置空，阻止新提交
    executor.shutdown(wait=wait, cancel_futures=False)
    logger.info("QueryExecutor shut down")


def active_count() -> int:
    """当前正在执行的 worker 数（供指标 / 健康检查）"""
    with _active_lock:
        return _active


def pending_count() -> int:
    """当前待处理任务数（排队中 + 执行中）"""
    with _pending_lock:
        return _pending
