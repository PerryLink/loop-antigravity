
"""
CircuitBreaker -- CLOSED/OPEN/HALF_OPEN 状态机。

保护系统免受级联 API 故障的影响，防止在 Gemini API 错误时持续重试
从而浪费配额。当 Gemini API 返回错误时，立即重试是反生产性的 --
会消耗配额（5 小时滚动重置窗口）并延迟恢复。

状态转换:
    CLOSED --(连续 N 次失败)--> OPEN --(冷却 T 秒)--> HALF_OPEN
    HALF_OPEN --(探测成功)--> CLOSED
    HALF_OPEN --(探测失败)--> OPEN (冷却重置，可选加倍)

按信任级别配置:
    L1 (safe):           failure_threshold=2,  cooldown=120s
    L2 (auto/默认):      failure_threshold=5,  cooldown=30s
    L3 (unsafe):         failure_threshold=20, cooldown=5s
    L1+ (collaborative): failure_threshold=3,  cooldown=60s
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ============================================================================
# 数据类型
# ============================================================================


class CircuitState(Enum):
    """熔断器状态枚举。"""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class FailureCategory(Enum):
    """失败类型分类，用于计数和日志记录。"""
    RATE_LIMIT = "RATE_LIMIT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    SERVER_ERROR = "SERVER_ERROR"
    TIMEOUT = "TIMEOUT"
    STREAM_PARSE_ERROR = "STREAM_PARSE_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    SUBPROCESS_CRASH = "SUBPROCESS_CRASH"
    UNKNOWN = "UNKNOWN"


FAILURE_TRIGGERING_CATEGORIES = frozenset({
    FailureCategory.RATE_LIMIT,
    FailureCategory.SERVICE_UNAVAILABLE,
    FailureCategory.SERVER_ERROR,
    FailureCategory.TIMEOUT,
    FailureCategory.STREAM_PARSE_ERROR,
    FailureCategory.SUBPROCESS_CRASH,
})

AUTH_FAILURE_CATEGORIES = frozenset({FailureCategory.AUTH_ERROR})


def classify_http_error(status_code: int) -> FailureCategory:
    if status_code == 429:
        return FailureCategory.RATE_LIMIT
    if status_code == 503:
        return FailureCategory.SERVICE_UNAVAILABLE
    if status_code == 403:
        return FailureCategory.AUTH_ERROR
    if 500 <= status_code < 600:
        return FailureCategory.SERVER_ERROR
    return FailureCategory.UNKNOWN


def classify_exception(exc: Exception) -> tuple:
    exc_name = type(exc).__name__
    exc_msg = str(exc).lower()
    if "QuotaExhausted" in exc_name or "ResourceExhausted" in exc_name:
        return FailureCategory.RATE_LIMIT, True
    if "CircuitOpen" in exc_name:
        return FailureCategory.SERVICE_UNAVAILABLE, True
    if "Timeout" in exc_name or "timeout" in exc_msg:
        return FailureCategory.TIMEOUT, True
    if "StreamParse" in exc_name or "parse" in exc_msg:
        return FailureCategory.STREAM_PARSE_ERROR, True
    if "Auth" in exc_name or "auth" in exc_msg or "403" in exc_msg:
        return FailureCategory.AUTH_ERROR, False
    if "Subprocess" in exc_name or "signal" in exc_msg:
        return FailureCategory.SUBPROCESS_CRASH, True
    if "NotInstalled" in exc_name:
        return FailureCategory.UNKNOWN, False
    return FailureCategory.UNKNOWN, True


# ============================================================================
# 配置与快照数据类
# ============================================================================


@dataclass
class CircuitBreakerConfig:
    """熔断器运行时配置。

    Attributes:
        failure_threshold: 连续失败多少次后触发 OPEN。
        cooldown_seconds: OPEN 后等待多少秒再尝试 HALF_OPEN。
        max_cooldown_seconds: 冷却时间上限（退避乘数达到后封顶）。
        cooldown_backoff_multiplier: 重复触发的冷却退避乘数。
        probe_max_retries: HALF_OPEN 最多探测次数。
        half_open_max_requests: HALF_OPEN 状态下允许通过的最大请求数。
        log_path: 状态转换日志文件路径。
    """
    failure_threshold: int = 5
    cooldown_seconds: float = 30.0
    max_cooldown_seconds: float = 600.0
    cooldown_backoff_multiplier: float = 2.0
    probe_max_retries: int = 3
    half_open_max_requests: int = 1
    log_path: str = ".claude/loop-antigravity/circuit_breaker.log"


@dataclass
class CircuitBreakerSnapshot:
    """不可变快照，用于 state.json 持久化。

    Attributes:
        state: 当前状态字符串 "CLOSED"/"OPEN"/"HALF_OPEN"。
        consecutive_failures: 连续失败计数。
        failure_threshold: 触发阈值。
        cooldown_seconds: 冷却秒数。
        opened_at: OPEN 状态开始的 ISO 时间戳。
        last_failure_at: 最后一次失败的 ISO 时间戳。
        last_failure_reason: 最后一次失败的原因。
        last_failure_category: 最后一次失败的类别字符串。
        last_probe_at: 最后一次探测的 ISO 时间戳。
        total_trips: 历史 OPEN 总次数。
        total_fast_fails_saved: 被快速失败拦截的请求数。
        current_cooldown_multiplier: 当前冷却乘数。
    """
    state: str
    consecutive_failures: int
    failure_threshold: int
    cooldown_seconds: float
    opened_at: Optional[str]
    last_failure_at: Optional[str]
    last_failure_reason: Optional[str]
    last_failure_category: Optional[str]
    last_probe_at: Optional[str]
    total_trips: int
    total_fast_fails_saved: int
    current_cooldown_multiplier: float


# ============================================================================
# 时间戳工具函数
# ============================================================================


def _ts_to_iso(ts: float) -> str:
    """将 Unix 时间戳转换为 ISO 8601 字符串。

    Args:
        ts: Unix 时间戳（秒）。

    Returns:
        ISO 8601 格式的时间字符串。
    """
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _iso_to_ts(iso: str) -> float:
    """将 ISO 8601 字符串转换为 Unix 时间戳。

    Args:
        iso: ISO 8601 格式的时间字符串。

    Returns:
        Unix 时间戳（秒）。
    """
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.timestamp()


# ============================================================================
# CircuitBreaker 类
# ============================================================================


class CircuitBreaker:
    """
    CLOSED/OPEN/HALF_OPEN 状态机，用于 API 失败保护。

    线程安全。所有状态变更受可重入锁保护。
    设计用于与 AgyClient 集成 -- AgyClient 在每次 invoke() 之前
    调用 guard()，之后调用 report_success()/report_failure()。

    Attributes:
        state: 当前熔断器状态（CircuitState 枚举）。
        is_closed: 熔断器是否处于 CLOSED 状态。
        is_open: 熔断器是否处于 OPEN 状态。
    """

    @dataclass
    class GuardResult:
        """guard() 方法的返回值。"""
        blocked: bool
        reason: str = ""
        cooldown_remaining_seconds: float = 0.0

    def __init__(self, config: CircuitBreakerConfig) -> None:
        """初始化熔断器。

        Args:
            config: CircuitBreakerConfig 实例。
        """
        self._config = config
        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._opened_at: Optional[float] = None
        self._last_failure_at: Optional[float] = None
        self._last_failure_reason: Optional[str] = None
        self._last_failure_category: Optional[FailureCategory] = None
        self._last_probe_at: Optional[float] = None
        self._total_trips: int = 0
        self._total_fast_fails_saved: int = 0
        self._cooldown_multiplier: float = 1.0
        self._half_open_requests_remaining: int = 0
        self._lock = threading.RLock()

    @classmethod
    def for_mode(cls, mode: str) -> "CircuitBreaker":
        """根据信任级别创建熔断器。

        Args:
            mode: 信任级别 -- "safe"、"auto"、"unsafe" 或 "collaborative"。

        Returns:
            对应模式的 CircuitBreaker 实例。
        """
        thresholds = {
            "safe":           (2,   120.0),
            "auto":           (5,    30.0),
            "unsafe":         (20,    5.0),
            "collaborative":  (3,   60.0),
        }
        failure_threshold, cooldown = thresholds.get(mode, (5, 30.0))
        return cls(CircuitBreakerConfig(
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown,
        ))

    def guard(self) -> GuardResult:
        """
        在每次 API 调用前调用。

        Returns:
            GuardResult.blocked=True 表示熔断器处于 OPEN 状态且冷却未结束。
            调用方必须跳过 API 调用。

            如果 OPEN 但冷却已结束，则转换到 HALF_OPEN 并允许一次探测请求。
        """
        with self._lock:
            now = time.time()

            if self._state == CircuitState.CLOSED:
                return self.GuardResult(blocked=False)

            if self._state == CircuitState.OPEN:
                cooldown = self._effective_cooldown()
                elapsed = now - (self._opened_at or now)
                remaining = cooldown - elapsed

                if remaining > 0:
                    self._total_fast_fails_saved += 1
                    return self.GuardResult(
                        blocked=True,
                        reason=(
                            f"Circuit breaker OPEN. "
                            f"Cooling down: {remaining:.0f}s remaining. "
                            f"Last failure: {self._last_failure_reason}"
                        ),
                        cooldown_remaining_seconds=remaining,
                    )

                # 冷却已结束 -- 转到 HALF_OPEN
                self._transition_to(CircuitState.HALF_OPEN)
                self._half_open_requests_remaining = (
                    self._config.half_open_max_requests
                )
                self._last_probe_at = now
                self._log_transition("OPEN->HALF_OPEN", "cooldown elapsed")
                return self.GuardResult(blocked=False)

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_requests_remaining <= 0:
                    self._total_fast_fails_saved += 1
                    return self.GuardResult(
                        blocked=True,
                        reason=(
                            "Circuit breaker HALF_OPEN -- "
                            "probe limit reached. Waiting for result."
                        ),
                    )
                self._half_open_requests_remaining -= 1
                return self.GuardResult(blocked=False)

            return self.GuardResult(blocked=False)

    def report_success(self) -> None:
        """
        API 调用成功后调用。

        CLOSED 状态: 重置 consecutive_failures 为 0。
        HALF_OPEN 状态: 转回 CLOSED（探测成功）。
        """
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.CLOSED)
                self._log_transition("HALF_OPEN->CLOSED", "probe succeeded")
            self._consecutive_failures = 0
            self._cooldown_multiplier = 1.0

    def report_failure(
        self,
        category: FailureCategory,
        reason: str = "",
    ) -> None:
        """
        API 调用失败后调用。

        递增失败计数器。如果达到阈值且失败类别属于触发类别，
        则转换到 OPEN。认证错误只记录日志不递增计数器 --
        它们是终端的，应由凭证刷新路径处理。

        Args:
            category: 失败类别（FailureCategory 枚举）。
            reason: 人类可读的失败原因。
        """
        with self._lock:
            now = time.time()
            self._last_failure_at = now
            self._last_failure_reason = reason
            self._last_failure_category = category

            if category in AUTH_FAILURE_CATEGORIES:
                self._log_transition(
                    "AUTH_FAILURE",
                    f"Auth error (not counted): {reason}",
                )
                return

            if category not in FAILURE_TRIGGERING_CATEGORIES:
                return

            self._consecutive_failures += 1

            if self._state == CircuitState.HALF_OPEN:
                self._cooldown_multiplier = min(
                    self._cooldown_multiplier
                    * self._config.cooldown_backoff_multiplier,
                    self._config.max_cooldown_seconds
                    / max(self._config.cooldown_seconds, 1),
                )
                self._transition_to(CircuitState.OPEN)
                self._log_transition(
                    "HALF_OPEN->OPEN",
                    f"probe failed ({category.value}): {reason}",
                )

            elif (
                self._consecutive_failures
                >= self._config.failure_threshold
            ):
                if self._state == CircuitState.CLOSED:
                    self._transition_to(CircuitState.OPEN)
                    self._log_transition(
                        "CLOSED->OPEN",
                        f"threshold reached "
                        f"({self._consecutive_failures}/"
                        f"{self._config.failure_threshold}): "
                        f"{reason}",
                    )

    def reset(self) -> None:
        """强制重置熔断器到 CLOSED（例如手动干预后）。"""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._consecutive_failures = 0
            self._cooldown_multiplier = 1.0
            self._log_transition("MANUAL_RESET", "breaker force-reset to CLOSED")

    def force_open(self, reason: str = "manual") -> None:
        """强制打开熔断器（例如达到计费上限时）。

        Args:
            reason: 强制打开的原因。
        """
        with self._lock:
            self._transition_to(CircuitState.OPEN)
            self._last_failure_reason = reason
            self._log_transition("FORCED_OPEN", reason)

    # ------------------------------------------------------------------
    # 查询属性
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """当前熔断器状态。"""
        return self._state

    @property
    def is_closed(self) -> bool:
        """熔断器是否 CLOSED（正常通行）。"""
        return self._state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """熔断器是否 OPEN（快速失败）。"""
        return self._state == CircuitState.OPEN

    @property
    def consecutive_failures(self) -> int:
        """当前连续失败计数。"""
        return self._consecutive_failures

    @property
    def failure_threshold(self) -> int:
        """触发熔断的失败阈值。"""
        return self._config.failure_threshold

    @property
    def cooldown_seconds(self) -> float:
        """冷却时间（秒）。"""
        return self._config.cooldown_seconds

    @property
    def opened_at(self) -> Optional[str]:
        """熔断器打开时的 ISO 时间戳。"""
        if self._opened_at is None:
            return None
        return _ts_to_iso(self._opened_at)

    @property
    def last_failure_at(self) -> Optional[str]:
        """最后一次失败时的 ISO 时间戳。"""
        if self._last_failure_at is None:
            return None
        return _ts_to_iso(self._last_failure_at)

    @property
    def last_failure_reason(self) -> Optional[str]:
        """最后一次失败的原因。"""
        return self._last_failure_reason

    @property
    def total_trips(self) -> int:
        """历史 OPEN 转换总次数。"""
        return self._total_trips

    @property
    def total_fast_fails_saved(self) -> int:
        """被快速失败拦截的请求总数。"""
        return self._total_fast_fails_saved

    def cooldown_remaining_seconds(self) -> float:
        """距离尝试 HALF_OPEN 还剩多少秒（CLOSED 时为 0）。"""
        if self._state != CircuitState.OPEN or self._opened_at is None:
            return 0.0
        elapsed = time.time() - self._opened_at
        remaining = self._effective_cooldown() - elapsed
        return max(0.0, remaining)

    # ------------------------------------------------------------------
    # 快照持久化
    # ------------------------------------------------------------------

    def snapshot(self) -> CircuitBreakerSnapshot:
        """返回不可变快照，用于写入 state.json 持久化。

        Returns:
            CircuitBreakerSnapshot 数据类实例。
        """
        with self._lock:
            return CircuitBreakerSnapshot(
                state=self._state.value,
                consecutive_failures=self._consecutive_failures,
                failure_threshold=self._config.failure_threshold,
                cooldown_seconds=self._config.cooldown_seconds,
                opened_at=(
                    _ts_to_iso(self._opened_at)
                    if self._opened_at else None
                ),
                last_failure_at=(
                    _ts_to_iso(self._last_failure_at)
                    if self._last_failure_at else None
                ),
                last_failure_reason=self._last_failure_reason,
                last_failure_category=(
                    self._last_failure_category.value
                    if self._last_failure_category else None
                ),
                last_probe_at=(
                    _ts_to_iso(self._last_probe_at)
                    if self._last_probe_at else None
                ),
                total_trips=self._total_trips,
                total_fast_fails_saved=self._total_fast_fails_saved,
                current_cooldown_multiplier=self._cooldown_multiplier,
            )

    def restore_from_snapshot(
        self, snap: CircuitBreakerSnapshot
    ) -> None:
        """从持久化快照恢复熔断器状态（例如会话重启后）。

        Args:
            snap: 之前由 snapshot() 生成的 CircuitBreakerSnapshot。
        """
        with self._lock:
            self._state = CircuitState(snap.state)
            self._consecutive_failures = snap.consecutive_failures
            self._opened_at = (
                _iso_to_ts(snap.opened_at)
                if snap.opened_at else None
            )
            self._last_failure_at = (
                _iso_to_ts(snap.last_failure_at)
                if snap.last_failure_at else None
            )
            self._last_failure_reason = snap.last_failure_reason
            self._last_failure_category = (
                FailureCategory(snap.last_failure_category)
                if snap.last_failure_category else None
            )
            self._last_probe_at = (
                _iso_to_ts(snap.last_probe_at)
                if snap.last_probe_at else None
            )
            self._total_trips = snap.total_trips
            self._total_fast_fails_saved = snap.total_fast_fails_saved
            self._cooldown_multiplier = snap.current_cooldown_multiplier

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _effective_cooldown(self) -> float:
        """计算当前有效的冷却时间（含退避乘数）。"""
        return min(
            self._config.cooldown_seconds * self._cooldown_multiplier,
            self._config.max_cooldown_seconds,
        )

    def _transition_to(self, new_state: CircuitState) -> None:
        """执行状态转换。

        Args:
            new_state: 目标 CircuitState。
        """
        self._state = new_state
        if new_state == CircuitState.OPEN:
            self._opened_at = time.time()
            self._total_trips += 1
        if new_state == CircuitState.HALF_OPEN:
            self._half_open_requests_remaining = (
                self._config.half_open_max_requests
            )

    def _log_transition(self, transition: str, detail: str) -> None:
        """向 circuit_breaker.log 追加状态转换事件。

        Args:
            transition: 转换描述（例如 "CLOSED->OPEN"）。
            detail: 人类可读的详细说明。
        """
        log_path = self._config.log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        entry = json.dumps({
            "timestamp": _ts_to_iso(time.time()),
            "transition": transition,
            "detail": detail,
            "state": self._state.value,
            "consecutive_failures": self._consecutive_failures,
            "total_trips": self._total_trips,
            "cooldown_multiplier": round(self._cooldown_multiplier, 2),
        }, ensure_ascii=False)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except OSError:
            pass  # 日志写入失败不应阻断运行
