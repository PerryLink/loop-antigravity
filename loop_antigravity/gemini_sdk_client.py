"""
GeminiSdkClient -- Google Gemini SDK 回退路径。

当 agy CLI 不可用时（未安装、版本不兼容、标志验证失败），
自动切换到 google-generativeai SDK 直接调用 Gemini API。
实现与 AgyClient 兼容的接口（GeminiBackend Protocol），
确保上层调用方无需感知底层使用哪个后端。

核心职责:
  1. 通过 google-generativeai SDK 直接调用 Gemini API
  2. 支持同步文本生成和流式输出
  3. 模拟 agy CLI 的 stream-json 事件格式（兼容性）
  4. 与 CircuitBreaker 集成 -- 失败保护
  5. 指数退避重试逻辑
  6. Token 使用量追踪和成本估算

SDK 依赖:
  google-generativeai >= 0.3.0 （可选依赖，仅在 agy CLI 不可用时需要）
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["GeminiSdkClient", "GeminiSdkResult"]


# ============================================================================
# 定价常量
# ============================================================================

_PRICING_INPUT = 0.00015
_PRICING_OUTPUT = 0.0006

# ============================================================================
# 重试和超时默认值
# ============================================================================

_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_MS = 1000
_RETRY_MAX_DELAY_MS = 16000


# ============================================================================
# 数据类
# ============================================================================


@dataclass
class GeminiSdkResult:
    """单次 Gemini SDK 调用的结构化结果。

    与 AgyResult 保持兼容的字段结构，便于上层代码统一处理。

    Attributes:
        text: 完整的响应文本。
        tokens_input: 输入 token 数。
        tokens_output: 输出 token 数。
        tokens_total: 总 token 数。
        model: 实际使用的模型名称。
        finish_reason: 结束原因。
        stream_events: 模拟的 stream-json 事件列表。
        latency_ms: 调用耗时（毫秒）。
        cost_estimate_usd: 估算成本（美元）。
        backend_used: 始终为 "gemini_sdk"。
        exit_code: 始终为 0（SDK 调用没有子进程退出码概念）。
        retry_count: 重试次数。
    """
    text: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_total: int = 0
    model: str = ""
    finish_reason: str = "UNKNOWN"
    stream_events: list = field(default_factory=list)
    latency_ms: int = 0
    cost_estimate_usd: float = 0.0
    backend_used: str = "gemini_sdk"
    exit_code: int = 0
    retry_count: int = 0

    @property
    def ok(self) -> bool:
        """True 表示获得了带文本内容的成功结果。"""
        return bool(self.text) and self.finish_reason in ("STOP", "MAX_TOKENS")


# ============================================================================
# GeminiSdkClient 主类
# ============================================================================


class GeminiSdkClient:
    """Google Gemini SDK 回退客户端。

    当 agy CLI 不可用时自动切换到此后端。
    实现与 AgyClient 兼容的接口。

    Attributes:
        model: 默认模型 ID。
        backend_type: 始终返回 "gemini_sdk"。
        sdk_available: google-generativeai SDK 是否已安装。
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        gemini_project: Optional[str] = None,
        gemini_location: str = "us-central1",
        circuit_breaker: Any = None,
    ) -> None:
        """初始化 Gemini SDK 客户端。

        Args:
            model: 模型 ID。
            gemini_project: GCP 项目 ID（可选，用于 Vertex AI 路径）。
            gemini_location: GCP 区域。
            circuit_breaker: CircuitBreaker 实例，用于失败保护。
        """
        self.model = model
        self.gemini_project = gemini_project
        self.gemini_location = gemini_location
        self.circuit_breaker = circuit_breaker

        # 检测 SDK 是否可用
        self._genai = None
        self._sdk_available = False
        self._sdk_error: str = ""
        try:
            import google.generativeai as genai
            self._genai = genai
            self._sdk_available = True
        except ImportError as e:
            self._sdk_error = (
                f"google-generativeai SDK 未安装: {e}。"
                f"请运行: pip install google-generativeai"
            )
        except Exception as e:
            self._sdk_error = f"SDK 初始化失败: {e}"

        # 遥测
        self._total_calls: int = 0
        self._total_success: int = 0
        self._total_latency_ms: int = 0

    @property
    def backend_type(self) -> str:
        """返回 "gemini_sdk" -- 实现 GeminiBackend 协议。"""
        return "gemini_sdk"

    @property
    def sdk_available(self) -> bool:
        """google-generativeai SDK 是否已安装且可导入。"""
        return self._sdk_available

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    def check_health(self) -> dict:
        """验证 SDK 客户端是否可用。

        Returns:
            包含 ok、backend_type、message 等字段的字典。
        """
        if not self._sdk_available:
            return {
                "ok": False,
                "backend_type": "gemini_sdk",
                "message": self._sdk_error,
            }
        return {
            "ok": True,
            "backend_type": "gemini_sdk",
            "message": "Gemini SDK 可用",
        }

    # ------------------------------------------------------------------
    # 核心调用
    # ------------------------------------------------------------------

    def invoke(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
        timeout_ms: int = 300_000,
    ) -> GeminiSdkResult:
        """通过 Gemini SDK 发送生成请求。

        支持指数退避重试。在 CircuitBreaker OPEN 时快速失败。

        Args:
            prompt: 用户提示词文本。
            system_prompt: 系统指令（可选）。
            model: 模型 ID（覆盖默认值）。
            temperature: 采样温度（0.0-2.0）。
            max_output_tokens: 最大输出 token 数。
            timeout_ms: 超时时间（毫秒）。

        Returns:
            GeminiSdkResult 实例。

        Raises:
            RuntimeError: SDK 不可用。
            Exception: SDK 调用失败（重试耗尽后向上抛出）。
        """
        if not self._sdk_available:
            raise RuntimeError(self._sdk_error)

        actual_model = model or self.model

        # 检查熔断器
        if self.circuit_breaker is not None:
            guard = self._check_circuit()
            if guard.get("blocked", False):
                raise RuntimeError(
                    f"熔断器已打开 -- {guard.get('reason', 'unknown')}"
                )

        last_error: Optional[Exception] = None
        start_time = time.time()

        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                result = self._do_invoke(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=actual_model,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    timeout_ms=timeout_ms,
                )
                result.retry_count = attempt
                result.latency_ms = int((time.time() - start_time) * 1000)

                # 成功 -- 通知熔断器
                self._report_success()
                self._total_calls += 1
                self._total_success += 1
                self._total_latency_ms += result.latency_ms
                return result

            except Exception as e:
                last_error = e
                if attempt < _RETRY_MAX_ATTEMPTS - 1:
                    delay = self._backoff_delay(
                        attempt,
                        _RETRY_BASE_DELAY_MS,
                        _RETRY_MAX_DELAY_MS,
                    )
                    time.sleep(delay / 1000.0)

        # 重试耗尽
        self._report_failure(str(last_error))
        raise last_error  # type: ignore[misc]

    def _do_invoke(
        self,
        prompt: str,
        system_prompt: Optional[str],
        model: str,
        temperature: float,
        max_output_tokens: int,
        timeout_ms: int,
    ) -> GeminiSdkResult:
        """执行实际的 SDK 调用（单次尝试）。

        通过 google-generativeai SDK 发送请求，解析响应，
        构造与 AgyResult 兼容的结构化结果。

        Args:
            prompt: 用户提示词。
            system_prompt: 系统指令。
            model: 模型 ID。
            temperature: 采样温度。
            max_output_tokens: 最大输出 token 数。
            timeout_ms: 超时时间。

        Returns:
            GeminiSdkResult 实例。
        """
        genai = self._genai
        genai.configure(api_key=self._resolve_api_key())

        # 构建生成配置
        gen_config = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }

        # 构建完整提示词
        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [system_prompt]})
            contents.append({
                "role": "model",
                "parts": ["我理解了以上系统指令，已准备好回答后续问题。"],
            })
        contents.append({"role": "user", "parts": [prompt]})

        # 调用 SDK
        sdk_model = genai.GenerativeModel(
            model_name=model,
            generation_config=gen_config,
        )
        response = sdk_model.generate_content(contents)

        # 提取文本
        text = ""
        try:
            text = response.text or ""
        except ValueError:
            # 响应可能因安全过滤被阻止
            if response.prompt_feedback:
                fb = response.prompt_feedback
                if getattr(fb, "block_reason", None):
                    raise RuntimeError(
                        f"响应被阻止: {fb.block_reason}"
                    )

        # 提取 token 使用量
        tokens_input = 0
        tokens_output = 0
        try:
            usage = response.usage_metadata
            if usage:
                tokens_input = getattr(usage, "prompt_token_count", 0)
                tokens_output = getattr(usage, "candidates_token_count", 0)
        except Exception:
            pass

        # 构建模拟 stream-json 事件（兼容 AgyResult 格式）
        stream_events = [
            {"type": "status", "stage": "started", "model": model},
            {"type": "text", "content": text},
            {
                "type": "usage",
                "input_tokens": tokens_input,
                "output_tokens": tokens_output,
                "total_tokens": tokens_input + tokens_output,
                "model": model,
            },
            {"type": "status", "stage": "complete", "finish_reason": "STOP"},
        ]

        # 计算成本
        cost = self._calculate_cost(tokens_input, tokens_output)

        return GeminiSdkResult(
            text=text,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            tokens_total=tokens_input + tokens_output,
            model=model,
            finish_reason="STOP",
            stream_events=stream_events,
            cost_estimate_usd=cost,
        )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_api_key() -> str:
        """解析 Gemini API 密钥。

        优先级:
          1. GEMINI_API_KEY 环境变量
          2. GOOGLE_API_KEY 环境变量
          3. 空字符串（将使用 ADC）

        Returns:
            API 密钥字符串。
        """
        import os
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            val = os.environ.get(var, "").strip()
            if val:
                return val
        return ""

    @staticmethod
    def _calculate_cost(input_tokens: int, output_tokens: int) -> float:
        """根据 token 计数计算估算成本。"""
        return (
            (input_tokens / 1000.0) * _PRICING_INPUT
            + (output_tokens / 1000.0) * _PRICING_OUTPUT
        )

    @staticmethod
    def _backoff_delay(attempt: int, base_ms: int, max_ms: int) -> int:
        """计算带抖动的指数退避延迟。"""
        delay = min(max_ms, base_ms * (2 ** attempt))
        jitter = delay * 0.25 * random.random()
        return int(delay + jitter)

    def _check_circuit(self) -> dict:
        """检查熔断器状态。"""
        cb = self.circuit_breaker
        try:
            if hasattr(cb, "guard"):
                result = cb.guard()
                return {
                    "blocked": getattr(result, "blocked", False),
                    "reason": getattr(result, "reason", ""),
                }
        except Exception:
            pass
        return {"blocked": False, "reason": ""}

    def _report_success(self) -> None:
        """向熔断器报告成功。"""
        cb = self.circuit_breaker
        try:
            if hasattr(cb, "on_success"):
                cb.on_success()
        except Exception:
            pass

    def _report_failure(self, reason: str) -> None:
        """向熔断器报告失败。"""
        cb = self.circuit_breaker
        try:
            if hasattr(cb, "on_failure"):
                cb.on_failure(reason=reason)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 遥测
    # ------------------------------------------------------------------

    def get_telemetry(self) -> dict:
        """返回 SDK 客户端的累计遥测数据。"""
        avg_lat = 0
        if self._total_success > 0:
            avg_lat = self._total_latency_ms // self._total_success
        return {
            "total_calls": self._total_calls,
            "total_success": self._total_success,
            "avg_latency_ms": avg_lat,
            "backend": "gemini_sdk",
            "sdk_available": self._sdk_available,
        }
