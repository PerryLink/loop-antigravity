"""CircuitBreaker 单元测试。

验证 CLOSED/OPEN/HALF_OPEN 状态机的所有状态转换路径。
"""

from __future__ import annotations

import time

import pytest

from loop_antigravity.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerSnapshot,
    CircuitState,
    FailureCategory,
    classify_http_error,
    classify_exception,
)


class TestCircuitBreakerInit:
    """初始化测试。"""

    def test_initial_state_closed(self, circuit_breaker: CircuitBreaker) -> None:
        """新创建的 CircuitBreaker 应为 CLOSED 状态。"""
        assert circuit_breaker.is_closed
        assert not circuit_breaker.is_open
        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.consecutive_failures == 0
        assert circuit_breaker.total_trips == 0

    def test_for_mode_returns_correct_thresholds(self) -> None:
        """for_mode 应返回对应模式的正确阈值。"""
        cb_safe = CircuitBreaker.for_mode("safe")
        assert cb_safe.failure_threshold == 2
        assert cb_safe.cooldown_seconds == 120.0

        cb_auto = CircuitBreaker.for_mode("auto")
        assert cb_auto.failure_threshold == 5
        assert cb_auto.cooldown_seconds == 30.0

    def test_guard_allows_when_closed(self, circuit_breaker: CircuitBreaker) -> None:
        """CLOSED 状态下 guard() 应允许所有请求。"""
        result = circuit_breaker.guard()
        assert not result.blocked


class TestClosedToOpen:
    """CLOSED -> OPEN 状态转换测试。"""

    def test_opens_after_threshold_failures(
        self, circuit_breaker: CircuitBreaker, cb_config: CircuitBreakerConfig
    ) -> None:
        """达到 failure_threshold 次连续失败后应转换到 OPEN。"""
        for i in range(cb_config.failure_threshold - 1):
            circuit_breaker.report_failure(
                FailureCategory.SERVER_ERROR, f"失败 #{i}"
            )
        assert circuit_breaker.is_closed
        assert circuit_breaker.consecutive_failures == cb_config.failure_threshold - 1

        circuit_breaker.report_failure(
            FailureCategory.SERVER_ERROR, "最终失败"
        )
        assert circuit_breaker.is_open
        assert circuit_breaker.total_trips == 1

    def test_auth_error_does_not_count(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """AUTH_ERROR 类失败不应增加连续失败计数。"""
        for _ in range(10):
            circuit_breaker.report_failure(
                FailureCategory.AUTH_ERROR, "认证失败"
            )
        assert circuit_breaker.is_closed
        assert circuit_breaker.consecutive_failures == 0

    def test_success_resets_failures(
        self, circuit_breaker: CircuitBreaker, cb_config: CircuitBreakerConfig
    ) -> None:
        """report_success 在 CLOSED 中应重置连续失败计数。"""
        circuit_breaker.report_failure(FailureCategory.TIMEOUT, "超时")
        circuit_breaker.report_failure(FailureCategory.TIMEOUT, "超时")
        assert circuit_breaker.consecutive_failures == 2

        circuit_breaker.report_success()
        assert circuit_breaker.consecutive_failures == 0


class TestOpenState:
    """OPEN 状态行为测试。"""

    def test_guard_blocks_in_open(
        self, open_circuit_breaker: CircuitBreaker
    ) -> None:
        """OPEN 状态下 guard() 应阻止请求。"""
        result = open_circuit_breaker.guard()
        assert result.blocked
        assert result.cooldown_remaining_seconds > 0

    def test_fast_fail_counter(
        self, open_circuit_breaker: CircuitBreaker
    ) -> None:
        """被 guard() 阻止的请求应计入 fast_fail 计数器。"""
        initial = open_circuit_breaker.total_fast_fails_saved
        open_circuit_breaker.guard()
        open_circuit_breaker.guard()
        assert open_circuit_breaker.total_fast_fails_saved == initial + 2


class TestHalfOpenTransitions:
    """HALF_OPEN 状态转换测试。"""

    def test_opens_to_half_open_after_cooldown(
        self, open_circuit_breaker: CircuitBreaker, cb_config: CircuitBreakerConfig
    ) -> None:
        """冷却结束后 guard() 应自动转换到 HALF_OPEN。"""
        time.sleep(cb_config.cooldown_seconds + 0.1)
        result = open_circuit_breaker.guard()
        assert not result.blocked
        assert open_circuit_breaker.state == CircuitState.HALF_OPEN

    def test_half_open_success_goes_to_closed(
        self, open_circuit_breaker: CircuitBreaker, cb_config: CircuitBreakerConfig
    ) -> None:
        """HALF_OPEN 中探测成功后应回到 CLOSED。"""
        time.sleep(cb_config.cooldown_seconds + 0.1)
        open_circuit_breaker.guard()  # 进入 HALF_OPEN
        assert open_circuit_breaker.state == CircuitState.HALF_OPEN

        open_circuit_breaker.report_success()
        assert open_circuit_breaker.is_closed
        assert open_circuit_breaker.consecutive_failures == 0

    def test_half_open_failure_goes_back_to_open(
        self, open_circuit_breaker: CircuitBreaker, cb_config: CircuitBreakerConfig
    ) -> None:
        """HALF_OPEN 中探测失败应回到 OPEN (含退避乘数)。"""
        time.sleep(cb_config.cooldown_seconds + 0.1)
        open_circuit_breaker.guard()  # 进入 HALF_OPEN
        assert open_circuit_breaker.state == CircuitState.HALF_OPEN

        open_circuit_breaker.report_failure(
            FailureCategory.SERVICE_UNAVAILABLE, "探测失败"
        )
        assert open_circuit_breaker.is_open
        assert open_circuit_breaker.total_trips == 2


class TestResetAndForce:
    """reset 与 force_open 操作测试。"""

    def test_reset_to_closed(
        self, open_circuit_breaker: CircuitBreaker
    ) -> None:
        """reset() 应将熔断器恢复至 CLOSED。"""
        assert open_circuit_breaker.is_open
        open_circuit_breaker.reset()
        assert open_circuit_breaker.is_closed
        assert open_circuit_breaker.consecutive_failures == 0

    def test_force_open(self, circuit_breaker: CircuitBreaker) -> None:
        """force_open() 应强制进入 OPEN。"""
        assert circuit_breaker.is_closed
        circuit_breaker.force_open(reason="计费上限")
        assert circuit_breaker.is_open


class TestSnapshot:
    """快照持久化测试。"""

    def test_snapshot_roundtrip(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """snapshot -> restore_from_snapshot 应还原状态。"""
        circuit_breaker.report_failure(
            FailureCategory.SERVER_ERROR, "测试"
        )
        snap = circuit_breaker.snapshot()
        assert isinstance(snap, CircuitBreakerSnapshot)
        assert snap.consecutive_failures == 1

        cb2 = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=5, cooldown_seconds=30,
        ))
        cb2.restore_from_snapshot(snap)
        assert cb2.consecutive_failures == 1
        assert cb2.state == CircuitState.CLOSED

    def test_snapshot_includes_open_state(
        self, open_circuit_breaker: CircuitBreaker
    ) -> None:
        """OPEN 状态的快照应包含完整时间戳。"""
        snap = open_circuit_breaker.snapshot()
        assert snap.state == "OPEN"
        assert snap.opened_at is not None
        assert snap.last_failure_reason is not None
        assert snap.total_trips == 1


