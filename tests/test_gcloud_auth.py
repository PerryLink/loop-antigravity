"""GCloudAuth unit tests."""
import json
import os
import subprocess
from unittest import mock

import pytest

from loop_antigravity.gcloud_auth import (
    GCloudAuth,
    AuthStatus,
    GCPEnvironment,
)


# ---- Helpers ----

def _fake_service_account_json(project_id="test-project"):
    return json.dumps({
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "abc123",
        "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
        "client_email": "test@test-project.iam.gserviceaccount.com",
        "client_id": "123456",
    })


# ============================================================
# TestInit
# ============================================================

class TestGCloudAuthInit:
    def test_default_init(self):
        auth = GCloudAuth()
        assert auth._cached_status is None
        assert auth._cache_ts == 0.0

    def test_no_cache_initially(self):
        auth = GCloudAuth()
        assert auth._is_cache_valid() is False


# ============================================================
# TestAuthStatus
# ============================================================

class TestAuthStatus:
    def test_default_values(self):
        status = AuthStatus()
        assert status.authenticated is False
        assert status.credential_source == "none"
        assert status.project_id is None
        assert status.access_token is None
        assert status.environment == GCPEnvironment.UNKNOWN
        assert status.error_message == ""
        assert status.checked_at

    def test_fields_settable(self):
        status = AuthStatus(
            authenticated=True,
            credential_source="service_account_json",
            project_id="my-project",
            access_token="ya29.fake",
            environment=GCPEnvironment.LOCAL,
            error_message="",
        )
        assert status.authenticated is True
        assert status.credential_source == "service_account_json"
        assert status.project_id == "my-project"
        assert status.access_token == "ya29.fake"
        assert status.environment == GCPEnvironment.LOCAL


# ============================================================
# TestAuthenticateServiceAccount
# ============================================================

class TestAuthenticateServiceAccount:
    def test_service_account_json(self, tmp_path, monkeypatch):
        sa_file = tmp_path / "sa.json"
        sa_file.write_text(_fake_service_account_json("my-project-id"))
        monkeypatch.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS", str(sa_file)
        )
        # Clear other auth env vars
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        monkeypatch.delenv("GCP_PROJECT", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
        monkeypatch.delenv("FUNCTION_TARGET", raising=False)
        monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)

        # Mock subprocess to prevent gcloud calls
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            status = auth.authenticate(force=True)
            assert status.authenticated is True
            assert status.credential_source == "service_account_json"

    def test_service_account_json_invalid(self, tmp_path, monkeypatch):
        sa_file = tmp_path / "bad.json"
        sa_file.write_text("not valid json{{{")
        monkeypatch.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS", str(sa_file)
        )
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            status = auth.authenticate(force=True)
            # Should fall through to other methods and eventually fail
            assert isinstance(status, AuthStatus)

    def test_service_account_json_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "nonexistent.json")
        )
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            status = auth.authenticate(force=True)
            assert isinstance(status, AuthStatus)


# ============================================================
# TestAuthenticateApiKey
# ============================================================

class TestAuthenticateApiKey:
    def test_explicit_api_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyTest123")
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            status = auth.authenticate(force=True)
            assert status.authenticated is True
            assert status.credential_source == "explicit_key"
            assert status.access_token == "AIzaSyTest123"

    def test_gemini_api_key_takes_precedence_over_explicit(self, monkeypatch):
        # GEMINI_API_KEY is checked by GeminiSdkClient._resolve_api_key,
        # not by GCloudAuth. GOOGLE_API_KEY is what GCloudAuth checks.
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyFromGoogle")
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            status = auth.authenticate(force=True)
            assert status.credential_source == "explicit_key"


# ============================================================
# TestDetectEnvironment
# ============================================================

class TestDetectEnvironment:
    def test_local_environment(self, monkeypatch):
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
        monkeypatch.delenv("FUNCTION_TARGET", raising=False)
        monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)

        auth = GCloudAuth()
        env = auth.detect_environment()
        assert env == GCPEnvironment.LOCAL

    def test_cloud_run_environment(self, monkeypatch):
        monkeypatch.setenv("K_SERVICE", "my-service")
        auth = GCloudAuth()
        env = auth.detect_environment()
        assert env == GCPEnvironment.CLOUD_RUN

    def test_cloud_run_job_environment(self, monkeypatch):
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.setenv("CLOUD_RUN_JOB", "my-job")
        auth = GCloudAuth()
        env = auth.detect_environment()
        assert env == GCPEnvironment.CLOUD_RUN

    def test_cloud_functions_environment(self, monkeypatch):
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
        monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
        monkeypatch.setenv("FUNCTION_TARGET", "my-function")
        auth = GCloudAuth()
        env = auth.detect_environment()
        assert env == GCPEnvironment.CLOUD_FUNCTIONS

    def test_gke_environment(self, monkeypatch):
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
        monkeypatch.delenv("FUNCTION_TARGET", raising=False)
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
        auth = GCloudAuth()
        env = auth.detect_environment()
        assert env == GCPEnvironment.GKE


