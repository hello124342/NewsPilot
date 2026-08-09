"""Circuit Breaker 单元测试

测试熔断器的三态转换和线程安全性。
"""
import time
import pytest

from app.core.resilience import CircuitBreaker, CircuitBreakerOpenError, CircuitState


class TestCircuitBreakerStates:
    """熔断器状态机测试"""

    def test_initial_state_closed(self):
        """初始状态为 CLOSED"""
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_successful_call_keeps_closed(self):
        """成功调用保持 CLOSED"""
        cb = CircuitBreaker("test")
        result = cb.call(lambda x: x * 2, 21)
        assert result == 42
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_failure_increments_count(self):
        """失败调用增加计数"""
        cb = CircuitBreaker("test", failure_threshold=3)

        for _ in range(2):
            with pytest.raises(ValueError, match="test error"):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("test error")))

        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 2

    def test_threshold_reached_opens_circuit(self):
        """达到阈值时熔断"""
        cb = CircuitBreaker("test", failure_threshold=3)

        for _ in range(3):
            with pytest.raises(ValueError, match="test error"):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("test error")))

        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 3

    def test_open_circuit_fast_fails(self):
        """熔断状态快速失败"""
        cb = CircuitBreaker("test", failure_threshold=1,
                            recovery_timeout=999.0)

        # Trip the breaker
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("bang")))

        # Now should fast-fail
        call_count = [0]

        def should_not_be_called():
            call_count[0] += 1
            return "should not reach"

        with pytest.raises(CircuitBreakerOpenError):
            cb.call(should_not_be_called)

        assert call_count[0] == 0  # Function was never called

    def test_open_transitions_to_half_open_after_timeout(self):
        """超时后 OPEN → HALF_OPEN"""
        cb = CircuitBreaker("test", failure_threshold=1,
                            recovery_timeout=0.01)  # 10ms

        # Trip
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("bang")))

        assert cb.state == CircuitState.OPEN

        # Wait for timeout
        time.sleep(0.02)

        # Next call should be HALF_OPEN, and if successful, CLOSED
        result = cb.call(lambda x: x, 42)
        assert result == 42
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_goes_back_to_open(self):
        """HALF_OPEN 失败 → 回到 OPEN"""
        cb = CircuitBreaker("test", failure_threshold=1,
                            recovery_timeout=0.01)

        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("bang")))
        assert cb.state == CircuitState.OPEN

        time.sleep(0.02)

        # HALF_OPEN → failure → OPEN again
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("bang again")))
        assert cb.state == CircuitState.OPEN

    def test_status_export(self):
        """状态导出"""
        cb = CircuitBreaker("my-cb", failure_threshold=5)
        status = cb.status
        assert status["name"] == "my-cb"
        assert status["state"] == "closed"
        assert status["failure_count"] == 0
        assert status["threshold"] == 5
