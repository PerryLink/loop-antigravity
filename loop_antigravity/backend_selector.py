"""
BackendSelector -- 后端选择器（agy CLI 优先 -> SDK 回退）。

在 agy CLI 和 Gemini SDK 之间自动选择最佳可用后端。
优先级策略:
  1. agy CLI（首选 -- 完整功能，stream-json 输出，token 追踪）
  2. Gemini SDK（回退 -- 当 agy CLI 不可用时自动切换）

选择逻辑:
  - 启动时检测 agy CLI 是否已安装且健康
  - 如果 agy CLI 可用 -> 使用 AgyClient
  - 如果 agy CLI 不可用 -> 自动降级到 GeminiSdkClient
  - 支持运行时动态切换（agy CLI 恢复后切回）

核心职责:
  1. 自动检测 agy CLI 可用性（路径、版本、标志兼容性）
  2. 检测 Gemini SDK 可用性
  3. 按优先级选择后端
  4. 提供统一的后端接口（GeminiBackend Protocol）
  5. 支持强制指定后端类型（通过环境变量或参数）
  6. 健康状态汇总报告
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "BackendSelector",
    "BackendSelection",
    "BACKEND_AGY_CLI",
    "BACKEND_GEMINI_SDK",
]


# ============================================================================
# 后端类型常量
# ============================================================================

BACKEND_AGY_CLI = "agy_cli"
BACKEND_GEMINI_SDK = "gemini_sdk"

# 环境变量：强制指定后端
_ENV_FORCE_BACKEND = "LOOP_AG_BACKEND"

# 所有可用的后端类型
_VALID_BACKENDS = frozenset({BACKEND_AGY_CLI, BACKEND_GEMINI_SDK, "auto"})


# ============================================================================
# 数据类
# ============================================================================


@dataclass
class BackendSelection:
    """后端选择结果。

    Attributes:
        backend_type: 选中的后端类型 -- "agy_cli" 或 "gemini_sdk"。
        backend_instance: 初始化好的后端客户端实例。
        agy_available: agy CLI 是否可用。
        sdk_available: Gemini SDK 是否可用。
        selection_reason: 人类可读的选择原因。
        health_summary: 所有后端的健康检查摘要。
        selected_at: 选择时的 ISO 时间戳。
    """
    backend_type: str = ""
    backend_instance: Any = None
    agy_available: bool = False
    sdk_available: bool = False
    selection_reason: str = ""
    health_summary: dict = field(default_factory=dict)
    selected_at: str = field(default_factory=lambda: time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
    ))


# ============================================================================
# BackendSelector 主类
# ============================================================================


class BackendSelector:
    """后端选择器 -- 自动选择最佳可用的 Gemini 后端。

    按优先级检测后端可用性:
      1. 检查环境变量 LOOP_AG_BACKEND 是否强制指定后端
      2. 检测 agy CLI 是否已安装、已认证、标志兼容
      3. 如果 agy CLI 不可用，检查 Gemini SDK 是否已安装
      4. 返回最佳可用后端

    Attributes:
        mode: 操作模式（传递给后端客户端）。
        model: 模型 ID。
        gemini_project: GCP 项目 ID。
        gemini_location: GCP 区域。
    """

    def __init__(
        self,
        mode: str = "auto",
        *,
        model: str = "gemini-2.5-flash",
        gemini_project: Optional[str] = None,
        gemini_location: str = "us-central1",
        circuit_breaker: Any = None,
    ) -> None:
        """初始化后端选择器。

        Args:
            mode: 操作模式 -- "safe"、"auto"、"unsafe"、"collaborative"。
            model: 默认模型 ID。
            gemini_project: GCP 项目 ID。
            gemini_location: GCP 区域。
            circuit_breaker: CircuitBreaker 实例，
                             传递给选中的后端客户端。
        """
        self.mode = mode
        self.model = model
        self.gemini_project = gemini_project
        self.gemini_location = gemini_location
        self.circuit_breaker = circuit_breaker

        # 缓存
        self._cached_selection: Optional[BackendSelection] = None
        self._agy_available: Optional[bool] = None
        self._sdk_available: Optional[bool] = None

    # ------------------------------------------------------------------
    # 公共 API：选择后端
    # ------------------------------------------------------------------

    def select(self, force: bool = False) -> BackendSelection:
        """选择最佳可用的 Gemini 后端。

        决策流程:
          1. LOOP_AG_BACKEND 环境变量强制指定 -> 直接使用指定后端
          2. agy CLI 可用 -> 使用 AgyClient
          3. Gemini SDK 可用 -> 使用 GeminiSdkClient
          4. 两者均不可用 -> 抛出 RuntimeError

        Args:
            force: 是否强制重新检测（忽略缓存）。

        Returns:
            BackendSelection 包含选中的后端实例和元数据。

        Raises:
            RuntimeError: 没有任何可用的后端。
        """
        if not force and self._cached_selection is not None:
            return self._cached_selection

        selection = BackendSelection()

        # 检测环境变量强制指定
        forced = os.environ.get(_ENV_FORCE_BACKEND, "").strip().lower()
        if forced in _VALID_BACKENDS and forced != "auto":
            return self._select_forced(forced, selection)

        # 步骤 1: 检测 agy CLI
        agy_ok = self._check_agy_available()

        if agy_ok:
            # agy CLI 可用 -- 优先使用
            from loop_antigravity.agy_client import AgyClient
            client = AgyClient(
                mode=self.mode,
                circuit_breaker=self.circuit_breaker,
            )
            selection.backend_type = BACKEND_AGY_CLI
            selection.backend_instance = client
            selection.agy_available = True
            selection.sdk_available = self._check_sdk_available()
            selection.selection_reason = "agy CLI 可用，已选择为主后端"
            self._cached_selection = selection
            return selection

        # 步骤 2: agy CLI 不可用 -- 尝试 Gemini SDK
        sdk_ok = self._check_sdk_available()

        if sdk_ok:
            from loop_antigravity.gemini_sdk_client import GeminiSdkClient
            client = GeminiSdkClient(
                model=self.model,
                gemini_project=self.gemini_project,
                gemini_location=self.gemini_location,
                circuit_breaker=self.circuit_breaker,
            )
            selection.backend_type = BACKEND_GEMINI_SDK
            selection.backend_instance = client
            selection.agy_available = False
            selection.sdk_available = True
            selection.selection_reason = (
                "agy CLI 不可用，已回退到 Gemini SDK"
            )
            self._cached_selection = selection
            return selection

        # 步骤 3: 两者均不可用
        raise RuntimeError(
            "没有可用的 Gemini 后端。"
            "请安装 agy CLI (pip install google-antigravity) "
            "或 google-generativeai SDK (pip install google-generativeai)。"
            "\n运行 loop-antigravity --check 查看详细诊断信息。"
        )

    # ------------------------------------------------------------------
    # 健康摘要
    # ------------------------------------------------------------------

    def health_summary(self) -> dict:
        """获取所有后端的综合健康检查摘要。

        Returns:
            包含 agy_cli 和 gemini_sdk 健康状态的字典。
        """
        return {
            "agy_cli": {
                "available": self._check_agy_available(),
                "message": (
                    "agy CLI 可用"
                    if self._check_agy_available()
                    else "agy CLI 不可用 -- 请安装 google-antigravity"
                ),
            },
            "gemini_sdk": {
                "available": self._check_sdk_available(),
                "message": (
                    "Gemini SDK 可用"
                    if self._check_sdk_available()
                    else "Gemini SDK 不可用 -- 请安装 google-generativeai"
                ),
            },
            "recommended_backend": self._resolve_recommended(),
            "checked_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        }

    # ------------------------------------------------------------------
    # 内部：可用性检测
    # ------------------------------------------------------------------

    def _check_agy_available(self) -> bool:
        """检测 agy CLI 是否可用。

        检测步骤:
          1. agy 二进制文件是否在 PATH 上
          2. agy --version 是否正常返回
          3. 关键标志是否支持

        Returns:
            True 表示 agy CLI 可用。
        """
        if self._agy_available is not None:
            return self._agy_available

        # 检查 agy 是否在 PATH 上
        try:
            proc = subprocess.run(
                ["agy", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                self._agy_available = False
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._agy_available = False
            return False

        # 关键标志快速检测
        try:
            proc = subprocess.run(
                [
                    "agy", "-p", "ping",
                    "--non-interactive",
                    "--output-format", "stream-json",
                    "--yolo",
                    "--model", "gemini-2.5-flash",
                    "--max-output-tokens", "16",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=20,
            )
            if proc.returncode == 0:
                self._agy_available = True
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        self._agy_available = False
        return False

    def _check_sdk_available(self) -> bool:
        """检测 Gemini SDK 是否可用。

        Returns:
            True 表示 google-generativeai SDK 已安装。
        """
        if self._sdk_available is not None:
            return self._sdk_available

        try:
            import google.generativeai
            self._sdk_available = True
            return True
        except ImportError:
            self._sdk_available = False
            return False

    # ------------------------------------------------------------------
    # 内部：强制选择与推荐
    # ------------------------------------------------------------------

    def _select_forced(
        self, backend: str, selection: BackendSelection
    ) -> BackendSelection:
        """根据强制指定的后端类型创建选择结果。

        Args:
            backend: 强制指定的后端类型。
            selection: 待填充的 BackendSelection 实例。

        Returns:
            填充好的 BackendSelection。

        Raises:
            RuntimeError: 强制指定的后端不可用。
        """
        if backend == BACKEND_AGY_CLI:
            if not self._check_agy_available():
                raise RuntimeError(
                    "LOOP_AG_BACKEND=agy_cli 但 agy CLI 不可用"
                )
            from loop_antigravity.agy_client import AgyClient
            selection.backend_type = BACKEND_AGY_CLI
            selection.backend_instance = AgyClient(
                mode=self.mode,
                circuit_breaker=self.circuit_breaker,
            )
            selection.agy_available = True
            selection.selection_reason = (
                "通过 LOOP_AG_BACKEND 环境变量强制选择 agy CLI"
            )
        elif backend == BACKEND_GEMINI_SDK:
            if not self._check_sdk_available():
                raise RuntimeError(
                    "LOOP_AG_BACKEND=gemini_sdk 但 Gemini SDK 不可用"
                )
            from loop_antigravity.gemini_sdk_client import GeminiSdkClient
            selection.backend_type = BACKEND_GEMINI_SDK
            selection.backend_instance = GeminiSdkClient(
                model=self.model,
                gemini_project=self.gemini_project,
                gemini_location=self.gemini_location,
                circuit_breaker=self.circuit_breaker,
            )
            selection.sdk_available = True
            selection.selection_reason = (
                "通过 LOOP_AG_BACKEND 环境变量强制选择 Gemini SDK"
            )

        selection.sdk_available = self._check_sdk_available()
        self._cached_selection = selection
        return selection

    def _resolve_recommended(self) -> str:
        """确定推荐使用的后端。

        Returns:
            推荐的后端类型字符串。
        """
        if self._check_agy_available():
            return BACKEND_AGY_CLI
        if self._check_sdk_available():
            return BACKEND_GEMINI_SDK
        return "none"

    # ------------------------------------------------------------------
    # 缓存管理
    # ------------------------------------------------------------------

    def invalidate_cache(self) -> None:
        """清除所有缓存，强制下次 select() 重新检测。"""
        self._cached_selection = None
        self._agy_available = None
        self._sdk_available = None
