"""
backend_protocol.py -- GeminiBackend Protocol 接口定义。

定义 AgyClient 和 GeminiSdkClient 必须实现的统一接口。
上层模块（PhaseDispatcher、ContextPacker 等）仅依赖此 Protocol，
不关心底层是 agy CLI 还是 Gemini SDK。

规范来源: DESIGN.md Section 4 核心接口 API 签名
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ============================================================================
# Data types
# ============================================================================

@dataclass
class MediaInput:
    """Multimodal 媒体输入描述符。

    由 MultimodalHandler.detect() 返回，传递给 GeminiBackend.invoke()。
    """
    path: str
    mime_type: str
    media_type: str       # "image" | "pdf" | "audio" | "video" | "text"
    size_bytes: int
    use_file_api: bool = False  # >=20MB 使用 File API 而非 base64 inline


@dataclass
class GeminiResult:
    """统一的 Gemini API 调用结果。

    AgyClient 和 GeminiSdkClient 的 invoke() 均返回此类型。
    """
    text: str = ""
    usage: dict | None = None           # {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}
    stop_reason: str = "end_turn"        # "end_turn" | "max_tokens" | "safety" | "tool_use" | "error"
    tool_calls: list[dict] = field(default_factory=list)
    reasoning: str | None = None         # Gemini thinking/reasoning 文本（若模型支持）
    duration_ms: int = 0                 # 调用耗时（毫秒）
    raw_response: object = None          # 原始响应对象（调试用）


@dataclass
class HealthStatus:
    """后端健康检查结果。"""
    ok: bool = False
    authenticated: bool = False
    version: str = ""
    backend_type: str = ""               # "agy_cli" | "gemini_sdk"
    message: str = ""
    checked_at: str = ""                 # ISO 8601 timestamp


@dataclass
class QuotaStatus:
    """配额/速率限制状态。"""
    available: bool = True
    remaining: int = -1                  # -1 表示未知
    limit: int = -1
    reset_at: str = ""                   # ISO 8601 timestamp
    message: str = ""


# ============================================================================
# Protocol
# ============================================================================

@runtime_checkable
class GeminiBackend(Protocol):
    """统一的 Gemini 后端接口协议。

    AgyClient 和 GeminiSdkClient 均实现此协议。
    上层模块只依赖此协议，不关心底层实现。

    规范: DESIGN.md Section 4 (1) -- GeminiBackend Protocol
    """

    @property
    def backend_type(self) -> str:
        """返回后端类型标识: "agy_cli" | "gemini_sdk"."""
        ...

    def invoke(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        context_files: list[str] | None = None,
        media_inputs: list[MediaInput] | None = None,
        model: str = "gemini-2.5-flash",
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
        timeout_ms: int = 300_000,
    ) -> GeminiResult:
        """调用 Gemini API 并返回统一结果。

        Args:
            prompt: 用户/agent 提示词文本。
            system_prompt: 系统指令（Gemini system_instruction）。
            context_files: 要包含在上下文中的文件路径列表。
            media_inputs: Multimodal 媒体输入（图像/PDF/音频/视频）。
            model: 模型标识符。
            temperature: 采样温度 0.0-2.0。
            max_output_tokens: 输出 token 上限。
            timeout_ms: 调用超时（毫秒）。

        Returns:
            GeminiResult 统一结果对象。
        """
        ...

    def check_health(self) -> HealthStatus:
        """检查后端健康状态。

        验证:
          - CLI/SDK 可执行文件/包存在
          - 认证凭据有效
          - 基本连通性正常

        Returns:
            HealthStatus(ok=True, ...) 或失败状态。
        """
        ...

    def check_quota(self) -> QuotaStatus:
        """查询当前配额/速率限制状态。

        Returns:
            QuotaStatus 反映剩余配额和重置时间。
        """
        ...