class TestClassifyHttpError:
    """classify_http_error() 测试。"""

    def test_classify_429_rate_limit(self) -> None:
        """429 状态码应归类为 RATE_LIMIT。"""
        assert classify_http_error(429) == FailureCategory.RATE_LIMIT

    def test_classify_503_service_unavailable(self) -> None:
        """503 状态码应归类为 SERVICE_UNAVAILABLE。"""
        assert classify_http_error(503) == FailureCategory.SERVICE_UNAVAILABLE

    def test_classify_403_auth_error(self) -> None:
        """403 状态码应归类为 AUTH_ERROR。"""
        assert classify_http_error(403) == FailureCategory.AUTH_ERROR

    def test_classify_5xx_server_error(self) -> None:
        """5xx 状态码（非 503）应归类为 SERVER_ERROR。"""
        assert classify_http_error(500) == FailureCategory.SERVER_ERROR
        assert classify_http_error(502) == FailureCategory.SERVER_ERROR
        assert classify_http_error(504) == FailureCategory.SERVER_ERROR

    def test_classify_other_unknown(self) -> None:
        """非 429/503/403/5xx 的状态码应归类为 UNKNOWN。"""
        assert classify_http_error(200) == FailureCategory.UNKNOWN
        assert classify_http_error(400) == FailureCategory.UNKNOWN
        assert classify_http_error(404) == FailureCategory.UNKNOWN


