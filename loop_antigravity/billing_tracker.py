"""
BillingTracker -- 计费追踪器（硬上限 + 配额跟踪）。

实时跟踪 Gemini API 调用的累计成本，强制执行每日和每周硬上限。
防止意外超支，支持四种操作模式的不同计费上限配置。

核心职责:
  1. 跟踪每次 API 调用的 token 消耗和估算成本
  2. 按日/周窗口聚合成本，强制执行硬上限
  3. 在达到警告阈值 (80%) 和严重阈值 (95%) 时发出警报
  4. 提供配额状态查询（可用预算、已用百分比）
  5. 与 CircuitBreaker 集成 -- 达到硬上限时强制熔断
  6. 支持成本数据持久化到 state.json

定价参考 (Gemini 2.5 Flash, 2026-06):
  输入:  $0.00015 / 1K tokens
  输出:  $0.0006  / 1K tokens
  缓存输入: $0.0000375 / 1K tokens
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "BillingTracker",
    "BillingRecord",
    "QuotaWindow",
    "PRICING",
]


# ============================================================================
# 定价常量 (Gemini 2.5 Flash, 2026-06)
# ============================================================================

PRICING = {
    "input_per_1k": 0.00015,
    "output_per_1k": 0.0006,
    "cached_input_per_1k": 0.0000375,
}


# ============================================================================
# 按模式的默认计费上限（美元）
# ============================================================================

_MODE_CAPS = {
    "safe":          {"daily": 5.0,   "weekly": 25.0},
    "auto":          {"daily": 20.0,  "weekly": 100.0},
    "unsafe":        {"daily": 100.0, "weekly": 500.0},
    "collaborative": {"daily": 10.0,  "weekly": 50.0},
}


# ============================================================================
# 数据类
# ============================================================================


@dataclass
class QuotaWindow:
    """单时间窗口（日/周）的成本聚合数据。

    Attributes:
        label: 窗口标签，如 "daily" 或 "weekly"。
        start_ts: 窗口起始 Unix 时间戳。
        end_ts: 窗口结束 Unix 时间戳。
        input_tokens: 窗口内累计输入 token 数。
        output_tokens: 窗口内累计输出 token 数。
        cost_usd: 窗口内估算累计成本（美元）。
        cap_usd: 该窗口的硬上限（美元）。
        invocation_count: 窗口内 API 调用次数。
    """
    label: str = ""
    start_ts: float = 0.0
    end_ts: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cap_usd: float = 0.0
    invocation_count: int = 0

    @property
    def used_pct(self) -> float:
        """已使用预算百分比。"""
        if self.cap_usd <= 0:
            return 100.0
        return min(100.0, (self.cost_usd / self.cap_usd) * 100.0)

    @property
    def exhausted(self) -> bool:
        """预算是否已耗尽（达到或超过 100%）。"""
        return self.cost_usd >= self.cap_usd


@dataclass
class BillingRecord:
    """单次 API 调用的计费记录。

    Attributes:
        timestamp: 调用发生时的 ISO 时间戳。
        input_tokens: 消耗的输入 token 数。
        output_tokens: 消耗的输出 token 数。
        cost_usd: 此次调用的估算成本。
        model: 使用的模型 ID。
        backend: 使用的后端 ("agy_cli" 或 "gemini_sdk")。
        daily_cost_after: 此调用后的当日累计成本。
        weekly_cost_after: 此调用后的当周累计成本。
    """
    timestamp: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    backend: str = ""
    daily_cost_after: float = 0.0
    weekly_cost_after: float = 0.0


# ============================================================================
# BillingTracker 主类
# ============================================================================


class BillingTracker:
    """计费追踪器 -- 实时跟踪 API 成本并强制执行硬上限。

    按日/周窗口聚合 token 消耗和成本。当任一周期的成本
    达到硬上限时，返回 blocked=True 以阻止后续 API 调用。

    典型用法:
        tracker = BillingTracker(mode="auto")
        tracker.record(input_tokens=5000, output_tokens=2000)
        if tracker.is_daily_exhausted:
            print("今日预算已耗尽，等待次日重置")

    Attributes:
        mode: 操作模式，决定每日/每周硬上限。
        daily_cap_usd: 每日成本硬上限（美元）。
        weekly_cap_usd: 每周成本硬上限（美元）。
        warning_pct: 警告阈值百分比（默认 80%）。
        hard_warning_pct: 严重警告阈值百分比（默认 95%）。
    """

    def __init__(
        self,
        mode: str = "auto",
        *,
        daily_cap_usd: Optional[float] = None,
        weekly_cap_usd: Optional[float] = None,
        warning_pct: float = 80.0,
        hard_warning_pct: float = 95.0,
        circuit_breaker: object = None,
    ) -> None:
        """初始化计费追踪器。

        Args:
            mode: 操作模式，决定默认每日/每周上限。
            daily_cap_usd: 覆盖默认每日上限（美元）。
            weekly_cap_usd: 覆盖默认每周上限（美元）。
            warning_pct: 触发警告的成本百分比。
            hard_warning_pct: 触发严重警告的成本百分比。
            circuit_breaker: 可选，达到硬上限时强制熔断的 CircuitBreaker 实例。

        Raises:
            ValueError: 如果 mode 不是有效的操作模式。
        """
        if mode not in _MODE_CAPS:
            raise ValueError(
                f"无效的操作模式 '{mode}'，必须是: "
                f"{list(_MODE_CAPS.keys())}"
            )

        self.mode = mode
        caps = _MODE_CAPS[mode]
        self.daily_cap_usd = daily_cap_usd if daily_cap_usd is not None else caps["daily"]
        self.weekly_cap_usd = weekly_cap_usd if weekly_cap_usd is not None else caps["weekly"]
        self.warning_pct = warning_pct
        self.hard_warning_pct = hard_warning_pct
        self._circuit_breaker = circuit_breaker

        # 累计计数器（运行时会话内）
        self._daily_input: int = 0
        self._daily_output: int = 0
        self._daily_cost: float = 0.0
        self._daily_invocations: int = 0
        self._daily_start_ts: float = self._today_start_ts()

        self._weekly_input: int = 0
        self._weekly_output: int = 0
        self._weekly_cost: float = 0.0
        self._weekly_invocations: int = 0
        self._weekly_start_ts: float = self._week_start_ts()

        # 累计总量（跨会话持久化用）
        self._total_cost: float = 0.0
        self._total_invocations: int = 0

        # 调用历史
        self._records: list[BillingRecord] = []

    # ------------------------------------------------------------------
    # 记录 API 调用
    # ------------------------------------------------------------------

    def record(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        model: str = "gemini-2.5-flash",
        backend: str = "agy_cli",
    ) -> BillingRecord:
        """记录一次 API 调用的 token 消耗和成本。

        同时检查日/周窗口是否需要重置（跨天后自动归零）。

        Args:
            input_tokens: 输入 token 数。
            output_tokens: 输出 token 数。
            model: 使用的模型 ID。
            backend: 使用的后端标识。

        Returns:
            BillingRecord 实例。
        """
        # 检查窗口重置
        self._check_window_reset()

        cost = self._calculate_cost(input_tokens, output_tokens)

        # 更新日窗口
        self._daily_input += input_tokens
        self._daily_output += output_tokens
        self._daily_cost += cost
        self._daily_invocations += 1

        # 更新周窗口
        self._weekly_input += input_tokens
        self._weekly_output += output_tokens
        self._weekly_cost += cost
        self._weekly_invocations += 1

        # 更新总计
        self._total_cost += cost
        self._total_invocations += 1

        # 创建记录
        record = BillingRecord(
            timestamp=time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
            model=model,
            backend=backend,
            daily_cost_after=round(self._daily_cost, 6),
            weekly_cost_after=round(self._weekly_cost, 6),
        )
        self._records.append(record)
        return record

    # ------------------------------------------------------------------
    # 配额检查
    # ------------------------------------------------------------------

    def check_before_call(self) -> tuple[bool, str]:
        """在 API 调用前检查配额是否允许。

        Returns:
            (allowed, reason) 元组。
            allowed=False 表示应阻止此次调用。
        """
        if self.is_daily_exhausted:
            return (False, "每日硬上限已达到")
        if self.is_weekly_exhausted:
            return (False, "每周硬上限已达到")
        return (True, "")

    @property
    def is_daily_exhausted(self) -> bool:
        """每日预算是否已耗尽。"""
        return self._daily_cost >= self.daily_cap_usd

    @property
    def is_weekly_exhausted(self) -> bool:
        """每周预算是否已耗尽。"""
        return self._weekly_cost >= self.weekly_cap_usd

    @property
    def is_any_exhausted(self) -> bool:
        """任一日或周预算是否已耗尽。"""
        return self.is_daily_exhausted or self.is_weekly_exhausted

    # ------------------------------------------------------------------
    # 窗口查询
    # ------------------------------------------------------------------

    def get_daily_window(self) -> QuotaWindow:
        """获取当日计费窗口快照。"""
        self._check_window_reset()
        return QuotaWindow(
            label="daily",
            start_ts=self._daily_start_ts,
            end_ts=self._daily_start_ts + 86400,
            input_tokens=self._daily_input,
            output_tokens=self._daily_output,
            cost_usd=round(self._daily_cost, 6),
            cap_usd=self.daily_cap_usd,
            invocation_count=self._daily_invocations,
        )

    def get_weekly_window(self) -> QuotaWindow:
        """获取当周计费窗口快照。"""
        self._check_window_reset()
        return QuotaWindow(
            label="weekly",
            start_ts=self._weekly_start_ts,
            end_ts=self._weekly_start_ts + 604800,
            input_tokens=self._weekly_input,
            output_tokens=self._weekly_output,
            cost_usd=round(self._weekly_cost, 6),
            cap_usd=self.weekly_cap_usd,
            invocation_count=self._weekly_invocations,
        )

    # ------------------------------------------------------------------
    # 警告信号
    # ------------------------------------------------------------------

    def get_warnings(self) -> list[dict]:
        """获取当前所有触发的警告。

        Returns:
            警告字典列表，含 level、window、used_pct、message 等字段。
        """
        warnings: list[dict] = []
        dw = self.get_daily_window()
        ww = self.get_weekly_window()

        for win in (dw, ww):
            if win.used_pct >= 100:
                warnings.append({
                    "level": "critical",
                    "window": win.label,
                    "used_pct": win.used_pct,
                    "message": (
                        f"{win.label} 预算已耗尽 "
                        f"(${win.cost_usd:.2f}/${win.cap_usd:.2f})"
                    ),
                })
            elif win.used_pct >= self.hard_warning_pct:
                warnings.append({
                    "level": "hard_warning",
                    "window": win.label,
                    "used_pct": win.used_pct,
                    "message": (
                        f"{win.label} 预算使用已达 "
                        f"{win.used_pct:.1f}%"
                    ),
                })
            elif win.used_pct >= self.warning_pct:
                warnings.append({
                    "level": "warning",
                    "window": win.label,
                    "used_pct": win.used_pct,
                    "message": (
                        f"{win.label} 预算使用已达 "
                        f"{win.used_pct:.1f}%"
                    ),
                })

        return warnings

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """导出计费状态为字典，用于写入 state.json。

        Returns:
            包含所有窗口和累计数据的字典。
        """
        self._check_window_reset()
        return {
            "mode": self.mode,
            "daily_cap_usd": self.daily_cap_usd,
            "weekly_cap_usd": self.weekly_cap_usd,
            "daily": {
                "input_tokens": self._daily_input,
                "output_tokens": self._daily_output,
                "cost_usd": round(self._daily_cost, 6),
                "invocations": self._daily_invocations,
                "start_ts": self._daily_start_ts,
            },
            "weekly": {
                "input_tokens": self._weekly_input,
                "output_tokens": self._weekly_output,
                "cost_usd": round(self._weekly_cost, 6),
                "invocations": self._weekly_invocations,
                "start_ts": self._weekly_start_ts,
            },
            "total_cost_usd": round(self._total_cost, 6),
            "total_invocations": self._total_invocations,
        }

    @classmethod
    def from_dict(
        cls, data: dict, circuit_breaker: object = None
    ) -> "BillingTracker":
        """从持久化字典恢复计费追踪器状态。

        Args:
            data: 由 to_dict() 生成的字典。
            circuit_breaker: 可选的 CircuitBreaker 实例。

        Returns:
            恢复状态的 BillingTracker 实例。
        """
        tracker = cls(
            mode=data.get("mode", "auto"),
            daily_cap_usd=data.get("daily_cap_usd"),
            weekly_cap_usd=data.get("weekly_cap_usd"),
            circuit_breaker=circuit_breaker,
        )
        # 恢复日窗口
        d = data.get("daily", {})
        tracker._daily_input = d.get("input_tokens", 0)
        tracker._daily_output = d.get("output_tokens", 0)
        tracker._daily_cost = d.get("cost_usd", 0.0)
        tracker._daily_invocations = d.get("invocations", 0)
        tracker._daily_start_ts = d.get("start_ts", tracker._today_start_ts())
        # 恢复周窗口
        w = data.get("weekly", {})
        tracker._weekly_input = w.get("input_tokens", 0)
        tracker._weekly_output = w.get("output_tokens", 0)
        tracker._weekly_cost = w.get("cost_usd", 0.0)
        tracker._weekly_invocations = w.get("invocations", 0)
        tracker._weekly_start_ts = w.get("start_ts", tracker._week_start_ts())
        # 总计
        tracker._total_cost = data.get("total_cost_usd", 0.0)
        tracker._total_invocations = data.get("total_invocations", 0)
        # 重置旧窗口
        tracker._check_window_reset()
        return tracker

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_cost(input_tokens: int, output_tokens: int) -> float:
        """根据 token 计数计算估算成本。

        Args:
            input_tokens: 输入 token 数。
            output_tokens: 输出 token 数。

        Returns:
            估算成本（美元）。
        """
        return (
            (input_tokens / 1000.0) * PRICING["input_per_1k"]
            + (output_tokens / 1000.0) * PRICING["output_per_1k"]
        )

    def _check_window_reset(self) -> None:
        """检查日/周窗口是否需要重置。

        如果当前时间超出了窗口范围，则将计数器归零
        并更新窗口起始时间戳。
        """
        now = time.time()
        # 日窗口重置（每 24 小时）
        if now >= self._daily_start_ts + 86400:
            self._daily_input = 0
            self._daily_output = 0
            self._daily_cost = 0.0
            self._daily_invocations = 0
            self._daily_start_ts = self._today_start_ts()
        # 周窗口重置（每 7 天）
        if now >= self._weekly_start_ts + 604800:
            self._weekly_input = 0
            self._weekly_output = 0
            self._weekly_cost = 0.0
            self._weekly_invocations = 0
            self._weekly_start_ts = self._week_start_ts()

    @staticmethod
    def _today_start_ts() -> float:
        """计算今日零点的 Unix 时间戳。"""
        now = time.time()
        return now - (now % 86400)

    @staticmethod
    def _week_start_ts() -> float:
        """计算本周一零点的 Unix 时间戳。"""
        now = time.time()
        # 周一 = 0 (time.gmtime 的 tm_wday 中 Monday=0)
        wday = time.gmtime(now).tm_wday
        return now - (now % 86400) - (wday * 86400)