# ============================================================
# TestDetectProjectId
# ============================================================

class TestDetectProjectId:
    def test_google_cloud_project_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-project")
        auth = GCloudAuth()
        pid = auth.detect_project_id()
        assert pid == "env-project"

    def test_google_project_id_env(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.setenv("GOOGLE_PROJECT_ID", "project-id-env")
        auth = GCloudAuth()
        pid = auth.detect_project_id()
        assert pid == "project-id-env"

    def test_gcp_project_env(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        monkeypatch.setenv("GCP_PROJECT", "gcp-project")
        auth = GCloudAuth()
        pid = auth.detect_project_id()
        assert pid == "gcp-project"

    def test_no_project_detected(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        monkeypatch.delenv("GCP_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            pid = auth.detect_project_id()
            assert pid is None

    def test_gcloud_config_project(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        monkeypatch.delenv("GCP_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        # Mock gcloud config get-value project
        proc = mock.MagicMock()
        proc.returncode = 0
        proc.stdout = "gcloud-project\n"
        with mock.patch("subprocess.run", return_value=proc):
            auth = GCloudAuth()
            pid = auth.detect_project_id()
            assert pid == "gcloud-project"

    def test_gcloud_project_unset(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        monkeypatch.delenv("GCP_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        proc = mock.MagicMock()
        proc.returncode = 0
        proc.stdout = "(unset)\n"
        with mock.patch("subprocess.run", return_value=proc):
            auth = GCloudAuth()
            pid = auth.detect_project_id()
            assert pid is None


# ============================================================
# TestCacheBehavior
# ============================================================

class TestCacheBehavior:
    def test_cache_returns_cached_status(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyTest123")
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            s1 = auth.authenticate()
            s2 = auth.authenticate()
            assert s1 is s2  # Same cached object

    def test_force_ignores_cache(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyTest123")
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            s1 = auth.authenticate()
            s2 = auth.authenticate(force=True)
            # Different objects but same content
            assert s1.authenticated == s2.authenticated
            assert s1.credential_source == s2.credential_source

    def test_cache_ttl_expired(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyTest123")
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            auth._cache_ts = 0  # Expired immediately
            assert not auth._is_cache_valid()


# ============================================================
# TestGetAccessToken
# ============================================================

class TestGetAccessToken:
    def test_returns_token_when_present(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyTest123")
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            token = auth.get_access_token()
            assert token == "AIzaSyTest123"

    def test_returns_none_when_not_authenticated(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            token = auth.get_access_token()
            assert token is None


# ============================================================
# TestVerify
# ============================================================

class TestVerify:
    def test_verify_forces_refresh(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyTest123")
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            auth = GCloudAuth()
            auth._cached_status = AuthStatus(authenticated=False)
            auth._cache_ts = 9999999999
            status = auth.verify()
            assert status.authenticated is True


# ============================================================
# TestGCPEnvironmentConstants
# ============================================================

class TestGCPEnvironmentConstants:
    def test_all_constants_defined(self):
        assert GCPEnvironment.LOCAL == "local"
        assert GCPEnvironment.CLOUD_RUN == "cloud_run"
        assert GCPEnvironment.GCE == "gce"
        assert GCPEnvironment.GKE == "gke"
        assert GCPEnvironment.CLOUD_FUNCTIONS == "cloud_functions"
        assert GCPEnvironment.UNKNOWN == "unknown"

    def test_constants_are_unique(self):
        values = [
            GCPEnvironment.LOCAL,
            GCPEnvironment.CLOUD_RUN,
            GCPEnvironment.GCE,
            GCPEnvironment.GKE,
            GCPEnvironment.CLOUD_FUNCTIONS,
            GCPEnvironment.UNKNOWN,
        ]
        assert len(values) == len(set(values))
