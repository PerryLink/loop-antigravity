# Security Policy / 安全策略

Copyright (c) 2026 Perry Link. All rights reserved.

Contact: novelnexusai@outlook.com | GitHub: [PerryLink](https://github.com/PerryLink)

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

---

## Supported Versions / 支持的版本

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |

---

## Gemini API Security Model / Gemini API 安全模型

loop-antigravity interacts with Google's Gemini API through two backends:

loop-antigravity 通过两种后端与 Google Gemini API 交互：

### 1. agy CLI Backend (Primary / 主后端)

- All API calls are routed through the `agy` CLI tool, which manages its own
  authentication and session lifecycle.
- 所有 API 调用均通过 `agy` CLI 工具路由，该工具自行管理认证和会话生命周期。
- The `--non-interactive` and `--yolo` flags ensure unattended operation without
  human-in-the-loop intervention.
- `--non-interactive` 和 `--yolo` 标志确保无人值守运行，无需人工介入。
- Stream-json output is parsed line-by-line; malformed or injected JSON lines
  are silently discarded to prevent prompt injection via CLI output.
- Stream-json 输出逐行解析；畸形或注入的 JSON 行会被静默丢弃，防止通过 CLI 输出进行提示注入。

### 2. Gemini SDK Backend (Fallback / 回退后端)

- Uses the `google-generativeai` Python SDK with Application Default
  Credentials (ADC) or explicit API keys.
- 使用 `google-generativeai` Python SDK，配合应用默认凭据 (ADC) 或显式 API 密钥。
- All SDK calls respect the Gemini safety settings configured via
  `safety_settings` in the request payload.
- 所有 SDK 调用均遵循请求负载中通过 `safety_settings` 配置的 Gemini 安全设置。
- Content safety filters run server-side; the client treats `stop_reason="safety"`
  as a terminal error and does not retry.
- 内容安全过滤器在服务端运行；客户端将 `stop_reason="safety"` 视为终止性错误，不进行重试。

### Data Handling / 数据处理

- **No persistent logging of prompts or responses.** Telemetry captures only
  aggregated token counts and latency, never the content of requests or responses.
- **不持久记录提示词或响应内容。** 遥测仅捕获聚合的 token 计数和延迟，绝不记录请求或响应的内容。
- Media inputs (images, PDFs, audio, video) are base64-encoded in-memory for
  the duration of a single API call and never written to disk.
- 媒体输入（图像、PDF、音频、视频）在单次 API 调用期间在内存中进行 base64 编码，绝不写入磁盘。
- Context files are packed with token budget enforcement to prevent
  accidental exfiltration of large codebases.
- 上下文文件打包时强制执行 token 预算，防止意外泄露大型代码库。

---

## API Key Protection / API 密钥保护

### Credential Resolution Chain / 凭据解析链

loop-antigravity resolves credentials in the following order (first match wins):

loop-antigravity 按以下顺序解析凭据（先匹配者优先）：

1. **Environment variable** `GOOGLE_API_KEY` -- highest priority, never logged.
   **环境变量** `GOOGLE_API_KEY` -- 最高优先级，绝不记录到日志。
2. **Service account JSON key file** specified by `GOOGLE_APPLICATION_CREDENTIALS`.
   **服务账号 JSON 密钥文件** 由 `GOOGLE_APPLICATION_CREDENTIALS` 指定。
3. **gcloud ADC** (`gcloud auth application-default login`).
4. **GCP metadata server** (only when running on GCP).
   **GCP 元数据服务器**（仅在 GCP 上运行时）。
5. **API key fallback** -- least preferred; use only for local development.
   **API 密钥回退** -- 最不推荐；仅用于本地开发。

### Best Practices / 最佳实践

- **Never hardcode API keys** in source code or configuration files committed to version control.
  **绝不**在源代码或提交到版本控制的配置文件中硬编码 API 密钥。
- Use `.env` files (added to `.gitignore`) for local development.
  本地开发使用 `.env` 文件（已加入 `.gitignore`）。
- Rotate API keys regularly and revoke leaked keys immediately via Google Cloud Console.
  定期轮换 API 密钥，并通过 Google Cloud Console 立即撤销已泄露的密钥。
- Use service accounts with least-privilege IAM roles in production.
  生产环境使用具有最小权限 IAM 角色的服务账号。
- The `CircuitBreaker` treats `AUTH_ERROR` as a non-counted failure --
  authentication errors require manual remediation and are never retried.
  `CircuitBreaker` 将 `AUTH_ERROR` 视为不计入的失败 -- 认证错误需要手动修复，绝不重试。

### What to Do If a Key Is Leaked / 密钥泄露后的处理

1. **Revoke the key immediately** via [Google Cloud Console > APIs & Services > Credentials](https://console.cloud.google.com/apis/credentials).
   通过 [Google Cloud Console > APIs & Services > Credentials](https://console.cloud.google.com/apis/credentials) **立即撤销密钥**。
2. Rotate all credentials that shared the same IAM scope.
   轮换所有共享相同 IAM 范围的凭据。
3. Audit Cloud Billing for unauthorized usage.
   审计 Cloud Billing 中的未授权使用。
4. Report the incident following the process below.
   按照下述流程报告事件。

---

## Reporting a Vulnerability / 报告安全漏洞

**Please do NOT file public GitHub issues for security vulnerabilities.**

**请不要为安全漏洞提交公开的 GitHub Issue。**

### Reporting Process / 报告流程

1. **Email** the details to: **novelnexusai@outlook.com**
   将详情通过**电子邮件**发送至：**novelnexusai@outlook.com**

2. Include in your report / 报告中请包含：
   - A clear description of the vulnerability / 漏洞的清晰描述
   - Steps to reproduce (if applicable) / 复现步骤（如适用）
   - Affected version(s) / 受影响的版本
   - Any suggested mitigations (optional) / 任何建议的缓解措施（可选）

3. **Response time / 响应时间：**
   - Initial acknowledgment: within **48 hours** / 初步确认：**48 小时**内
   - Status update: within **5 business days** / 状态更新：**5 个工作日**内
   - Fix timeline: determined case-by-case / 修复时间表：根据具体情况确定

4. **Disclosure policy / 披露政策：**
   - We follow coordinated vulnerability disclosure (CVD).
     我们遵循协调漏洞披露 (CVD)。
   - Please allow up to **90 days** for a fix before public disclosure.
     请在公开披露前留出最多 **90 天**的修复时间。
   - Credit will be given in release notes unless you request anonymity.
     除非您要求匿名，否则将在发布说明中致谢。

---

## Security Features in loop-antigravity / loop-antigravity 的安全特性

| Feature / 特性 | Description / 描述 |
|---|---|
| **CircuitBreaker** | Prevents cascading failures; treats auth errors as non-retryable. 防止级联失败；将认证错误视为不可重试。 |
| **Config validation** | JSON Schema validation on all configuration files. 对所有配置文件进行 JSON Schema 验证。 |
| **Token budget** | Hard limits on context size prevent prompt injection via oversized inputs. 上下文大小的硬限制可防止通过超大输入进行提示注入。 |
| **Backend sandboxing** | Subprocess isolation for agy CLI; separate process group to contain failures. agy CLI 的子进程隔离；独立的进程组以遏制故障。 |
| **Atomic file writes** | State files written via tmp->rename->fsync to prevent corruption. 状态文件通过 tmp->rename->fsync 写入，防止损坏。 |
| **Gitignore-aware packing** | Context packer respects `.gitignore` to avoid sending secrets in context. 上下文打包器遵循 `.gitignore`，避免在上下文中发送敏感信息。 |

---

## Dependency Security / 依赖安全

- CI pipeline runs `bandit` static analysis on every push (Stage 6 -- Security audit).
  CI 管道在每次推送时运行 `bandit` 静态分析（第 6 阶段 -- 安全审计）。
- All dependencies are pinned with minimum version constraints in `pyproject.toml`.
  所有依赖在 `pyproject.toml` 中使用最低版本约束进行锁定。
- Report vulnerable dependencies by email (same process as vulnerability reporting above).
  通过电子邮件报告存在漏洞的依赖（流程与上述漏洞报告相同）。
