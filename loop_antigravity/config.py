"""
loop_antigravity 配置管理模块。

管理 agy CLI 路径、Gemini project/location/model、配额限制、
billing 阈值、操作模式等运行时配置。

配置来源（优先级从高到低）:
    1. 环境变量: LOOP_AG_*
    2. state.json config 字段
    3. 默认值（此模块中定义）
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


# ============================================================================
# 默认常量
# ============================================================================

# agy CLI 默认设置
DEFAULT_AGY_PATH = "agy"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_LOCATION = "us-central1"

# 配额与计费默认值（按模式分组）
BILLING_CAPS = {
    "safe":          {"daily": 5.0,   "weekly": 25.0},
    "auto":          {"daily": 20.0,  "weekly": 100.0},
    "unsafe":        {"daily": 100.0, "weekly": 500.0},
    "collaborative": {"daily": 10.0,  "weekly": 50.0},
}

# 熔断器阈值（按模式分组）
CIRCUIT_BREAKER_THRESHOLDS = {
    "safe":          {"failure_threshold": 2,   "cooldown_seconds": 120},
    "auto":          {"failure_threshold": 5,   "cooldown_seconds": 30},
    "unsafe":        {"failure_threshold": 20,  "cooldown_seconds": 5},
    "collaborative": {"failure_threshold": 3,   "cooldown_seconds": 60},
}

# 重试配置（按模式分组）
RETRY_CONFIG = {
    "safe": {
        "retry_base_delay_ms": 2000,
        "retry_max_delay_ms": 30000,
        "retry_max_attempts": 2,
        "timeout_ms": 600_000,
        "temperature": 0.4,
        "max_output_tokens": 4096,
    },
    "auto": {
        "retry_base_delay_ms": 1000,
        "retry_max_delay_ms": 16000,
        "retry_max_attempts": 5,
        "timeout_ms": 300_000,
        "temperature": 0.7,
        "max_output_tokens": 8192,
    },
    "unsafe": {
        "retry_base_delay_ms": 500,
        "retry_max_delay_ms": 8000,
        "retry_max_attempts": 10,
        "timeout_ms": 600_000,
        "temperature": 1.0,
        "max_output_tokens": 16384,
    },
    "collaborative": {
        "retry_base_delay_ms": 1500,
        "retry_max_delay_ms": 30000,
        "retry_max_attempts": 3,
        "timeout_ms": 600_000,
        "temperature": 0.5,
        "max_output_tokens": 8192,
    },
}

# Gemini 2.5 Flash 定价 (2026-06)
PRICING_INPUT_PER_1K = 0.00015
PRICING_OUTPUT_PER_1K = 0.0006
PRICING_CACHED_INPUT_PER_1K = 0.0000375

# Token 预算
DEFAULT_TOKEN_BUDGET = 900_000

# 有效操作模式
VALID_MODES = ("safe", "auto", "unsafe", "collaborative")


# ============================================================================
# 数据类
# ============================================================================


@dataclass
class AgyConfig:
    """agy CLI 相关配置。"""
    agy_path: str = DEFAULT_AGY_PATH
    model: str = DEFAULT_MODEL
    gemini_project: Optional[str] = None
    gemini_location: str = DEFAULT_GEMINI_LOCATION


@dataclass
class BillingConfig:
    """计费和配额相关配置。"""
    daily_cap_usd: float = 20.0
    weekly_cap_usd: float = 100.0
    hard_cap_enforced: bool = True
    warning_threshold_pct: float = 80.0
    hard_warning_threshold_pct: float = 95.0


@dataclass
class RuntimeConfig:
    """运行时行为配置。"""
    mode: str = "auto"
    context_window_strategy: str = "whole_codebase"
    max_cycles: int = 5
    convergence_rounds: int = 2
    route_repeat_max: int = 3
    token_budget: int = DEFAULT_TOKEN_BUDGET
    timeout_ms: int = 300_000
    temperature: float = 0.7
    max_output_tokens: int = 8192


# ============================================================================
# 配置加载器
# ============================================================================


class Config:
    """
    统一的运行时配置管理器。

    整合 agy CLI、Gemini、计费、熔断器、运行模式等所有配置。
    支持从环境变量和 state.json 覆盖默认值。

    Attributes:
        agy: agy CLI 相关配置。
        billing: 计费与配额配置。
        runtime: 运行时行为配置。
        circuit_breaker_thresholds: 熔断器阈值字典（按模式只读）。
        retry: 重试策略参数字典（按模式只读）。
    """

    # 受支持的有效模式（类级别常量）
    VALID_MODES = VALID_MODES

    def __init__(
        self,
        mode: str = "auto",
        *,
        agy_path: Optional[str] = None,
        model: Optional[str] = None,
        gemini_project: Optional[str] = None,
        gemini_location: Optional[str] = None,
        daily_cap_usd: Optional[float] = None,
        weekly_cap_usd: Optional[float] = None,
        context_window_strategy: Optional[str] = None,
        max_cycles: Optional[int] = None,
        convergence_rounds: Optional[int] = None,
        timeout_ms: Optional[int] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ) -> None:
        """初始化配置管理器。

        Args:
            mode: 操作模式，可选 "safe"、"auto"、"unsafe"、"collaborative"。
            agy_path: agy CLI 可执行文件路径，可从环境变量 LOOP_AG_AGY_PATH 读取。
            model: Gemini 模型 ID。
            gemini_project: GCP 项目 ID。
            gemini_location: GCP 区域。
            daily_cap_usd: 每日成本硬上限（美元）。
            weekly_cap_usd: 每周成本硬上限（美元）。
            context_window_strategy: 上下文窗口策略。
            max_cycles: 最大循环次数。
            convergence_rounds: 收敛所需轮次。
            timeout_ms: 默认超时时间（毫秒）。
            temperature: 默认采样温度。
            max_output_tokens: 最大输出 token 数。

        Raises:
            ValueError: 如果 mode 不是有效值。
        """
        if mode not in self.VALID_MODES:
            raise ValueError(
                f"无效的操作模式 '{mode}'，必须是以下之一: {self.VALID_MODES}"
            )

        self._mode = mode

        # agy CLI 配置
        self.agy = AgyConfig(
            agy_path=agy_path or os.environ.get("LOOP_AG_AGY_PATH", DEFAULT_AGY_PATH),
            model=model or os.environ.get("LOOP_AG_MODEL", DEFAULT_MODEL),
            gemini_project=gemini_project or os.environ.get("LOOP_AG_GEMINI_PROJECT"),
            gemini_location=gemini_location or os.environ.get(
                "LOOP_AG_GEMINI_LOCATION", DEFAULT_GEMINI_LOCATION
            ),
        )

        # 计费配置
        caps = BILLING_CAPS.get(mode, BILLING_CAPS["auto"])
        self.billing = BillingConfig(
            daily_cap_usd=daily_cap_usd if daily_cap_usd is not None else float(
                os.environ.get("LOOP_AG_DAILY_CAP", caps["daily"])
            ),
            weekly_cap_usd=weekly_cap_usd if weekly_cap_usd is not None else float(
                os.environ.get("LOOP_AG_WEEKLY_CAP", caps["weekly"])
            ),
            hard_cap_enforced=os.environ.get("LOOP_AG_HARD_CAP_ENFORCED", "1") != "0",
        )

        # 运行时配置
        retry = RETRY_CONFIG.get(mode, RETRY_CONFIG["auto"])
        self.runtime = RuntimeConfig(
            mode=mode,
            context_window_strategy=context_window_strategy or "whole_codebase",
            max_cycles=max_cycles if max_cycles is not None else int(
                os.environ.get("LOOP_AG_MAX_CYCLES", "5")
            ),
            convergence_rounds=convergence_rounds if convergence_rounds is not None else 2,
            route_repeat_max=3,
            token_budget=DEFAULT_TOKEN_BUDGET,
            timeout_ms=timeout_ms if timeout_ms is not None else retry["timeout_ms"],
            temperature=temperature if temperature is not None else retry["temperature"],
            max_output_tokens=max_output_tokens if max_output_tokens is not None else retry["max_output_tokens"],
        )

        # 熔断器阈值（只读，按模式固定）
        self.circuit_breaker_thresholds = CIRCUIT_BREAKER_THRESHOLDS.get(
            mode, CIRCUIT_BREAKER_THRESHOLDS["auto"]
        )

        # 重试配置（只读，按模式固定）
        self.retry = RETRY_CONFIG.get(mode, RETRY_CONFIG["auto"])

    # ------------------------------------------------------------------
    # 属性访问器
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        """当前操作模式。"""
        return self._mode

    @property
    def failure_threshold(self) -> int:
        """熔断器连续失败阈值。"""
        return self.circuit_breaker_thresholds["failure_threshold"]

    @property
    def cooldown_seconds(self) -> float:
        """熔断器冷却时间（秒）。"""
        return float(self.circuit_breaker_thresholds["cooldown_seconds"])

    @property
    def retry_base_delay_ms(self) -> int:
        """指数退避基础延迟（毫秒）。"""
        return self.retry["retry_base_delay_ms"]

    @property
    def retry_max_delay_ms(self) -> int:
        """指数退避最大延迟（毫秒）。"""
        return self.retry["retry_max_delay_ms"]

    @property
    def retry_max_attempts(self) -> int:
        """最大重试次数。"""
        return self.retry["retry_max_attempts"]

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """将配置导出为字典，适合写入 state.json config 字段。

        Returns:
            包含所有配置键值的字典。
        """
        return {
            "mode": self.mode,
            "model": self.agy.model,
            "gemini_project": self.agy.gemini_project,
            "gemini_location": self.agy.gemini_location,
            "context_window_strategy": self.runtime.context_window_strategy,
            "max_cycles": self.runtime.max_cycles,
            "convergence_rounds": self.runtime.convergence_rounds,
            "route_repeat_max": self.runtime.route_repeat_max,
            "token_budget": self.runtime.token_budget,
            "timeout_ms": self.runtime.timeout_ms,
            "temperature": self.runtime.temperature,
            "max_output_tokens": self.runtime.max_output_tokens,
            "daily_cap_usd": self.billing.daily_cap_usd,
            "weekly_cap_usd": self.billing.weekly_cap_usd,
            "hard_cap_enforced": self.billing.hard_cap_enforced,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """从字典加载配置（例如从 state.json 的 config 字段读取）。

        Args:
            data: 配置字典。

        Returns:
            Config 实例。
        """
        return cls(
            mode=data.get("mode", "auto"),
            model=data.get("model"),
            gemini_project=data.get("gemini_project"),
            gemini_location=data.get("gemini_location"),
            daily_cap_usd=data.get("daily_cap_usd"),
            weekly_cap_usd=data.get("weekly_cap_usd"),
            context_window_strategy=data.get("context_window_strategy"),
            max_cycles=data.get("max_cycles"),
            convergence_rounds=data.get("convergence_rounds"),
            timeout_ms=data.get("timeout_ms"),
            temperature=data.get("temperature"),
            max_output_tokens=data.get("max_output_tokens"),
        )
