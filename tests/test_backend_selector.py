"""BackendSelector unit tests."""
import os
import subprocess
from unittest import mock

import pytest

from loop_antigravity.backend_selector import (
    BackendSelector,
    BackendSelection,
    BACKEND_AGY_CLI,
    BACKEND_GEMINI_SDK,
)


# ---- Helpers ----

def _make_select_calls_fail():
    """Patch subprocess.run to simulate agy CLI not found."""
    def run_side_effect(*args, **kwargs):
        raise FileNotFoundError("agy not found")
    return mock.patch("subprocess.run", side_effect=run_side_effect)


def _make_sdk_import_fail():
    """Patch import to simulate SDK not installed."""
    def import_side_effect(name, *args, **kwargs):
        if "google.generativeai" in name:
            raise ImportError("No module named google.generativeai")
        return __import__(name, *args, **kwargs)
    return mock.patch("builtins.__import__", side_effect=import_side_effect)


# ============================================================
# TestInit
# ============================================================

class TestBackendSelectorInit:
    def test_default_init(self):
        sel = BackendSelector()
        assert sel.mode == "auto"
        assert sel.model == "gemini-2.5-flash"
        assert sel.gemini_project is None
        assert sel.gemini_location == "us-central1"

    def test_custom_params(self):
        sel = BackendSelector(
            mode="safe",
            model="gemini-2.5-pro",
            gemini_project="my-project",
            gemini_location="europe-west1",
        )
        assert sel.mode == "safe"
        assert sel.model == "gemini-2.5-pro"
        assert sel.gemini_project == "my-project"
        assert sel.gemini_location == "europe-west1"

    def test_circuit_breaker_passed(self):
        fake_cb = object()
        sel = BackendSelector(circuit_breaker=fake_cb)
        assert sel.circuit_breaker is fake_cb

    def test_no_cache_initially(self):
        sel = BackendSelector()
        assert sel._cached_selection is None
        assert sel._agy_available is None
        assert sel._sdk_available is None


# ============================================================
# TestSelectBasic
# ============================================================

class TestBackendSelectorSelect:
    def test_select_caches_result(self):
        with _make_select_calls_fail(), _make_sdk_import_fail():
            sel = BackendSelector()
            with pytest.raises(RuntimeError, match="没有可用的"):
                sel.select()
            # Cache is NOT populated on error path (only on success)
            assert sel._cached_selection is None

    def test_select_force_ignores_cache(self):
        with _make_select_calls_fail(), _make_sdk_import_fail():
            sel = BackendSelector()
            sel._cached_selection = BackendSelection(
                backend_type=BACKEND_AGY_CLI
            )
            # force=True should re-check
            with pytest.raises(RuntimeError, match="没有可用的"):
                sel.select(force=True)

    def test_select_returns_cache_if_present(self):
        sel = BackendSelector()
        cached = BackendSelection(backend_type=BACKEND_AGY_CLI)
        sel._cached_selection = cached
        result = sel.select()
        assert result is cached

    def test_select_force_env_var_agy_cli(self, monkeypatch):
        monkeypatch.setenv("LOOP_AG_BACKEND", "agy_cli")
        sel = BackendSelector()
        sel._agy_available = True
        sel._cached_selection = None
        result = sel.select()
        assert result.backend_type == BACKEND_AGY_CLI

    def test_select_force_env_var_gemini_sdk(self, monkeypatch):
        monkeypatch.setenv("LOOP_AG_BACKEND", "gemini_sdk")
        sel = BackendSelector()
        sel._sdk_available = True
        sel._cached_selection = None
        result = sel.select()
        assert result.backend_type == BACKEND_GEMINI_SDK

    def test_select_force_invalid_env_var(self, monkeypatch):
        monkeypatch.setenv("LOOP_AG_BACKEND", "invalid_backend")
        sel = BackendSelector()
        sel._agy_available = False
        sel._sdk_available = False
        with pytest.raises(RuntimeError, match="没有可用的"):
            sel.select()


# ============================================================
# TestSelectionResult
# ============================================================

class TestBackendSelection:
    def test_default_values(self):
        bs = BackendSelection()
        assert bs.backend_type == ""
        assert bs.backend_instance is None
        assert bs.agy_available is False
        assert bs.sdk_available is False
        assert isinstance(bs.health_summary, dict)
        assert bs.selected_at

    def test_fields_settable(self):
        bs = BackendSelection(
            backend_type=BACKEND_AGY_CLI,
            agy_available=True,
            selection_reason="test reason",
        )
        assert bs.backend_type == BACKEND_AGY_CLI
        assert bs.agy_available is True
        assert bs.selection_reason == "test reason"


