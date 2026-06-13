"""
AgyClient -- agy CLI 子进程管理器。

管理 agy CLI 的完整子进程生命周期:
    - 子进程 spawn/monitor/kill
    - stdin/stdout/stderr 管道处理
    - 超时 SIGTERM -> SIGKILL 升级
    - stream-json 行级解析状态机
    - CircuitBreaker 集成
    - 指数退避重试逻辑
    - 错误分类与结构化异常体系
    - 健康检查与配额检查

定价 (Gemini 2.5 Flash, 2026-06):
    输入:  $0.00015 / 1K tokens
    输出:  $0.0006  / 1K tokens
"""

from __future__ import annotations

import json
import math
import os
import random
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional


# ============================================================================
# 数据类
# ============================================================================


@dataclass
class AgyResult:
    """单次 agy CLI 调用的结构化结果。

    Attributes:
        text: 完整的响应文本（来自所有 text 事件的聚合）。
        tokens_input: 输入 token 消耗数。
        tokens_output: 输出 token 消耗数。
        tokens_total: 总 token 数。
        model: 实际使用的模型，如 "gemini-2.5-flash"。
        finish_reason: 结束原因 -- "STOP" | "MAX_TOKENS" | "SAFETY" 等。
        stream_events: 原始 stream-json 事件列表（调试用）。
        latency_ms: 子进程总耗时（毫秒）。
        cost_estimate_usd: 基于 token 计数和定价估算的成本。
        backend_used: 始终为 "agy_cli"。
        agy_version: agy CLI 版本字符串。
        exit_code: 子进程退出码。
        stderr_output: 捕获的 stderr 输出（诊断用）。
        retry_count: 得到此结果前的重试次数。
        command_line: 执行的完整 agy CLI 命令。
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
    backend_used: str = "agy_cli"
    agy_version: str = ""
    exit_code: int = -1
    stderr_output: str = ""
    retry_count: int = 0
    command_line: str = ""

    @property
    def ok(self) -> bool:
        """True 表示获得了带文本内容的成功结果。"""
        return bool(self.text) and self.finish_reason in ("STOP", "MAX_TOKENS")


@dataclass
class HealthStatus:
    """后端健康检查结果。

    Attributes:
        ok: 是否健康。
        backend_type: 后端类型，始终为 "agy_cli"。
        version: agy CLI 版本字符串。
        authenticated: 是否已认证。
        model_available: 模型是否可用。
        flags_supported: 关键标志是否支持。
        message: 人类可读的状态消息。
        checked_at: 检查时的 ISO 时间戳。
        latency_ms: 检查耗时（毫秒）。
    """
    ok: bool = False
    backend_type: str = "agy_cli"
    version: Optional[str] = None
    authenticated: bool = False
    model_available: bool = False
    flags_supported: dict = field(default_factory=lambda: {
        "--non-interactive": False,
        "--output-format stream-json": False,
        "--yolo": False,
    })
    message: str = ""
    checked_at: str = ""
    latency_ms: int = 0

    def __post_init__(self):
        if not self.checked_at:
            self.checked_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )


@dataclass
class QuotaStatus:
    """配额/限流状态（来自 agy CLI 或从错误推断）。

    Attributes:
        available: 配额是否可用。
        rpm_used: 已使用的每分钟请求数。
        rpm_limit: 每分钟请求限制。
        tpd_used: 已使用的每天 token 数。
        tpd_limit: 每天 token 限制。
        status_code: "AVAILABLE" | "WARNING" | "EXHAUSTED" | "UNKNOWN"。
        estimated_recovery_at: 预估恢复时间（ISO 8601）。
        rate_limit_429_count: 本次 429 计数。
        current_5h_window_used_pct: 当前 5 小时窗口使用百分比。
        weekly_cap_used_pct: 每周上限使用百分比。
        raw_response: 原始响应字典。
    """
    available: bool = True
    rpm_used: int = 0
    rpm_limit: int = 0
    tpd_used: int = 0
    tpd_limit: int = 0
    status_code: str = "UNKNOWN"
    estimated_recovery_at: Optional[str] = None
    rate_limit_429_count: int = 0
    current_5h_window_used_pct: float = 0.0
    weekly_cap_used_pct: float = 0.0
    raw_response: dict = field(default_factory=dict)


@dataclass
class MediaInput:
    """Multimodal 输入描述符。

    Attributes:
        path: 磁盘上的文件路径。
        mime_type: MIME 类型，如 "image/png"。
        media_type: "image" | "pdf" | "audio" | "video"。
        size_bytes: 文件大小（字节）。
        use_file_api: 是否需要 Gemini File API 上传。
        file_uri: File API 上传后的 URI。
        encoded_data: base64 编码的内联数据。
    """
    path: str
    mime_type: str
    media_type: str = ""
    size_bytes: int = 0
    use_file_api: bool = False
    file_uri: Optional[str] = None
    encoded_data: Optional[str] = None

    def __post_init__(self):
        if not self.media_type:
            self.media_type = _infer_media_type(self.mime_type)
        if not self.size_bytes and os.path.exists(self.path):
            self.size_bytes = os.path.getsize(self.path)
        if self.size_bytes > 20 * 1024 * 1024:
            self.use_file_api = True


# ============================================================================
# Stream 解析状态机
# ============================================================================


class StreamParsingState(Enum):
    """stream-json 行级解析状态机。

    状态说明:
        IDLE              -- 初始状态或重置后。
        RECEIVING_STATUS  -- 至少收到一个 {"type":"status",...} 事件。
        RECEIVING_TEXT    -- 至少收到一个 {"type":"text",...} 事件。
        COMPLETE          -- 收到 {"type":"status","stage":"complete",...} 或
                             {"type":"usage",...} 表示响应结束。
        ERROR             -- 收到 {"type":"error",...} 或某行解析失败。
    """
    IDLE = auto()
    RECEIVING_STATUS = auto()
    RECEIVING_TEXT = auto()
    COMPLETE = auto()
    ERROR = auto()


# ============================================================================
# 异常体系
# ============================================================================


class AgyError(Exception):
    """AgyClient 所有错误的基础异常。

    Attributes:
        code: 错误码字符串。
        retryable: 是否可通过重试恢复。
    """
    def __init__(self, message: str, *, code: str = "AGY_UNKNOWN",
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AgyNotInstalledError(AgyError):
    """agy CLI 二进制文件在 PATH 中未找到。"""
    def __init__(self, message: str = "agy CLI not found"):
        super().__init__(message, code="AGY_NOT_FOUND", retryable=False)


class AgyAuthError(AgyError):
    """认证失败（403 或凭证问题）。"""
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, code="AGY_AUTH_ERROR", retryable=True)


class AgyQuotaExhausted(AgyError):
    """429 限流超出或配额耗尽。

    Attributes:
        retry_after_seconds: 建议重试等待秒数。
        estimated_recovery_at: 预估配额恢复时间（ISO 8601）。
    """
    def __init__(self, message: str = "Gemini quota exhausted", *,
                 retry_after_seconds: Optional[int] = None,
                 estimated_recovery_at: Optional[str] = None):
        super().__init__(message, code="AGY_QUOTA_EXHAUSTED", retryable=True)
        self.retry_after_seconds = retry_after_seconds
        self.estimated_recovery_at = estimated_recovery_at


class AgyTimeoutError(AgyError):
    """子进程超时（未在 timeout_ms 内完成）。

    Attributes:
        timeout_ms: 超时时间（毫秒）。
    """
    def __init__(self, message: str = "agy CLI subprocess timed out", *,
                 timeout_ms: int = 0):
        super().__init__(message, code="AGY_TIMEOUT", retryable=True)
        self.timeout_ms = timeout_ms


class AgyStreamParseError(AgyError):
    """stream-json 格式错误 -- 某行无法解析为 JSON。

    Attributes:
        line_number: 出错行号。
        raw_line: 原始行内容（截断至 200 字符）。
    """
    def __init__(self, message: str = "Failed to parse stream-json", *,
                 line_number: int = 0, raw_line: str = ""):
        super().__init__(message, code="AGY_STREAM_PARSE_ERROR", retryable=True)
        self.line_number = line_number
        self.raw_line = raw_line[:200]


class AgyCircuitOpenError(AgyError):
    """CircuitBreaker 处于 OPEN 状态 -- 快速失败，未尝试 API 调用。

    Attributes:
        cooldown_remaining_seconds: 冷却剩余秒数。
    """
    def __init__(self, message: str = "Circuit breaker is OPEN", *,
                 cooldown_remaining_seconds: float = 0):
        super().__init__(message, code="AGY_CIRCUIT_OPEN", retryable=True)
        self.cooldown_remaining_seconds = cooldown_remaining_seconds


class AgySubprocessError(AgyError):
    """子进程以非零退出码退出或被信号杀死。

    Attributes:
        exit_code: 退出码。
        signal_num: 信号编号（-1 表示无）。
    """
    def __init__(self, message: str = "agy CLI subprocess error", *,
                 exit_code: int = -1, signal_num: int = -1):
        super().__init__(message, code="AGY_SUBPROCESS_ERROR", retryable=True)
        self.exit_code = exit_code
        self.signal_num = signal_num


class AgyBadRequestError(AgyError):
    """HTTP 400 或格式错误的 prompt -- 不修改 prompt 则不可重试。"""
    def __init__(self, message: str = "Bad request"):
        super().__init__(message, code="AGY_BAD_REQUEST", retryable=False)


# ============================================================================
# 按信任级别的配置
# ============================================================================

_TRUST_CONFIG = {
    "safe": {
        "failure_threshold": 2, "cooldown_seconds": 120,
        "probe_max_retries": 1,
        "retry_base_delay_ms": 2000, "retry_max_delay_ms": 30000,
        "retry_max_attempts": 2,
        "timeout_ms": 600_000,
        "temperature": 0.4, "max_output_tokens": 4096,
    },
    "auto": {
        "failure_threshold": 5, "cooldown_seconds": 30,
        "probe_max_retries": 3,
        "retry_base_delay_ms": 1000, "retry_max_delay_ms": 16000,
        "retry_max_attempts": 5,
        "timeout_ms": 300_000,
        "temperature": 0.7, "max_output_tokens": 8192,
    },
    "unsafe": {
        "failure_threshold": 20, "cooldown_seconds": 5,
        "probe_max_retries": 5,
        "retry_base_delay_ms": 500, "retry_max_delay_ms": 8000,
        "retry_max_attempts": 10,
        "timeout_ms": 600_000,
        "temperature": 1.0, "max_output_tokens": 16384,
    },
    "collaborative": {
        "failure_threshold": 3, "cooldown_seconds": 60,
        "probe_max_retries": 2,
        "retry_base_delay_ms": 1500, "retry_max_delay_ms": 30000,
        "retry_max_attempts": 3,
        "timeout_ms": 600_000,
        "temperature": 0.5, "max_output_tokens": 8192,
    },
}


# ============================================================================
# 辅助函数
# ============================================================================


def _iso_now() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _infer_media_type(mime_type: str) -> str:
    """从 MIME 类型推断媒体大类型。

    Args:
        mime_type: MIME 类型字符串。

    Returns:
        "image", "audio", "video", "pdf", 或 "unknown"。
    """
    mime = mime_type.lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime == "application/pdf":
        return "pdf"
    return "unknown"


# ============================================================================
# AgyClient -- 主类
# ============================================================================


class AgyClient:
    """agy CLI 子进程管理器。

    管理 agy CLI 作为确定性子进程引擎的完整生命周期。
    使用 --non-interactive --output-format stream-json --yolo 标志生成 agy，
    通过状态机逐行解析 stream-json，提取文本和 token 使用量，
    并返回结构化的 AgyResult。

    与 CircuitBreaker 集成用于失败保护，
    并对瞬时错误实现指数退避重试逻辑。

    Attributes:
        mode: 信任级别 -- "safe" | "auto" | "unsafe" | "collaborative"。
        backend_type: 始终返回 "agy_cli"。
    """

    # Gemini 2.5 Flash 定价 (2026-06)
    PRICING_INPUT = 0.00015
    PRICING_OUTPUT = 0.0006
    PRICING_CACHED_INPUT = 0.0000375

    # 子进程超时升级
    _SIGTERM_GRACE_SECONDS = 3.0
    _SUBProcess_POLL_INTERVAL = 0.05

    # stream-json 已知消息类型
    _MSG_TYPES = frozenset({"status", "text", "usage", "error", "meta", "progress"})

    def __init__(self, mode: str, circuit_breaker: Any) -> None:
        """初始化 AgyClient。

        Args:
            mode: 信任级别 -- "safe" (L1)、"auto" (L2 默认)、
                  "unsafe" (L3)、"collaborative" (L1+)。
            circuit_breaker: CircuitBreaker 实例，
                             支持 .state / .on_success() / .on_failure(reason)。

        Raises:
            ValueError: 如果 mode 不是可识别的信任级别。
        """
        if mode not in _TRUST_CONFIG:
            raise ValueError(
                f"未知的 mode '{mode}'，必须是: "
                f"{sorted(_TRUST_CONFIG.keys())}"
            )

        self.mode: str = mode
        self.circuit_breaker: Any = circuit_breaker
        self._cfg: dict = _TRUST_CONFIG[mode]

        # 遥测累加器（推送到 state.json agy_subprocess 段）
        self._total_spawns: int = 0
        self._total_successful_completions: int = 0
        self._total_timeouts: int = 0
        self._total_stream_parse_errors: int = 0
        self._total_latency_ms: int = 0
        self._last_command: Optional[str] = None
        self._last_exit_code: Optional[int] = None
        self._last_stream_json_sample: Optional[list] = None
        self._cached_agy_version: Optional[str] = None

        # 线程安全（每次只允许一个 invoke() 调用）
        self._invoke_lock = threading.Lock()

    @property
    def backend_type(self) -> str:
        """返回 "agy_cli" -- 实现 GeminiBackend 协议。"""
        return "agy_cli"

    # ------------------------------------------------------------------
    # check_health
    # ------------------------------------------------------------------

    def check_health(self) -> HealthStatus:
        """验证 agy CLI 是否已安装、已认证且响应正常。

        按顺序执行:
          1. 检查 agy 二进制文件是否在 PATH 上 (agy --version)。
          2. 验证 --non-interactive 标志是否可用。
          3. 验证 --output-format stream-json 标志是否可用。
          4. 验证 --yolo 标志是否可用。
          5. 运行轻量级功能测试 prompt。
          6. 验证模型可用性。

        Returns:
            HealthStatus，所有检查通过时 ok=True。
        """
        start = time.time()
        status = HealthStatus(checked_at=_iso_now())

        # Step 1: agy --version
        try:
            proc = subprocess.run(
                ["agy", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                status.message = (
                    f"agy --version failed (exit={proc.returncode}): "
                    f"{proc.stderr[:200]}"
                )
                status.latency_ms = int((time.time() - start) * 1000)
                return status
            version_str = proc.stdout.strip() or proc.stderr.strip()
            status.version = version_str
            self._cached_agy_version = version_str
            status.authenticated = True
        except FileNotFoundError:
            status.message = "agy CLI not found on PATH"
            status.latency_ms = int((time.time() - start) * 1000)
            return status
        except subprocess.TimeoutExpired:
            status.message = "agy --version timed out"
            status.latency_ms = int((time.time() - start) * 1000)
            return status

        # Step 2-4: 验证三个关键标志
        flag_result = self._verify_flags()
        status.flags_supported = flag_result["flags"]
        if not flag_result["all_pass"]:
            missing = [f for f, ok in flag_result["flags"].items() if not ok]
            status.message = f"Critical flags not supported: {missing}"
            status.latency_ms = int((time.time() - start) * 1000)
            return status

        # Step 5: 功能测试
        try:
            ping_cmd = [
                "agy", "-p", "Reply with exactly OK and nothing else.",
                "--non-interactive", "--output-format", "stream-json",
                "--yolo", "--model", "gemini-2.5-flash",
            ]
            proc = subprocess.run(
                ping_cmd, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                status.message = (
                    f"Functional test failed (exit={proc.returncode})"
                )
                status.latency_ms = int((time.time() - start) * 1000)
                return status

            has_text = False
            for line in proc.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "error":
                        status.message = (
                            f"Health check error: "
                            f"{obj.get('message', 'unknown')}"
                        )
                        status.latency_ms = int((time.time() - start) * 1000)
                        return status
                    if obj.get("type") == "text" and obj.get("content", "").strip():
                        has_text = True
                        break
                except json.JSONDecodeError:
                    pass

            if not has_text:
                status.message = "No text content in health check response"
                status.latency_ms = int((time.time() - start) * 1000)
                return status

        except subprocess.TimeoutExpired:
            status.message = "Health check functional test timed out"
            status.latency_ms = int((time.time() - start) * 1000)
            return status
        except FileNotFoundError:
            status.message = "agy CLI disappeared during health check"
            status.latency_ms = int((time.time() - start) * 1000)
            return status

        # 全部通过
        status.ok = True
        status.model_available = True
        status.message = f"agy CLI healthy (v{status.version})"
        status.latency_ms = int((time.time() - start) * 1000)
        return status

    def _verify_flags(self) -> dict:
        """验证三个关键 agy CLI 标志。

        直接解决 P0-8（agy CLI 兼容性验证）。
        每个标志用最小 prompt 和严格超时进行测试。
        结果在此 AgyClient 实例的生命周期内缓存。

        Returns:
            包含以下键的字典:
                flags: dict[str, bool] -- 每个标志的通过/失败状态。
                all_pass: bool -- 三个标志是否全部通过。
                detail: str -- 摘要消息。
        """
        flags = {
            "--non-interactive": False,
            "--output-format stream-json": False,
            "--yolo": False,
        }
        model = "gemini-2.5-flash"
        timeout_sec = 15

        prompt = "Reply with exactly: FLAGS_OK"
        cmd = [
            "agy", "-p", prompt,
            "--non-interactive", "--output-format", "stream-json",
            "--yolo", "--model", model,
        ]

        try:
            proc = subprocess.run(
                cmd, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=timeout_sec,
            )
            if proc.returncode == 0:
                lines = [l.strip() for l in proc.stdout.strip().split("\n") if l.strip()]
                json_lines = 0
                text_found = "FLAGS_OK" in proc.stdout
                for line in lines:
                    try:
                        json.loads(line)
                        json_lines += 1
                    except json.JSONDecodeError:
                        continue
                if json_lines > 0 and (text_found or json_lines >= 2):
                    for key in flags:
                        flags[key] = True
                    return {
                        "flags": flags, "all_pass": True,
                        "detail": f"All 3 flags verified ({json_lines} JSON lines)",
                    }

            return {
                "flags": flags, "all_pass": False,
                "detail": (
                    f"Combined flag test failed (exit={proc.returncode}). "
                    f"stderr: {proc.stderr[:200]}"
                ),
            }
        except subprocess.TimeoutExpired:
            return {
                "flags": flags, "all_pass": False,
                "detail": f"Flag verification timed out after {timeout_sec}s",
            }
        except FileNotFoundError:
            return {
                "flags": flags, "all_pass": False,
                "detail": "agy CLI not found during flag verification",
            }

    # ------------------------------------------------------------------
    # check_quota
    # ------------------------------------------------------------------

    def check_quota(self) -> QuotaStatus:
        """查询 agy 的当前配额/限流状态。

        agy CLI 在 stream-json 状态消息中报告配额信息。
        此方法发送一个最小的 "ping" prompt，从响应中提取配额字段。

        如果 ping 本身返回 429，则配额已耗尽。如果 ping 成功，
        则配额可用但可能接近限制。

        Returns:
            反映当前配额状态的 QuotaStatus。
        """
        status = QuotaStatus()
        try:
            result = self.invoke(
                prompt="ping",
                system_prompt="Reply with exactly pong and nothing else.",
                model="gemini-2.5-flash",
                max_output_tokens=16,
                timeout_ms=30000,
            )
            status.available = True
            status.status_code = "AVAILABLE"

            for evt in result.stream_events:
                if evt.get("type") == "status":
                    quota = evt.get("quota", {})
                    if quota:
                        status.rpm_used = quota.get("rpm_used", status.rpm_used)
                        status.rpm_limit = quota.get("rpm_limit", status.rpm_limit)
                        status.tpd_used = quota.get("tpd_used", status.tpd_used)
                        status.tpd_limit = quota.get("tpd_limit", status.tpd_limit)
                        pct = quota.get("current_usage_pct", 0)
                        status.current_5h_window_used_pct = float(pct)
                        if float(pct) > 80:
                            status.status_code = "WARNING"
                if evt.get("type") == "usage":
                    quota = evt.get("quota", {})
                    if quota:
                        status.raw_response = quota

        except AgyQuotaExhausted as e:
            status.available = False
            status.status_code = "EXHAUSTED"
            status.estimated_recovery_at = e.estimated_recovery_at
            status.rate_limit_429_count = 1
        except (AgyCircuitOpenError, AgyTimeoutError):
            status.available = False
            status.status_code = "UNKNOWN"
        except Exception:
            status.available = False
            status.status_code = "UNKNOWN"

        return status

    # ------------------------------------------------------------------
    # _augment_prompt_with_context
    # ------------------------------------------------------------------

    def _augment_prompt_with_context(
        self, prompt: str, context_files: list[str]
    ) -> str:
        """将文本上下文文件前置到 prompt。

        对于文本文件（根据扩展名确定），读取内容并内联到
        主 prompt 之前。媒体文件不在此处理，
        它们通过 MultimodalHandler -> media_inputs 参数传递。

        Args:
            prompt: 原始用户 prompt。
            context_files: 要读取并内联的文件路径列表。

        Returns:
            前置了上下文文件的增强 prompt 字符串。
        """
        if not context_files:
            return prompt

        text_extensions = {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go",
            ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".php",
            ".swift", ".kt", ".scala", ".cs",
            ".sh", ".bash", ".zsh", ".ps1", ".bat",
            ".md", ".rst", ".txt", ".yaml", ".yml", ".json",
            ".toml", ".ini", ".cfg",
            ".xml", ".html", ".css", ".scss", ".less",
            ".sql", ".r", ".jl", ".lua",
            ".gitignore", ".env", ".editorconfig",
        }
        text_extensions.update({e.upper() for e in list(text_extensions)})

        parts: list[str] = []

        for filepath in context_files:
            if not os.path.isfile(filepath):
                parts.append(
                    f"=== {filepath} ===\n[File not found]\n=== END ===\n"
                )
                continue

            ext = os.path.splitext(filepath)[1]
            if ext.lower() not in text_extensions:
                parts.append(
                    f"=== {filepath} ===\n"
                    f"[Non-text file -- included as media input]\n"
                    f"=== END ===\n"
                )
                continue

            try:
                with open(filepath, "r", encoding="utf-8",
                          errors="replace") as f:
                    content = f.read()
                if len(content) > 200 * 1024:
                    truncated_len = len(content)
                    content = content[:200 * 1024]
                    content += (
                        f"\n... [TRUNCATED: "
                        f"{truncated_len - 200 * 1024} bytes omitted]"
                    )
                parts.append(
                    f"=== {filepath} ===\n{content}\n=== END ===\n"
                )
            except Exception as e:
                parts.append(
                    f"=== {filepath} ===\n"
                    f"[Error reading file: {e}]\n=== END ===\n"
                )

        if parts:
            return "\n".join(parts) + "\n\n---\n\n" + prompt
        return prompt


    # ------------------------------------------------------------------
    # _build_command
    # ------------------------------------------------------------------

    def _build_command(
        self,
        prompt: str,
        system_prompt: Optional[str],
        model: str,
        temperature: float,
        max_output_tokens: int,
        media_inputs: list,
        extra_flags: Optional[list[str]],
    ) -> list[str]:
        """构建 agy CLI 命令参数列表。

        关键标志（在启动时由 check_health 验证）:
          --non-interactive: agy 不得提示 stdin 输入。
          --output-format stream-json: stdout 为每行一条 JSON。
          --yolo: 抑制安全确认。

        Args:
            prompt: 完整的（增强的）prompt 文本。
            system_prompt: 系统指令字符串（可选）。
            model: 模型 ID。
            temperature: 采样温度。
            max_output_tokens: 最大输出 token 数。
            media_inputs: 预处理的媒体输入。
            extra_flags: 要透传的额外标志。

        Returns:
            适用于 subprocess.Popen 的命令行参数列表。
        """
        cmd = ["agy"]

        cmd.extend(["-p", prompt])
        cmd.append("--non-interactive")
        cmd.extend(["--output-format", "stream-json"])
        cmd.append("--yolo")

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        cmd.extend(["--model", model])
        cmd.extend(["--temperature", str(temperature)])
        cmd.extend(["--max-output-tokens", str(max_output_tokens)])

        for mi in media_inputs:
            if mi.use_file_api and mi.file_uri:
                cmd.extend(["--file-uri", mi.file_uri])
            elif mi.media_type == "image":
                cmd.extend(["--image", mi.path])
            elif mi.media_type == "pdf" and not mi.use_file_api:
                cmd.extend(["--file", mi.path])
            elif mi.media_type in ("audio", "video") and not mi.use_file_api:
                cmd.extend(["--file", mi.path])

        if extra_flags:
            cmd.extend(extra_flags)

        return cmd

    # ------------------------------------------------------------------
    # _spawn_and_monitor
    # ------------------------------------------------------------------

    def _spawn_and_monitor(
        self, cmd: list[str], timeout_ms: int
    ) -> tuple:
        """生成 agy CLI 子进程并监控其生命周期。

        生命周期:
          1. Popen 使用 stdin=DEVNULL，
             如果 agy 尝试读取 stdin 则 --non-interactive 标志损坏。
          2. stdout 和 stderr 通过 PIPE 捕获。
          3. 在后台线程中逐行读取 stdout 直到 EOF。
          4. 主线程等待最多 timeout_ms 毫秒进程退出。
          5. 超时时先 SIGTERM，再等宽限期，最后 SIGKILL。
          6. 检查退出码。

        Args:
            cmd: agy CLI 命令（参数列表）。
            timeout_ms: 硬超时（毫秒）。

        Returns:
            (stdout_lines, stderr_text, exit_code, elapsed_ms) 元组。

        Raises:
            AgyTimeoutError: 子进程在 timeout_ms 内未完成。
            AgySubprocessError: 子进程非零退出或被信号杀死。
        """
        start = time.time()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        stdout_done = threading.Event()

        def _read_stdout(pipe, lines_out, done_event):
            try:
                for line in iter(pipe.readline, ""):
                    lines_out.append(line)
            except ValueError:
                pass
            finally:
                done_event.set()
                try:
                    pipe.close()
                except Exception:
                    pass

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            reader_thread = threading.Thread(
                target=_read_stdout,
                args=(proc.stdout, stdout_lines, stdout_done),
                daemon=True,
            )
            reader_thread.start()

            timeout_sec = timeout_ms / 1000.0

            try:
                proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                self._send_signal(proc, signal.SIGTERM)
                try:
                    proc.wait(timeout=self._SIGTERM_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    self._send_signal(proc, signal.SIGKILL)
                    try:
                        proc.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2.0)

                try:
                    remaining_stderr = proc.stderr.read()
                    if remaining_stderr:
                        stderr_lines.append(remaining_stderr)
                except Exception:
                    pass

                stdout_done.wait(timeout=2.0)

                elapsed_ms = int((time.time() - start) * 1000)
                raise AgyTimeoutError(
                    f"agy CLI subprocess timed out after {elapsed_ms}ms (limit={timeout_ms}ms)",
                    timeout_ms=timeout_ms,
                )

            stdout_done.wait(timeout=2.0)

            try:
                remaining_stderr = proc.stderr.read()
                if remaining_stderr:
                    stderr_lines.append(remaining_stderr)
            except Exception:
                pass

            exit_code = proc.returncode
            stderr_text = "".join(stderr_lines)
            elapsed_ms = int((time.time() - start) * 1000)

            if exit_code == 0:
                return (stdout_lines, stderr_text, exit_code, elapsed_ms)

            if exit_code < 0:
                sig_num = -exit_code
                raise AgySubprocessError(
                    f"agy CLI killed by signal {sig_num}",
                    exit_code=exit_code, signal_num=sig_num,
                )
            else:
                raise AgySubprocessError(
                    f"agy CLI exited with code {exit_code}. stderr: {stderr_text[:500]}",
                    exit_code=exit_code,
                )

        except FileNotFoundError:
            raise AgyNotInstalledError(
                "agy CLI not found. Install: pip install google-antigravity"
            )
        except (AgyTimeoutError, AgySubprocessError, AgyNotInstalledError):
            raise
        except Exception as e:
            raise AgySubprocessError(
                f"Unexpected subprocess error: {e}", exit_code=-999,
            )

    @staticmethod
    def _send_signal(proc, sig: int) -> None:
        """向子进程发送信号。如果进程已死则抑制错误。"""
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # _parse_stream_json
    # ------------------------------------------------------------------

    def _parse_stream_json(self, stdout_lines: list[str]) -> list[dict]:
        """通过状态机逐行解析 stream-json 输出。

        状态转换:
            IDLE -> RECEIVING_STATUS (first status) | RECEIVING_TEXT (first text) | ERROR
            RECEIVING_STATUS -> RECEIVING_TEXT | COMPLETE | ERROR
            RECEIVING_TEXT -> COMPLETE | ERROR
            COMPLETE (terminal), ERROR (terminal)

        Args:
            stdout_lines: 来自子进程的原始 stdout 行。

        Returns:
            已解析的 JSON 事件字典列表。

        Raises:
            AgyStreamParseError: 如果某不可跳过的行解析失败或遇到错误事件。
        """
        state = StreamParsingState.IDLE
        events: list[dict] = []
        parse_errors: list = []

        for i, raw_line in enumerate(stdout_lines):
            line_no = i + 1
            stripped = raw_line.strip()
            if not stripped:
                continue

            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                if "{" in stripped and "}" in stripped:
                    parse_errors.append((line_no, stripped, str(e)))
                    if state != StreamParsingState.ERROR:
                        state = StreamParsingState.ERROR
                    continue
                obj = {"type": "text", "content": stripped, "_synthetic": True}

            msg_type = obj.get("type", "unknown")

            if msg_type == "error":
                state = StreamParsingState.ERROR
                events.append(obj)
                continue

            if msg_type not in self._MSG_TYPES and msg_type != "unknown":
                obj["_unrecognized_type"] = True

            if state == StreamParsingState.IDLE:
                if msg_type == "status":
                    state = StreamParsingState.RECEIVING_STATUS
                elif msg_type == "text":
                    state = StreamParsingState.RECEIVING_TEXT
                elif msg_type == "usage":
                    state = StreamParsingState.COMPLETE

            elif state == StreamParsingState.RECEIVING_STATUS:
                if msg_type == "text":
                    state = StreamParsingState.RECEIVING_TEXT
                elif msg_type == "status" and obj.get("stage") == "complete":
                    state = StreamParsingState.COMPLETE
                elif msg_type == "usage":
                    state = StreamParsingState.COMPLETE

            elif state == StreamParsingState.RECEIVING_TEXT:
                if msg_type == "status" and obj.get("stage") == "complete":
                    state = StreamParsingState.COMPLETE
                elif msg_type == "usage":
                    state = StreamParsingState.COMPLETE

            events.append(obj)

        if parse_errors:
            first = parse_errors[0]
            raise AgyStreamParseError(
                f"stream-json parse error at line {first[0]}: {first[2]}",
                line_number=first[0], raw_line=first[1],
            )

        if state == StreamParsingState.ERROR:
            error_evt = self._find_error_event(events)
            if error_evt:
                raise AgyStreamParseError(
                    f"stream-json error event: {error_evt.get('message', 'unknown')}",
                    line_number=0,
                )

        return events

    # ------------------------------------------------------------------
    # _extract_text
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(events: list[dict]) -> str:
        """从所有 text 事件聚合文本内容。

        Args:
            events: 已解析的 stream-json 事件。

        Returns:
            完整的响应文本字符串。
        """
        parts: list[str] = []
        for evt in events:
            if evt.get("type") == "text":
                content = evt.get("content", "")
                if content:
                    parts.append(content)
            elif evt.get("type") == "status":
                content = evt.get("content") or evt.get("text") or ""
                if content and len(content) > 5:
                    parts.append(content)
        return "".join(parts)

    # ------------------------------------------------------------------
    # _extract_tokens
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tokens(events: list[dict]) -> tuple:
        """从 stream-json 事件提取 token 使用数据。

        查找:
          - {"type":"usage","input_tokens":N,"output_tokens":N,...}
          - {"type":"status","stage":"complete","output_tokens":N}
          - {"type":"status","tokens":{"input":N,"output":N}}

        "usage" 事件是权威来源，status 事件补充之。

        Args:
            events: 已解析的 stream-json 事件。

        Returns:
            (input_tokens, output_tokens, total_tokens) 元组。
        """
        input_tokens = 0
        output_tokens = 0
        total_from_usage = 0

        for evt in events:
            if evt.get("type") == "usage":
                in_tok = int(evt.get("input_tokens", 0))
                out_tok = int(evt.get("output_tokens", 0))
                tot = int(evt.get("total_tokens", 0))
                if in_tok > 0:
                    input_tokens = max(input_tokens, in_tok)
                if out_tok > 0:
                    output_tokens = max(output_tokens, out_tok)
                if tot > 0:
                    total_from_usage = tot

            elif evt.get("type") == "status":
                in_tok = int(evt.get("input_tokens", 0))
                out_tok = int(evt.get("output_tokens", 0))

                tokens_obj = evt.get("tokens", {})
                if isinstance(tokens_obj, dict):
                    in_tok = max(in_tok, int(tokens_obj.get("input", 0)))
                    out_tok = max(out_tok, int(tokens_obj.get("output", 0)))

                if in_tok > 0:
                    input_tokens = max(input_tokens, in_tok)
                if out_tok > 0:
                    output_tokens = max(output_tokens, out_tok)

        if total_from_usage > 0 and input_tokens == 0:
            input_tokens = total_from_usage - output_tokens

        total_tokens = input_tokens + output_tokens
        if total_from_usage > 0:
            total_tokens = max(total_tokens, total_from_usage)

        return (input_tokens, output_tokens, total_tokens)

    # ------------------------------------------------------------------
    # _extract_model_and_finish
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_model_and_finish(
        events: list[dict], default_model: str
    ) -> tuple:
        """从事件中提取实际使用的模型和结束原因。

        Args:
            events: 已解析的 stream-json 事件。
            default_model: 事件中未找到时的回退模型名。

        Returns:
            (model_name, finish_reason) 元组。
        """
        model = default_model
        finish_reason = "UNKNOWN"

        for evt in events:
            if evt.get("type") == "status":
                if "model" in evt:
                    model = evt["model"]
                if evt.get("stage") == "complete":
                    fr = evt.get("finish_reason", "")
                    if fr:
                        finish_reason = fr.upper()
            elif evt.get("type") == "usage":
                if "model" in evt:
                    model = evt["model"]
                fr = evt.get("finish_reason", "")
                if fr:
                    finish_reason = fr.upper()

        return (model, finish_reason)

    # ------------------------------------------------------------------
    # _find_error_event
    # ------------------------------------------------------------------

    @staticmethod
    def _find_error_event(events: list[dict]) -> Optional[dict]:
        """在 stream 中查找第一个错误事件。"""
        for evt in events:
            if evt.get("type") == "error":
                return evt
        return None

    # ------------------------------------------------------------------
    # _calculate_cost
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_cost(input_tokens: int, output_tokens: int) -> float:
        """根据 token 计数计算估算的 USD 成本。

        使用 Gemini 2.5 Flash 定价 (2026-06):
          输入:  $0.00015 / 1K tokens
          输出:  $0.0006  / 1K tokens

        Args:
            input_tokens: 输入（prompt）token 数。
            output_tokens: 输出（completion）token 数。

        Returns:
            估算成本（美元）。
        """
        input_cost = (input_tokens / 1000.0) * AgyClient.PRICING_INPUT
        output_cost = (output_tokens / 1000.0) * AgyClient.PRICING_OUTPUT
        return input_cost + output_cost

    # ------------------------------------------------------------------
    # 重试 / 退避
    # ------------------------------------------------------------------

    @staticmethod
    def _backoff_delay(attempt: int, base_ms: int, max_ms: int) -> int:
        """计算带抖动的指数退避延迟。

        公式: min(max_ms, base_ms * 2^attempt) + random jitter (0-25%)

        按信任级别缩放:
          L1 (safe):   base=2000ms, max=30000ms
          L2 (auto):   base=1000ms, max=16000ms
          L3 (unsafe): base=500ms,  max=8000ms

        Args:
            attempt: 从零开始的尝试次数。
            base_ms: 基础延迟（毫秒）。
            max_ms: 最大延迟上限（毫秒）。

        Returns:
            延迟时间（毫秒）。
        """
        delay = min(max_ms, base_ms * (2 ** attempt))
        jitter = delay * 0.25 * random.random()
        return int(delay + jitter)

    # ------------------------------------------------------------------
    # CircuitBreaker 集成
    # ------------------------------------------------------------------

    def _read_circuit_state(self) -> str:
        """读取熔断器当前状态。

        Returns:
            "CLOSED"、"OPEN" 或 "HALF_OPEN"。
        """
        try:
            state = getattr(self.circuit_breaker, "state", None)
            if state is not None:
                return str(state.value if hasattr(state, "value") else state)
            return "CLOSED"
        except Exception:
            return "CLOSED"

    def _circuit_cooldown_remaining(self) -> float:
        """计算 HALF_OPEN 转换前的剩余冷却秒数。

        Returns:
            如果熔断器非 OPEN 或冷却已过则返回 0。
        """
        try:
            if self._read_circuit_state() != "OPEN":
                return 0.0
            opened_at_str = getattr(self.circuit_breaker, "opened_at", None)
            if not opened_at_str:
                return 0.0
            from datetime import datetime
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
                try:
                    opened_at = datetime.strptime(opened_at_str, fmt)
                    break
                except ValueError:
                    continue
            else:
                return 0.0
            elapsed = (datetime.utcnow() - opened_at).total_seconds()
            cooldown = float(getattr(
                self.circuit_breaker, "cooldown_seconds", 30
            ))
            return max(0.0, cooldown - elapsed)
        except Exception:
            return 0.0

    def _circuit_transition_to(
        self, target_state: str, *, reason: str = ""
    ) -> None:
        """请求熔断器状态转换。

        Args:
            target_state: "CLOSED"、"OPEN" 或 "HALF_OPEN"。
            reason: 人类可读的转换原因。
        """
        cb = self.circuit_breaker
        try:
            if hasattr(cb, "_transition_to"):
                from loop_antigravity.circuit_breaker import CircuitState
                cb._transition_to(CircuitState(target_state))
            elif hasattr(cb, "state"):
                from loop_antigravity.circuit_breaker import CircuitState
                cb._state = CircuitState(target_state)
        except Exception:
            pass

    def _record_success(self) -> None:
        """向熔断器报告成功 API 调用。"""
        cb = self.circuit_breaker
        try:
            if hasattr(cb, "on_success"):
                cb.on_success()
            elif hasattr(cb, "report_success"):
                cb.report_success()
        except Exception:
            pass

    def _record_failure(self, reason: str) -> None:
        """向熔断器报告失败 API 调用。

        递增 consecutive_failures。如果达到阈值，
        熔断器从 CLOSED/HALF_OPEN 转到 OPEN。

        Args:
            reason: 人类可读的失败原因。
        """
        cb = self.circuit_breaker
        try:
            if hasattr(cb, "on_failure"):
                cb.on_failure(reason=reason)
            elif hasattr(cb, "report_failure"):
                from loop_antigravity.circuit_breaker import FailureCategory
                cb.report_failure(FailureCategory.UNKNOWN, reason)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 遥测访问器
    # ------------------------------------------------------------------

    def get_telemetry(self) -> dict:
        """返回适合写入 state.json agy_subprocess 的累计遥测数据。"""
        avg_latency = 0
        if self._total_successful_completions > 0:
            avg_latency = (
                self._total_latency_ms // self._total_successful_completions
            )
        return {
            "total_spawns": self._total_spawns,
            "total_successful_completions": self._total_successful_completions,
            "total_timeouts": self._total_timeouts,
            "total_stream_parse_errors": self._total_stream_parse_errors,
            "avg_response_time_ms": avg_latency,
            "last_command": self._last_command,
            "last_exit_code": self._last_exit_code,
            "last_stream_json_sample": self._last_stream_json_sample,
            "agy_version": self._cached_agy_version,
        }

    def reset_telemetry(self) -> None:
        """重置所有遥测计数器（例如推送到 state.json 后）。"""
        self._total_spawns = 0
        self._total_successful_completions = 0
        self._total_timeouts = 0
        self._total_stream_parse_errors = 0
        self._total_latency_ms = 0
        self._last_command = None
        self._last_exit_code = None
        self._last_stream_json_sample = None
