"""
loop_antigravity 包初始化。

loop-antigravity 是一款基于 Google Antigravity (agy CLI) 的 Python 工具，
利用 Gemini 3.5 Flash 的 1M token 上下文窗口实现目标驱动的自主开发闭环。

Public API:
    cli.main()                  — CLI 入口（init/run/check/status/resume）。
    config.AntigravityConfig    — 全局配置数据类（信任模式、模型选择、超时等）。
    agy_client.AgyClient        — agy CLI 子进程管理器（GeminiBackend 实现）。
    state_manager.StateManager  — state.json 原子读写与 schema 验证。
    circuit_breaker.CircuitBreaker — CLOSED/OPEN/HALF_OPEN 熔断保护。
    gcloud_auth.GCloudAuth      — GCP 认证管理（service account / ADC）。
    backend_protocol.GeminiBackend — 统一后端接口协议（Protocol class）。
    backend_selector.BackendSelector — 后端选择器（agy_cli / gemini_sdk）。
    multimodal_handler.MultimodalHandler — 多模态输入检测与处理。
    gemini_sdk_client.GeminiSdkClient — Gemini SDK 直连客户端（GeminiBackend 实现）。
    phase_dispatcher.PhaseDispatcher — 11 阶段分派引擎。
    context_packer.ContextPacker — 上下文打包器（1M token 窗口优化）。
    billing_tracker.BillingTracker — 计费追踪器（token 消耗统计）。
    verify_agy_flags — agy CLI flags 验证模块（三种模式验证）。
"""

__version__ = "0.1.0"
__author__ = "loop-antigravity"
__description__ = "Goal-driven autonomous development with Gemini 3.5 Flash and 1M context window"