# ============================================================
# TestHealthSummary
# ============================================================

class TestHealthSummary:
    def test_returns_dict(self):
        sel = BackendSelector()
        sel._agy_available = True
        sel._sdk_available = True
        summary = sel.health_summary()
        assert isinstance(summary, dict)
        assert "agy_cli" in summary
        assert "gemini_sdk" in summary
        assert "recommended_backend" in summary
        assert "checked_at" in summary

    def test_recommends_agy_when_available(self):
        sel = BackendSelector()
        sel._agy_available = True
        sel._sdk_available = False
        summary = sel.health_summary()
        assert summary["recommended_backend"] == BACKEND_AGY_CLI

    def test_recommends_sdk_when_agy_unavailable(self):
        sel = BackendSelector()
        sel._agy_available = False
        sel._sdk_available = True
        summary = sel.health_summary()
        assert summary["recommended_backend"] == BACKEND_GEMINI_SDK

    def test_recommends_none_when_both_unavailable(self):
        sel = BackendSelector()
        sel._agy_available = False
        sel._sdk_available = False
        summary = sel.health_summary()
        assert summary["recommended_backend"] == "none"


# ============================================================
# TestCacheInvalidation
# ============================================================

class TestCacheInvalidation:
    def test_invalidate_clears_all(self):
        sel = BackendSelector()
        sel._cached_selection = BackendSelection()
        sel._agy_available = True
        sel._sdk_available = True
        sel.invalidate_cache()
        assert sel._cached_selection is None
        assert sel._agy_available is None
        assert sel._sdk_available is None

    def test_invalidate_then_recheck(self):
        sel = BackendSelector()
        sel._agy_available = False
        sel._sdk_available = True
        sel._cached_selection = object()
        sel.invalidate_cache()
        # After invalidation, cache is cleared
        assert sel._cached_selection is None


# ============================================================
# TestConstants
# ============================================================

class TestBackendConstants:
    def test_agy_cli_constant(self):
        assert BACKEND_AGY_CLI == "agy_cli"

    def test_gemini_sdk_constant(self):
        assert BACKEND_GEMINI_SDK == "gemini_sdk"

    def test_constants_are_different(self):
        assert BACKEND_AGY_CLI != BACKEND_GEMINI_SDK


# ============================================================
# TestSelectForcedEdgeCases
# ============================================================

class TestSelectForcedEdgeCases:
    def test_force_agy_unavailable_raises(self, monkeypatch):
        monkeypatch.setenv("LOOP_AG_BACKEND", "agy_cli")
        sel = BackendSelector()
        sel._agy_available = False
        with pytest.raises(RuntimeError, match="不可用"):
            sel.select(force=True)

    def test_force_sdk_unavailable_raises(self, monkeypatch):
        monkeypatch.setenv("LOOP_AG_BACKEND", "gemini_sdk")
        sel = BackendSelector()
        sel._sdk_available = False
        with pytest.raises(RuntimeError, match="不可用"):
            sel.select(force=True)


# ============================================================
# TestCheckAgyAvailable
# ============================================================

