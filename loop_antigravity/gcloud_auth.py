"""
GCloudAuth -- GCP ADC 认证与项目检测模块。

管理 Google Cloud 的 Application Default Credentials (ADC) 认证流程。
支持多种认证来源，按优先级自动检测:
  1. GOOGLE_APPLICATION_CREDENTIALS 环境变量（服务账号 JSON 文件）
  2. gcloud CLI 登录状态 (gcloud auth application-default login)
  3. Cloud Run / GCE / GKE 的元数据服务器（自动 ADC）
  4. GOOGLE_CLOUD_PROJECT / GOOGLE_PROJECT_ID 环境变量

核心职责:
  1. 验证 ADC 凭证是否有效（未过期、权限足够）
  2. 自动检测当前 GCP 项目 ID
  3. 提供 access token 用于直接 HTTP API 调用
  4. 支持凭证刷新
  5. 检测运行环境（本地、Cloud Run、GCE、GKE）

认证错误处理:
  认证失败属于终端错误 -- 无法通过重试恢复。
  CircuitBreaker 不因认证错误而递增失败计数。
  用户必须手动修复凭证问题。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

__all__ = ["GCloudAuth", "AuthStatus", "GCPEnvironment"]


# ============================================================================
# 枚举与环境检测
# ============================================================================


class GCPEnvironment:
    """GCP 运行环境类型常量。"""
    LOCAL = "local"
    CLOUD_RUN = "cloud_run"
    GCE = "gce"
    GKE = "gke"
    CLOUD_FUNCTIONS = "cloud_functions"
    UNKNOWN = "unknown"


# GCP 元数据服务器基础 URL
_METADATA_BASE = "http://metadata.google.internal/computeMetadata/v1"

# Gemini API 所需的最低权限范围
_GEMINI_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
]


# ============================================================================
# 数据类
# ============================================================================


@dataclass
class AuthStatus:
    """ADC 认证检查结果。

    Attributes:
        authenticated: 是否已成功认证。
        credential_source: 凭证来源描述。
            "service_account_json" | "gcloud_adc" | "metadata_server" |
            "explicit_key" | "none"。
        project_id: 检测到的 GCP 项目 ID（可选）。
        access_token: 短期有效的 access token（可选，用于直接 API 调用）。
        token_expiry: access token 过期时间（ISO 8601）。
        quota_project_id: 用于计费的配额项目 ID。
        environment: 运行环境类型（local/cloud_run/gce/gke 等）。
        error_message: 认证失败时的错误信息。
        checked_at: 检查时的 ISO 时间戳。
    """
    authenticated: bool = False
    credential_source: str = "none"
    project_id: Optional[str] = None
    access_token: Optional[str] = None
    token_expiry: Optional[str] = None
    quota_project_id: Optional[str] = None
    environment: str = GCPEnvironment.UNKNOWN
    error_message: str = ""
    checked_at: str = field(default_factory=lambda: time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
    ))


# ============================================================================
# GCloudAuth 主类
# ============================================================================


class GCloudAuth:
    """GCP ADC 认证管理器。

    自动检测并使用可用的 ADC 凭证。按以下优先级查找凭证:
      1. GOOGLE_APPLICATION_CREDENTIALS 环境变量指向的 JSON 文件
      2. gcloud CLI 的 application-default 登录状态
      3. GCP 环境（Cloud Run / GCE / GKE）的元数据服务器
      4. GOOGLE_API_KEY 环境变量（显式 API 密钥）

    所有方法均为幂等且无副作用 -- 不修改任何文件或环境变量。

    Attributes:
        _cached_status: 最近一次认证检查的缓存结果。
    """

    # 缓存 TTL（秒）
    _CACHE_TTL = 300.0

    def __init__(self) -> None:
        """初始化 GCloudAuth 实例。"""
        self._cached_status: Optional[AuthStatus] = None
        self._cache_ts: float = 0.0

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def authenticate(self, force: bool = False) -> AuthStatus:
        """执行 ADC 认证检测。

        按优先级逐一尝试凭证来源，直到找到一个有效的。

        Args:
            force: 是否强制重新检测（忽略缓存）。

        Returns:
            AuthStatus 实例，authenticated=True 表示认证成功。
        """
        if not force and self._is_cache_valid():
            return self._cached_status  # type: ignore[return-value]

        status = AuthStatus()

        # 1. 检测运行环境
        status.environment = self.detect_environment()

        # 2. GOOGLE_APPLICATION_CREDENTIALS 环境变量
        creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        if creds_file and os.path.isfile(creds_file):
            try:
                creds = self._load_service_account_json(creds_file)
                if creds:
                    status.authenticated = True
                    status.credential_source = "service_account_json"
                    status.project_id = creds.get("project_id", "")
                    if not status.project_id:
                        status.project_id = self.detect_project_id()
                    self._cache_status(status)
                    return status
            except (json.JSONDecodeError, OSError, KeyError) as e:
                status.error_message = (
                    f"服务账号 JSON 解析失败 ({creds_file}): {e}"
                )

        # 3. gcloud CLI ADC 登录
        try:
            proc = subprocess.run(
                ["gcloud", "auth", "application-default", "print-access-token"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                status.authenticated = True
                status.credential_source = "gcloud_adc"
                status.access_token = proc.stdout.strip()
                status.project_id = self.detect_project_id()
                status.token_expiry = self._estimate_token_expiry()
                self._cache_status(status)
                return status
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # 4. GCP 元数据服务器 (Cloud Run / GCE / GKE)
        if status.environment != GCPEnvironment.LOCAL:
            try:
                md_status = self._check_metadata_server()
                if md_status.authenticated:
                    self._cache_status(md_status)
                    return md_status
            except Exception:
                pass

        # 5. GOOGLE_API_KEY 环境变量（显式 API 密钥）
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if api_key:
            status.authenticated = True
            status.credential_source = "explicit_key"
            status.access_token = api_key
            status.project_id = self.detect_project_id()
            self._cache_status(status)
            return status

        # 所有来源均失败
        if not status.error_message:
            status.error_message = (
                "未找到有效的 ADC 凭证。"
                "请运行: gcloud auth application-default login"
            )
        self._cache_status(status)
        return status

    def detect_project_id(self) -> Optional[str]:
        """自动检测当前 GCP 项目 ID。

        检测来源（按优先级）:
          1. GOOGLE_CLOUD_PROJECT 环境变量
          2. GOOGLE_PROJECT_ID 环境变量
          3. GCP_PROJECT 环境变量
          4. gcloud config get-value project
          5. 从服务账号 JSON 文件中提取
          6. 元数据服务器

        Returns:
            项目 ID 字符串，未检测到时返回 None。
        """
        # 环境变量
        for var in (
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_PROJECT_ID",
            "GCP_PROJECT",
        ):
            val = os.environ.get(var, "").strip()
            if val:
                return val

        # gcloud CLI 项目配置
        try:
            proc = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0:
                val = proc.stdout.strip()
                if val and val != "(unset)":
                    return val
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # 服务账号 JSON 文件
        creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        if creds_file and os.path.isfile(creds_file):
            try:
                creds = self._load_service_account_json(creds_file)
                pid = creds.get("project_id", "")
                if pid:
                    return pid
            except Exception:
                pass

        # GCE 元数据服务器
        env = self.detect_environment()
        if env in (GCPEnvironment.GCE, GCPEnvironment.CLOUD_RUN):
            try:
                return self._fetch_metadata("project/project-id")
            except Exception:
                pass

        return None

    def detect_environment(self) -> str:
        """检测当前 GCP 运行环境。

        依据以下特征判断:
          - K_SERVICE 环境变量存在 -> Cloud Run
          - CLOUD_RUN_JOB 环境变量存在 -> Cloud Run Jobs
          - FUNCTION_TARGET 环境变量存在 -> Cloud Functions
          - KUBERNETES_SERVICE_HOST 存在 -> GKE
          - GCE 元数据服务器可达 -> GCE
          - 以上均不满足 -> local

        Returns:
            GCPEnvironment 常量字符串。
        """
        if os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"):
            return GCPEnvironment.CLOUD_RUN
        if os.environ.get("FUNCTION_TARGET"):
            return GCPEnvironment.CLOUD_FUNCTIONS
        if os.environ.get("KUBERNETES_SERVICE_HOST"):
            return GCPEnvironment.GKE
        # 尝试 GCE 元数据服务器
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{_METADATA_BASE}/?recursive=false",
                headers={"Metadata-Flavor": "Google"},
            )
            urllib.request.urlopen(req, timeout=2)
            return GCPEnvironment.GCE
        except Exception:
            return GCPEnvironment.LOCAL

    # ------------------------------------------------------------------
    # 凭证验证
    # ------------------------------------------------------------------

    def verify(self) -> AuthStatus:
        """验证当前 ADC 凭证是否有效。

        等同于 authenticate()，但强制忽略缓存。

        Returns:
            AuthStatus 实例。
        """
        return self.authenticate(force=True)

    def get_access_token(self) -> Optional[str]:
        """获取短期有效的 access token。

        Returns:
            Bearer token 字符串，认证失败时返回 None。
        """
        status = self.authenticate()
        return status.access_token

    # ------------------------------------------------------------------
    # 缓存管理
    # ------------------------------------------------------------------

    def _cache_status(self, status: AuthStatus) -> None:
        """缓存认证状态。

        Args:
            status: 要缓存的 AuthStatus 实例。
        """
        self._cached_status = status
        self._cache_ts = time.time()

    def _is_cache_valid(self) -> bool:
        """检查缓存的认证状态是否仍然有效。

        Returns:
            True 表示缓存未过期且存在。
        """
        if self._cached_status is None:
            return False
        return (time.time() - self._cache_ts) < self._CACHE_TTL

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _load_service_account_json(path: str) -> dict:
        """加载服务账号 JSON 密钥文件。

        Args:
            path: JSON 密钥文件路径。

        Returns:
            包含凭证信息的字典。

        Raises:
            OSError: 文件不可读。
            json.JSONDecodeError: 文件格式无效。
        """
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _estimate_token_expiry() -> str:
        """估算 access token 的过期时间。

        大多数 GCP access token 有效期为 3600 秒。

        Returns:
            ISO 8601 格式的过期时间。
        """
        expiry_ts = time.time() + 3500  # 保守估计，留出缓冲
        return time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry_ts)
        )

    @staticmethod
    def _fetch_metadata(path: str) -> str:
        """从 GCP 元数据服务器获取数据。

        Args:
            path: 元数据路径（例如 "project/project-id"）。

        Returns:
            元数据响应文本。

        Raises:
            urllib.error.URLError: 元数据服务器不可达。
        """
        import urllib.request
        url = f"{_METADATA_BASE}/{path}"
        req = urllib.request.Request(
            url, headers={"Metadata-Flavor": "Google"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode("utf-8")

    def _check_metadata_server(self) -> AuthStatus:
        """通过 GCP 元数据服务器尝试认证。

        Returns:
            AuthStatus 实例。
        """
        status = AuthStatus(
            environment=self.detect_environment(),
            credential_source="metadata_server",
        )
        try:
            # 获取项目 ID
            pid = self._fetch_metadata("project/project-id")
            status.project_id = pid

            # 获取 access token
            token_resp = self._fetch_metadata(
                "instance/service-accounts/default/token"
            )
            token_data = json.loads(token_resp)
            status.access_token = token_data.get("access_token", "")
            status.token_expiry = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(time.time() + token_data.get("expires_in", 3600)),
            )
            status.authenticated = bool(status.access_token)
        except Exception as e:
            status.error_message = f"元数据服务器认证失败: {e}"

        return status