class TestClassifyException:
    """classify_exception() 测试。"""

    def test_classify_quota_exhausted(self) -> None:
        """QuotaExhausted 异常应归类为 RATE_LIMIT 且计为触发。"""
        class QuotaExhaustedExc(Exception):
            pass
        category, _breaker_relevant = classify_exception(
            QuotaExhaustedExc("daily limit")
        )
        assert category == FailureCategory.RATE_LIMIT

    def test_classify_circuit_open(self) -> None:
        """CircuitOpen 异常应归类为 SERVICE_UNAVAILABLE。"""
        class CircuitOpenExc(Exception):
            pass
        category, _breaker_relevant = classify_exception(
            CircuitOpenExc("breaker is open")
        )
        assert category == FailureCategory.SERVICE_UNAVAILABLE

    def test_classify_timeout(self) -> None:
        """Timeout 异常应归类为 TIMEOUT。"""
        class TimeoutExc(Exception):
            pass
        category, _breaker_relevant = classify_exception(
            TimeoutExc("operation timed out")
        )
        assert category == FailureCategory.TIMEOUT

    def test_classify_stream_parse(self) -> None:
        """StreamParse 异常应归类为 STREAM_PARSE_ERROR。"""
        class StreamParseExc(Exception):
            pass
        category, _breaker_relevant = classify_exception(
            StreamParseExc("parse error")
        )
        assert category == FailureCategory.STREAM_PARSE_ERROR

    def test_classify_auth_error(self) -> None:
        """Auth 异常应归类为 AUTH_ERROR 且不计为触发。"""
        class AuthExc(Exception):
            pass
        category, breaker_relevant = classify_exception(
            AuthExc("auth 403 forbidden")
        )
        assert category == FailureCategory.AUTH_ERROR
        assert breaker_relevant is False

    def test_classify_subprocess_crash(self) -> None:
        """Subprocess 异常应归类为 SUBPROCESS_CRASH。"""
        class SubprocessExc(Exception):
            pass
        category, _breaker_relevant = classify_exception(
            SubprocessExc("signal 9")
        )
        assert category == FailureCategory.SUBPROCESS_CRASH

    def test_classify_not_installed(self) -> None:
        """NotInstalled 异常应归类为 UNKNOWN 且不计为触发。"""
        class NotInstalledExc(Exception):
            pass
        category, breaker_relevant = classify_exception(
            NotInstalledExc("not installed")
        )
        assert category == FailureCategory.UNKNOWN
        assert breaker_relevant is False

    def test_classify_generic_unknown(self) -> None:
        """未知异常应归类为 UNKNOWN 且计为触发。"""
        class GenericExc(Exception):
            pass
        category, breaker_relevant = classify_exception(GenericExc("weird"))
        assert category == FailureCategory.UNKNOWN
        assert breaker_relevant is True


class TestNoneProperties:
    """None 属性路径测试（熔断器未经历 OPEN 时的属性访问）。"""

    def test_opened_at_none_when_never_opened(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """从未 OPEN 过的熔断器 opened_at 应返回 None。"""
        assert circuit_breaker.opened_at is None

    def test_last_failure_at_none_when_no_failure(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """从未发生过失败时 last_failure_at 应返回 None。"""
        assert circuit_breaker.last_failure_at is None

    def test_cooldown_remaining_zero_when_closed(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """CLOSED 状态下 cooldown_remaining_seconds 应返回 0。"""
        assert circuit_breaker.cooldown_remaining_seconds() == 0.0

    def test_last_failure_reason_property(
        self, open_circuit_breaker: CircuitBreaker
    ) -> None:
        """last_failure_reason 属性应返回最后一次失败的原因。"""
        assert open_circuit_breaker.last_failure_reason is not None

    def test_cooldown_remaining_positive_when_open(
        self, open_circuit_breaker: CircuitBreaker
    ) -> None:
        """OPEN 状态下 cooldown_remaining_seconds 应返回正值。"""
        remaining = open_circuit_breaker.cooldown_remaining_seconds()
        assert remaining > 0.0


class TestUnknownCategoryNotCounted:
    """UNKNOWN 类别不计入触发计数的测试。"""

    def test_unknown_category_does_not_increment(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """UNKNOWN 失败类别不应增加连续失败计数。"""
        initial = circuit_breaker.consecutive_failures
        circuit_breaker.report_failure(
            FailureCategory.UNKNOWN, "非触发类错误"
        )
        assert circuit_breaker.consecutive_failures == initial
        assert circuit_breaker.is_closed


class TestHalfOpenProbeExhausted:
    """HALF_OPEN 探测次数耗尽测试。"""

    def test_half_open_blocks_when_probe_limit_reached(
        self, circuit_breaker: CircuitBreaker, cb_config: CircuitBreakerConfig
    ) -> None:
        """HALF_OPEN 中探测次数耗尽后 guard() 应阻止请求。"""
        cb = circuit_breaker
        # 先用 report_failure 把 breaker 推到 OPEN
        for _ in range(cb_config.failure_threshold):
            cb.report_failure(FailureCategory.SERVER_ERROR, "推入 OPEN")
        assert cb.is_open

        # 等待冷却结束后通过 guard() 进入 HALF_OPEN (第1次: OPEN→HALF_OPEN)
        time.sleep(cb_config.cooldown_seconds + 0.1)
        result1 = cb.guard()
        assert not result1.blocked
        assert cb.state == CircuitState.HALF_OPEN

        # 第2次 guard(): 消耗探测配额 (half_open_max_requests=1 → 0)
        result2 = cb.guard()
        assert not result2.blocked

        # 第3次 guard(): 探测配额已耗尽 → 应被阻止
        result3 = cb.guard()
        assert result3.blocked
        assert "probe limit reached" in result3.reason
