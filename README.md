# loop-antigravity — Gemini API Wrapper with Circuit Breaker &amp; Multimodal Context Packing

*A [**Loop Engineering**](https://github.com/PerryLink/loop-everything) autonomous coding loop engine — turn goals into production code.*

> 封装 Gemini API 的 Python SDK（agy CLI + Gemini SDK 双后端），内置熔断器、上下文打包、用量追踪和多模态处理，为成本可控的自主编码优化。

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/badge/pypi-loop--antigravity-orange.svg)](https://pypi.org/project/loop-antigravity/)
[![CI](https://github.com/PerryLink/loop-antigravity/actions/workflows/ci.yml/badge.svg)](https://github.com/PerryLink/loop-antigravity/actions)

**This project is an alternative to raw Gemini API, optimized for cost-controlled autonomous coding with circuit breaker and multimodal context packing — wrapping Gemini via a dual-backend architecture (agy CLI subprocess + Gemini SDK direct) compiled to a single PyInstaller binary.**

## Features

- **Dual Backend** — agy CLI subprocess engine and Gemini SDK direct client, auto-selectable via `BackendSelector` with unified `GeminiBackend` protocol
- **Circuit Breaker Protection** — CLOSED/OPEN/HALF_OPEN state machine with configurable failure thresholds and exponential backoff, prevents API fault cascading and quota waste
- **Context Packer** — intelligently packs full codebase into Gemini's 1M token context window, no chunking or RAG needed
- **Billing Tracker** — `BillingTracker` enforces daily/weekly hard caps with per-cycle token/cost accounting
- **Multimodal Handler** — dedicated `MultimodalHandler` processes images (png/jpg/gif/webp), PDFs, audio (mp3/wav), and video (mp4)
- **GCloud Auth** — `GCloudAuth` manages ADC credentials with service account support, ready for Cloud Run and Vertex AI deployment
- **11-Phase Dispatch Engine** — `PhaseDispatcher` drives the autonomous development loop from design through implementation, testing, and verification
- **Crash-Proof Persistence** — `state.json` with atomic writes and schema validation via `StateManager`; session restart picks up from last completed phase

## Quick Start

```bash
# Prerequisites: Python >= 3.11, agy CLI installed, GCP project configured
pip install loop-antigravity

# Or clone source
git clone https://github.com/PerryLink/loop-antigravity.git
cd loop-antigravity
pip install -r requirements.txt

# Authenticate with GCP
gcloud auth application-default login

# Run with a goal
loop-antigravity run --goal "Build a REST API with FastAPI"

# Check dependencies and health
loop-antigravity --check

# Choose operation mode
loop-antigravity --safe    # L1 Shield: conservative breaker, low billing cap
loop-antigravity --auto    # L2 Standard: balanced breaker and billing (default)
loop-antigravity --unsafe  # L3 Unlimited: loosest breaker, for trusted sandboxes only
```

Requirements: Python >= 3.11, Google Cloud SDK, `agy` CLI in PATH, GCP project with Gemini API enabled.

## FAQ

### Q: Why use loop-antigravity instead of calling the Gemini API directly?

A: loop-antigravity layers production-grade safeguards on top of Gemini that raw API calls lack: a circuit breaker that prevents cascading failures during outages, a billing tracker that enforces hard spending caps, and a context packer that optimizes your 1M token window. The dual-backend architecture (agy CLI or Gemini SDK) gives you deployment flexibility — use the SDK for lightweight scripts, or agy CLI for full GCP-native toolchain access.

### Q: What happens if the Gemini API has an outage?

A: The circuit breaker opens after 5 consecutive failures (configurable), blocking all API calls for a cooldown period (default 60s). During cooldown, the loop pauses and saves state. After cooldown, it enters HALF_OPEN and sends a single probe request. If the probe succeeds, the breaker closes and the loop resumes. If not, the breaker re-opens with exponential backoff.

### Q: How much does it cost to run?

A: Costs vary based on project size and token usage. The 1M token context is powerful but can be expensive. loop-antigravity's `BillingTracker` logs per-cycle costs and enforces daily/weekly hard caps (configurable via `--budget-daily` and `--budget-weekly`). For a typical medium-sized project, expect $2-15 per full development loop with Gemini 2.5 Flash.

### Q: Which backend should I choose — agy CLI or Gemini SDK?

A: **agy CLI** — best for GCP-native workflows, supports full `stream-json` output and Google Antigravity toolchain integration. **Gemini SDK** — best for lightweight usage, simpler dependency footprint, direct `google-generativeai` calls. The `BackendSelector` auto-detects available backends. Override with `--backend agy_cli` or `--backend gemini_sdk`.

## 中文文档 / Chinese Docs

**loop-antigravity** 封装 Gemini API（agy CLI + Gemini SDK 双后端），内置熔断器、上下文打包、用量追踪和多模态处理，为成本可控的自主编码优化。

### 功能特性

- 🔀 **双后端架构** — agy CLI 子进程引擎 和 Gemini SDK 直连客户端，通过 `BackendSelector` 自动选择，统一 `GeminiBackend` 协议
- ⚡ **断路器保护** — CLOSED/OPEN/HALF_OPEN 状态机，可配置故障阈值和指数退避，防止 API 故障级联和配额浪费
- 📦 **上下文打包器** — `ContextPacker` 智能将完整代码库打包进 Gemini 1M token 上下文窗口，无需分块或 RAG
- 💰 **用量追踪** — `BillingTracker` 强制执行每日/每周硬上限，含每周期 token/成本核算
- 🎨 **多模态处理器** — 专用 `MultimodalHandler` 处理图片（png/jpg/gif/webp）、PDF、音频（mp3/wav）、视频（mp4）
- ☁️ **GCP 认证** — `GCloudAuth` 管理 ADC 凭证，支持服务账号，可直接部署到 Cloud Run 和 Vertex AI
- 🔄 **11 阶段分派引擎** — `PhaseDispatcher` 驱动自主开发闭环，从设计到实现、测试、验证
- 💾 **崩溃恢复** — `state.json` 原子写入与 schema 校验，会话重启从最后完成的阶段继续

### 快速开始

```bash
# 安装
pip install loop-antigravity

# 或克隆源码
git clone https://github.com/PerryLink/loop-antigravity.git
cd loop-antigravity
pip install -r requirements.txt

# GCP 认证
gcloud auth application-default login

# 设定目标运行
loop-antigravity run --goal "用 FastAPI 构建 REST API"

# 检查依赖和健康状态
loop-antigravity --check

# 选择操作模式
loop-antigravity --safe    # L1 Shield：保守熔断，低计费上限
loop-antigravity --auto    # L2 Standard：平衡熔断和计费（默认）
loop-antigravity --unsafe  # L3 Unlimited：最宽松熔断，仅可信沙箱使用
```

---

## Related Projects

- [loop-everything](https://github.com/PerryLink/loop-everything) — master index & orchestration layer for all 11 loop engines
- [loop-aider](https://github.com/PerryLink/loop-aider) — closed-loop steering layer for Aider AI coding engine
- [loop-claudecode](https://github.com/PerryLink/loop-claudecode) — goal-driven autonomous development closed-loop for Claude Code
- [loop-codex](https://github.com/PerryLink/loop-codex) — dual-channel (JSON-RPC + CDP) driver for Codex Desktop
- [loop-copilot](https://github.com/PerryLink/loop-copilot) — closed-loop driver for GitHub Copilot SDK
- [loop-cursor](https://github.com/PerryLink/loop-cursor) — closed-loop driver for Cursor IDE SDK
- [loop-deepseek](https://github.com/PerryLink/loop-deepseek) — self-built ReAct agent loop for DeepSeek API
- [loop-hermes](https://github.com/PerryLink/loop-hermes) — autonomous coding loop wrapping Hermes SDK
- [loop-ollama](https://github.com/PerryLink/loop-ollama) — self-built ReAct agent loop for local Ollama models
- [loop-openclaw](https://github.com/PerryLink/loop-openclaw) — multi-agent loop config generator for OpenClaw Gateway
- [loop-opencode](https://github.com/PerryLink/loop-opencode) — closed-loop driver for OpenCode CLI
- [loop-superpowers](https://github.com/PerryLink/loop-superpowers) — pure Skill mini-loops for Claude Code

## 完成度声明 / Completeness Declaration

当前完成度：**90%**

已完成模块：双后端架构（agy CLI + Gemini SDK + BackendSelector）、断路器保护（CLOSED/OPEN/HALF_OPEN + 指数退避）、上下文打包器（ContextPacker）、用量追踪（BillingTracker 每日/每周硬上限）、多模态处理器（图片/PDF/音频/视频）、GCP 认证（GCloudAuth ADC + 服务账号）、11 阶段分派引擎（PhaseDispatcher）、状态管理器（state.json 原子写入 + schema 校验）、CLI 入口

待完善：agy CLI 的 Windows 平台适配尚未完成（当前仅 Linux/macOS）；多模态处理器对大文件（>500MB）视频的帧提取逻辑需要优化；断路器 HALF_OPEN 状态下探测请求在边缘场景下可能存在竞态条件

Completion：**90%**

Completed：dual backend (agy CLI + Gemini SDK + BackendSelector), circuit breaker (CLOSED/OPEN/HALF_OPEN + exponential backoff), context packer (ContextPacker), billing tracker (BillingTracker daily/weekly hard caps), multimodal handler (images/PDF/audio/video), GCP auth (GCloudAuth ADC + service account), 11-phase dispatch engine (PhaseDispatcher), state manager (state.json atomic write + schema validation), CLI entry point

Pending：agy CLI Windows platform adaptation not yet complete (Linux/macOS only); multimodal handler video frame extraction for large files (>500MB) needs optimization; circuit breaker HALF_OPEN probe requests may trigger race conditions in certain edge cases

---

## License

Apache License 2.0 — see [LICENSE](./LICENSE) for full text.

Copyright 2026 Perry Link