class TestCheckAgyAvailable:
    def test_agy_not_in_path(self):
        sel = BackendSelector()
        sel._agy_available = None

        def run_side_effect(*args, **kwargs):
            raise FileNotFoundError("agy not found")

        with mock.patch("subprocess.run", side_effect=run_side_effect):
            result = sel._check_agy_available()
            assert result is False
            assert sel._agy_available is False

    def test_agy_version_fails(self):
        sel = BackendSelector()
        sel._agy_available = None

        mock_result = mock.MagicMock()
        mock_result.returncode = 1

        with mock.patch("subprocess.run", return_value=mock_result):
            result = sel._check_agy_available()
            assert result is False

    def test_agy_version_timeout(self):
        sel = BackendSelector()
        sel._agy_available = None

        def run_side_effect(*args, **kwargs):
            raise subprocess.TimeoutExpired("agy", 10)

        with mock.patch("subprocess.run", side_effect=run_side_effect):
            result = sel._check_agy_available()
            assert result is False

    def test_agy_available_cached(self):
        sel = BackendSelector()
        sel._agy_available = True
        result = sel._check_agy_available()
        assert result is True

    def test_agy_full_check_succeeds(self):
        """模拟 agy 完整检测成功（--version + 标志兼容性测试）。"""
        sel = BackendSelector()
        sel._agy_available = None

        # 第一次调用 subprocess.run 返回 --version 成功
        # 第二次调用返回标志兼容性测试成功
        call_count = [0]

        def run_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = mock.MagicMock()
            result.returncode = 0
            return result

        with mock.patch("subprocess.run", side_effect=run_side_effect):
            result = sel._check_agy_available()
            assert result is True
            assert sel._agy_available is True
        assert call_count[0] == 2

    def test_agy_flag_check_timeout(self):
        """标志兼容性测试超时应返回 False。"""
        sel = BackendSelector()
        sel._agy_available = None

        call_count = [0]

        def run_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # --version 成功
                result = mock.MagicMock()
                result.returncode = 0
                return result
            else:
                # 标志检查超时
                raise subprocess.TimeoutExpired("agy", 20)

        with mock.patch("subprocess.run", side_effect=run_side_effect):
            result = sel._check_agy_available()
            assert result is False


# ============================================================
# TestCheckSdkAvailable
# ============================================================

class TestCheckSdkAvailable:
    def test_sdk_available_cached(self):
        sel = BackendSelector()
        sel._sdk_available = True
        result = sel._check_sdk_available()
        assert result is True

    def test_sdk_import_success(self):
        """模拟 google.generativeai 导入成功。"""
        sel = BackendSelector()
        sel._sdk_available = None

        fake_module = mock.MagicMock()
        with mock.patch.dict(
            "sys.modules", {"google.generativeai": fake_module}
        ):
            result = sel._check_sdk_available()
            assert result is True
            # 缓存已设置
            assert sel._sdk_available is True


# ============================================================
# TestSelectAgyPath
# ============================================================

class TestSelectAgyPath:
    def test_select_agy_when_available(self):
        """模拟 agy CLI 可用时 select() 选择 agy。"""
        sel = BackendSelector()
        sel._agy_available = True
        sel._sdk_available = False
        sel._cached_selection = None

        # Mock AgyClient 导入
        fake_agy_client = mock.MagicMock()
        with mock.patch.dict(
            "sys.modules",
            {"loop_antigravity.agy_client": mock.MagicMock(
                AgyClient=mock.MagicMock(return_value=fake_agy_client)
            )},
        ):
            result = sel.select()
            assert result.backend_type == BACKEND_AGY_CLI
            assert result.agy_available is True
            assert "agy CLI 可用" in result.selection_reason
            assert sel._cached_selection is not None

    def test_select_sdk_when_agy_unavailable(self):
        """模拟 agy CLI 不可用、SDK 可用时 select() 回退到 SDK。"""
        sel = BackendSelector()
        sel._agy_available = False
        sel._sdk_available = True
        sel._cached_selection = None

        fake_sdk_client = mock.MagicMock()
        with mock.patch.dict(
            "sys.modules",
            {"loop_antigravity.gemini_sdk_client": mock.MagicMock(
                GeminiSdkClient=mock.MagicMock(return_value=fake_sdk_client)
            )},
        ):
            result = sel.select()
            assert result.backend_type == BACKEND_GEMINI_SDK
            assert result.agy_available is False
            assert result.sdk_available is True
            assert "回退" in result.selection_reason
            assert sel._cached_selection is not None


# ============================================================
# TestResolveRecommended
# ============================================================

class TestResolveRecommended:
    def test_recommends_agy(self):
        sel = BackendSelector()
        sel._agy_available = True
        assert sel._resolve_recommended() == BACKEND_AGY_CLI

    def test_recommends_sdk_fallback(self):
        sel = BackendSelector()
        sel._agy_available = False
        sel._sdk_available = True
        assert sel._resolve_recommended() == BACKEND_GEMINI_SDK

    def test_recommends_none(self):
        sel = BackendSelector()
        sel._agy_available = False
        sel._sdk_available = False
        assert sel._resolve_recommended() == "none"

    def test_recommended_cached_agy(self):
        sel = BackendSelector()
        sel._sdk_available = False
        sel._agy_available = True
        assert sel._resolve_recommended() == BACKEND_AGY_CLI
