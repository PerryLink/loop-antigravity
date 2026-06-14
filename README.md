# loop-antigravity — Goal-Driven Autonomous Dev Loop for Google Antigravity / Gemini

*A [**Loop Engineering**](https://github.com/PerryLink/loop-everything) autonomous coding loop engine — turn goals into production code.*

> 利用 Gemini 的 1M token 上下文窗口和多模态推理能力，设定一个目标，在 GCP 上自主完成设计、实现、测试、验证的完整闭环。

[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/PerryLink/loop-antigravity)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)


[English](#english) | [中文](#中文)

**This project is an alternative to cloud-agnostic AI coding tools, but specifically optimized for GCP-native autonomous development using Google Antigravity (agy CLI) with Gemini's 1M token context and multimodal reasoning, compiled to a single PyInstaller binary.**

<a name="english"></a>

## English

### Features

- **Dual Backend** — agy CLI subprocess engine and Gemini SDK direct client, auto-selectable via `BackendSelector` with unified `GeminiBackend` protocol
- **Circuit Breaker Protection** — CLOSED/OPEN/HALF_OPEN state machine with configurable failure thresholds and exponential backoff, prevents API fault cascading and quota waste
- **Context Packer** — intelligently packs full codebase into Gemini's 1M token context window, no chunking or RAG needed
- **Billing Tracker** — `BillingTracker` enforces daily/weekly hard caps with per-cycle token/cost accounting
- **Multimodal Handler** — dedicated `MultimodalHandler` processes images (png/jpg/gif/webp), PDFs, audio (mp3/wav), and video (mp4)
- **GCloud Auth** — `GCloudAuth` manages ADC credentials with service account support, ready for Cloud Run and Vertex AI deployment
- **11-Phase Dispatch Engine** — `PhaseDispatcher` drives the autonomous development loop from design through implementation, testing, and verification
- **Crash-Proof Persistence** — `state.json` with atomic writes and schema validation via `StateManager`; session restart picks up from last completed phase

### Quick Start

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

### FAQ

#### Q: Why use loop-antigravity instead of calling the Gemini API directly?

A: loop-antigravity layers production-grade safeguards on top of Gemini that raw API calls lack: a circuit breaker that prevents cascading failures during outages, a billing tracker that enforces hard spending caps, and a context packer that optimizes your 1M token window. The dual-backend architecture (agy CLI or Gemini SDK) gives you deployment flexibility — use the SDK for lightweight scripts, or agy CLI for full GCP-native toolchain access.

#### Q: What happens if the Gemini API has an outage?

A: The circuit breaker opens after 5 consecutive failures (configurable), blocking all API calls for a cooldown period (default 60s). During cooldown, the loop pauses and saves state. After cooldown, it enters HALF_OPEN and sends a single probe request. If the probe succeeds, the breaker closes and the loop resumes. If not, the breaker re-opens with exponential backoff.

#### Q: How much does it cost to run?

A: Costs vary based on project size and token usage. The 1M token context is powerful but can be expensive. loop-antigravity's `BillingTracker` logs per-cycle costs and enforces daily/weekly hard caps (configurable via `--budget-daily` and `--budget-weekly`). For a typical medium-sized project, expect $2-15 per full development loop with Gemini 2.5 Flash.

#### Q: Which backend should I choose — agy CLI or Gemini SDK?

A: **agy CLI** — best for GCP-native workflows, supports full `stream-json` output and Google Antigravity toolchain integration. **Gemini SDK** — best for lightweight usage, simpler dependency footprint, direct `google-generativeai` calls. The `BackendSelector` auto-detects available backends. Override with `--backend agy_cli` or `--backend gemini_sdk`.

### Related Projects

- [loop-everything](https://github.com/PerryLink/loop-everything) — master index & orchestration layer for all loop engines
- [loop-superpowers](https://github.com/PerryLink/loop-superpowers) — pure Skill mini-loops for Claude Code
- [loop-opencode](https://github.com/PerryLink/loop-opencode) — closed-loop driver for OpenCode CLI
- [loop-codex](https://github.com/PerryLink/loop-codex) — dual-channel (JSON-RPC + CDP) driver for Codex Desktop
- [loop-copilot](https://github.com/PerryLink/loop-copilot) — closed-loop driver for GitHub Copilot SDK
- [loop-cursor](https://github.com/PerryLink/loop-cursor) — closed-loop driver for Cursor IDE SDK
- [loop-deepseek](https://github.com/PerryLink/loop-deepseek) — self-built ReAct agent loop for DeepSeek API
- [loop-ollama](https://github.com/PerryLink/loop-ollama) — self-built ReAct agent loop for local Ollama models
- [loop-openclaw](https://github.com/PerryLink/loop-openclaw) — multi-agent loop config generator for OpenClaw Gateway
- [loop-aider](https://github.com/PerryLink/loop-aider) — closed-loop steering layer for Aider AI coding engine
- [loop-claudecode](https://github.com/PerryLink/loop-claudecode) — goal-driven autonomous development closed-loop for Claude Code
- [loop-hermes](https://github.com/PerryLink/loop-hermes) — autonomous coding loop wrapping Hermes SDK


<a name="中文"></a>

## 中文

### 项目简介

**loop-antigravity** 封装 Gemini API（agy CLI + Gemini SDK 双后端），内置熔断器、上下文打包、用量追踪和多模态处理，为成本可控的自主编码优化。不同于通用 AI 编码工具，本项目专为 GCP 原生自主开发优化，充分利用 Google Antigravity (agy CLI) 与 Gemini 的 1M token 上下文及多模态推理能力，编译为单一 PyInstaller 二进制文件。

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
# 环境要求: Python >= 3.11, agy CLI 已安装, GCP 项目已配置
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

环境要求: Python >= 3.11, Google Cloud SDK, `agy` CLI 在 PATH 中, GCP 项目已启用 Gemini API。

### 常见问题

#### Q: 为什么要用 loop-antigravity 而不是直接调用 Gemini API？

A: loop-antigravity 在 Gemini 之上增加了原生 API 调用所缺乏的生产级安全保障：断路器可防止宕机期间的级联故障，用量追踪器强制执行硬性支出上限，上下文打包器优化你的 1M token 窗口。双后端架构（agy CLI 或 Gemini SDK）为你提供部署灵活性——用 SDK 处理轻量脚本，或用 agy CLI 获取完整的 GCP 原生工具链。

#### Q: 如果 Gemini API 宕机了会发生什么？

A: 断路器在连续 5 次故障后打开（可配置），在冷却期（默认 60s）内阻止所有 API 调用。冷却期间，循环暂停并保存状态。冷却结束后进入 HALF_OPEN 状态并发送单个探测请求。如果探测成功，断路器关闭，循环恢复。如果失败，断路器重新打开并采用指数退避策略。

#### Q: 运行成本是多少？

A: 成本因项目大小和 token 用量而异。1M token 上下文功能强大但可能费用较高。loop-antigravity 的 `BillingTracker` 记录每周期成本并强制执行每日/每周硬上限（可通过 `--budget-daily` 和 `--budget-weekly` 配置）。对于一个典型的中型项目，使用 Gemini 2.5 Flash 每次完整开发循环预计花费 $2-15。

#### Q: 应该选择哪个后端——agy CLI 还是 Gemini SDK？

A: **agy CLI** — 最适合 GCP 原生工作流，支持完整 `stream-json` 输出和 Google Antigravity 工具链集成。**Gemini SDK** — 最适合轻量使用，依赖更少，直接调用 `google-generativeai`。`BackendSelector` 自动检测可用后端。可通过 `--backend agy_cli` 或 `--backend gemini_sdk` 手动指定。

### 相关项目

- [loop-everything](https://github.com/PerryLink/loop-everything) — 所有 loop 引擎的总索引和编排层
- [loop-superpowers](https://github.com/PerryLink/loop-superpowers) — Claude Code 的纯 Skill 迷你循环
- [loop-opencode](https://github.com/PerryLink/loop-opencode) — OpenCode CLI 的闭环驱动
- [loop-codex](https://github.com/PerryLink/loop-codex) — Codex Desktop 的双通道（JSON-RPC + CDP）驱动
- [loop-copilot](https://github.com/PerryLink/loop-copilot) — GitHub Copilot SDK 的闭环驱动
- [loop-cursor](https://github.com/PerryLink/loop-cursor) — Cursor IDE SDK 的闭环驱动
- [loop-deepseek](https://github.com/PerryLink/loop-deepseek) — DeepSeek API 的自建 ReAct 代理循环
- [loop-ollama](https://github.com/PerryLink/loop-ollama) — 本地 Ollama 模型的自建 ReAct 代理循环
- [loop-openclaw](https://github.com/PerryLink/loop-openclaw) — OpenClaw Gateway 的多代理循环配置生成器
- [loop-aider](https://github.com/PerryLink/loop-aider) — Aider AI 编码引擎的闭环控制层
- [loop-claudecode](https://github.com/PerryLink/loop-claudecode) — Claude Code 的目标驱动自主开发闭环
- [loop-hermes](https://github.com/PerryLink/loop-hermes) — 封装 Hermes SDK 的自主编码循环


---

## License

Apache License 2.0 — see [LICENSE](./LICENSE) for full text.

Copyright 2026 Perry Link
