"""韧性模式：熔断器（Circuit Breaker）

为外部 API 调用提供熔断保护，防止级联故障。
实现标准三态机：CLOSED → OPEN → HALF_OPEN → CLOSED

参考：《Release It!》Michael Nygard, Chapter 5
"""
import logging
import threading
import time
from enum import Enum
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """熔断器状态"""
    CLOSED = "closed"          # 正常：请求正常通过
    OPEN = "open"              # 熔断：快速失败，不发起远程调用
    HALF_OPEN = "half_open"    # 探测：允许一个请求通过以测试服务恢复


class CircuitBreaker:
    """熔断器

    状态转换：
        CLOSED ──(failures ≥ threshold)──▶ OPEN
        OPEN ──(timeout elapsed)──▶ HALF_OPEN
        HALF_OPEN ──(success)──▶ CLOSED
        HALF_OPEN ──(failure)──▶ OPEN

    线程安全：所有状态变更通过 threading.Lock 保护。
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ):
        """
        Args:
            name: 熔断器名称（用于日志）
            failure_threshold: 连续失败 N 次后熔断
            recovery_timeout: 熔断后等待多少秒进入 HALF_OPEN
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self._lock = threading.Lock()

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """通过熔断器调用函数

        Args:
            func: 被保护的函数
            *args, **kwargs: 传递给 func 的参数

        Returns:
            func 的返回值

        Raises:
            CircuitBreakerOpenError: 熔断器处于 OPEN 状态
            其他异常: func 抛出的原始异常
        """
        with self._lock:
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    # 超时 → 进入探测
                    self.state = CircuitState.HALF_OPEN
                    logger.info(
                        f"[CB:{self.name}] OPEN → HALF_OPEN (recovery timeout reached)"
                    )
                else:
                    # 仍在熔断 → 快速失败
                    raise CircuitBreakerOpenError(
                        f"[CB:{self.name}] Circuit is OPEN. "
                        f"Fast-failing without calling {func.__name__}"
                    )

            # HALF_OPEN 或 CLOSED → 允许调用
            # （在锁外执行，避免长时间持锁）

        try:
            result = func(*args, **kwargs)
        except Exception as e:
            self._on_failure()
            raise e

        self._on_success()
        return result

    def _on_success(self) -> None:
        """调用成功 → 重置状态"""
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                logger.info(f"[CB:{self.name}] HALF_OPEN → CLOSED (probe succeeded)")
            self.state = CircuitState.CLOSED
            self.failure_count = 0

    def _on_failure(self) -> None:
        """调用失败 → 更新失败计数"""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if (
                self.state == CircuitState.HALF_OPEN
                or self.failure_count >= self.failure_threshold
            ):
                if self.state != CircuitState.OPEN:
                    logger.warning(
                        f"[CB:{self.name}] → OPEN "
                        f"(failures={self.failure_count}/{self.failure_threshold})"
                    )
                self.state = CircuitState.OPEN

    @property
    def status(self) -> dict:
        """导出状态（供 /health 端点使用）"""
        with self._lock:
            return {
                "name": self.name,
                "state": self.state.value,
                "failure_count": self.failure_count,
                "threshold": self.failure_threshold,
            }


class CircuitBreakerOpenError(Exception):
    """熔断器 OPEN 状态时抛出的异常（快速失败）"""
    pass
