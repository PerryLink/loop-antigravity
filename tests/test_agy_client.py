# -*- coding: utf-8 -*-
"""AgyClient unit tests.

Mock subprocess for health checks, communication, and error handling.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from loop_antigravity.agy_client import (
    AgyAuthError,
    AgyBadRequestError,
    AgyCircuitOpenError,
    AgyClient,
    AgyError,
    AgyNotInstalledError,
    AgyQuotaExhausted,
    AgyStreamParseError,
    AgySubprocessError,
    AgyTimeoutError,
    HealthStatus,
    MediaInput,
    QuotaStatus,
    StreamParsingState,
    _infer_media_type,
)
from loop_antigravity.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
)


# ============================================================================
# Data Class and Exception Tests
# ============================================================================


class TestAgyResult:
    """AgyResult data class tests."""

    def test_ok_true_with_text_and_stop(self) -> None:
        """ok should be True with text and STOP finish_reason."""
        from loop_antigravity.agy_client import AgyResult
        result = AgyResult(text="Hello", finish_reason="STOP")
        assert result.ok is True

    def test_ok_true_with_max_tokens(self) -> None:
        """ok should be True with MAX_TOKENS finish_reason."""
        from loop_antigravity.agy_client import AgyResult
        result = AgyResult(text="Output truncated", finish_reason="MAX_TOKENS")
        assert result.ok is True

    def test_ok_false_empty_text(self) -> None:
        """ok should be False with empty text."""
        from loop_antigravity.agy_client import AgyResult
        result = AgyResult(text="", finish_reason="STOP")
        assert result.ok is False

    def test_ok_false_safety_finish(self) -> None:
        """ok should be False with SAFETY finish_reason."""
        from loop_antigravity.agy_client import AgyResult
        result = AgyResult(text="Blocked", finish_reason="SAFETY")
        assert result.ok is False

    def test_ok_false_unknown_finish(self) -> None:
        """ok should be False with UNKNOWN finish_reason."""
        from loop_antigravity.agy_client import AgyResult
        result = AgyResult(text="something", finish_reason="UNKNOWN")
        assert result.ok is False


class TestHealthStatus:
    """HealthStatus data class tests."""

    def test_default_values(self) -> None:
        """Default values should match definition."""
        status = HealthStatus()
        assert status.ok is False
        assert status.backend_type == "agy_cli"
        assert status.authenticated is False
        assert status.model_available is False

    def test_post_init_sets_checked_at(self) -> None:
        """__post_init__ should auto-set checked_at timestamp."""
        status = HealthStatus()
        assert status.checked_at != ""
        assert "T" in status.checked_at

    def test_checked_at_not_overwritten(self) -> None:
        """If checked_at is provided, should not be overwritten."""
        status = HealthStatus(checked_at="2026-01-01T00:00:00Z")
        assert status.checked_at == "2026-01-01T00:00:00Z"


class TestQuotaStatus:
    """QuotaStatus data class tests."""

    def test_default_values(self) -> None:
        """Default values should match definition."""
        qs = QuotaStatus()
        assert qs.available is True
        assert qs.status_code == "UNKNOWN"
        assert qs.rpm_used == 0
        assert qs.rate_limit_429_count == 0

    def test_custom_values(self) -> None:
        """Custom values should be correctly stored."""
        qs = QuotaStatus(
            available=False,
            rpm_used=50,
            rpm_limit=100,
            tpd_used=10000,
            tpd_limit=50000,
            status_code="WARNING",
            estimated_recovery_at="2026-06-13T12:00:00Z",
            rate_limit_429_count=2,
            current_5h_window_used_pct=85.0,
            weekly_cap_used_pct=40.0,
        )
        assert qs.available is False
        assert qs.rpm_used == 50
        assert qs.rpm_limit == 100
        assert qs.status_code == "WARNING"
        assert qs.rate_limit_429_count == 2
        assert qs.current_5h_window_used_pct == 85.0


class TestMediaInput:
    """MediaInput data class tests."""

    def test_basic_initialization(self) -> None:
        """Basic initialization should infer media_type."""
        mi = MediaInput(path="/tmp/test.png", mime_type="image/png")
        assert mi.path == "/tmp/test.png"
        assert mi.mime_type == "image/png"
        assert mi.media_type == "image"

    def test_infer_audio_type(self) -> None:
        """audio/* MIME should be inferred as audio."""
        mi = MediaInput(path="/tmp/test.mp3", mime_type="audio/mpeg")
        assert mi.media_type == "audio"

    def test_infer_video_type(self) -> None:
        """video/* MIME should be inferred as video."""
        mi = MediaInput(path="/tmp/test.mp4", mime_type="video/mp4")
        assert mi.media_type == "video"

    def test_infer_pdf_type(self) -> None:
        """application/pdf should be inferred as pdf."""
        mi = MediaInput(path="/tmp/test.pdf", mime_type="application/pdf")
        assert mi.media_type == "pdf"

    def test_large_file_uses_file_api(self) -> None:
        """Files > 20MB should set use_file_api=True."""
        mi = MediaInput(path="/tmp/large.png", mime_type="image/png", size_bytes=25 * 1024 * 1024)
        assert mi.use_file_api is True

    def test_small_file_not_use_file_api(self) -> None:
        """Files <= 20MB should not use File API."""
        mi = MediaInput(path="/tmp/small.png", mime_type="image/png", size_bytes=10 * 1024 * 1024)
        assert mi.use_file_api is False

    def test_size_from_disk(self, tmp_path) -> None:
        """size_bytes should be read from disk if not provided."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello World" * 100)
        mi = MediaInput(path=str(test_file), mime_type="text/plain")
        assert mi.size_bytes > 0


class TestExceptionClasses:
    """AgyError and subclass tests."""

    def test_agy_error_base(self) -> None:
        """Base AgyError should have correct code and retryable flag."""
        err = AgyError("test error", code="AGY_TEST", retryable=True)
        assert err.code == "AGY_TEST"
        assert err.retryable is True
        assert str(err) == "test error"

    def test_agy_not_installed_default(self) -> None:
        """AgyNotInstalledError defaults."""
        err = AgyNotInstalledError()
        assert err.code == "AGY_NOT_FOUND"
        assert err.retryable is False

    def test_agy_auth_error_default(self) -> None:
        """AgyAuthError defaults."""
        err = AgyAuthError()
        assert err.code == "AGY_AUTH_ERROR"
        assert err.retryable is True

    def test_agy_auth_error_custom(self) -> None:
        """AgyAuthError with custom message."""
        err = AgyAuthError("Custom auth failed")
        assert str(err) == "Custom auth failed"

    def test_agy_quota_exhausted_with_retry_after(self) -> None:
        """AgyQuotaExhausted should carry retry_after_seconds."""
        err = AgyQuotaExhausted(
            "Quota exhausted",
            retry_after_seconds=60,
            estimated_recovery_at="2026-06-13T12:00:00Z",
        )
        assert err.code == "AGY_QUOTA_EXHAUSTED"
        assert err.retryable is True
        assert err.retry_after_seconds == 60
        assert err.estimated_recovery_at == "2026-06-13T12:00:00Z"

    def test_agy_timeout_error(self) -> None:
        """AgyTimeoutError should carry timeout_ms."""
        err = AgyTimeoutError("Timed out", timeout_ms=5000)
        assert err.code == "AGY_TIMEOUT"
        assert err.timeout_ms == 5000

    def test_agy_stream_parse_error(self) -> None:
        """AgyStreamParseError should carry line_number and raw_line."""
        err = AgyStreamParseError(
            "Parse failed", line_number=5, raw_line="bad json here {"
        )
        assert err.code == "AGY_STREAM_PARSE_ERROR"
        assert err.line_number == 5
        assert "bad json" in err.raw_line

    def test_agy_circuit_open_error(self) -> None:
        """AgyCircuitOpenError should carry cooldown_remaining_seconds."""
        err = AgyCircuitOpenError(
            "Circuit open", cooldown_remaining_seconds=30.5
        )
        assert err.code == "AGY_CIRCUIT_OPEN"
        assert err.cooldown_remaining_seconds == 30.5

    def test_agy_subprocess_error(self) -> None:
        """AgySubprocessError should carry exit_code and signal_num."""
        err = AgySubprocessError(
            "Subprocess failed", exit_code=1, signal_num=-1
        )
        assert err.code == "AGY_SUBPROCESS_ERROR"
        assert err.exit_code == 1
        assert err.signal_num == -1

    def test_agy_bad_request_error(self) -> None:
        """AgyBadRequestError should be non-retryable."""
        err = AgyBadRequestError("Bad request")
        assert err.code == "AGY_BAD_REQUEST"
        assert err.retryable is False


class TestInferMediaType:
    """_infer_media_type helper function tests."""

    def test_image_types(self) -> None:
        """Image MIME types should return 'image'."""
        assert _infer_media_type("image/png") == "image"
        assert _infer_media_type("image/jpeg") == "image"
        assert _infer_media_type("image/gif") == "image"
        assert _infer_media_type("IMAGE/PNG") == "image"

    def test_audio_types(self) -> None:
        """Audio MIME types should return 'audio'."""
        assert _infer_media_type("audio/mpeg") == "audio"
        assert _infer_media_type("audio/wav") == "audio"
        assert _infer_media_type("audio/ogg") == "audio"

    def test_video_types(self) -> None:
        """Video MIME types should return 'video'."""
        assert _infer_media_type("video/mp4") == "video"
        assert _infer_media_type("video/webm") == "video"

    def test_pdf_type(self) -> None:
        """application/pdf should return 'pdf'."""
        assert _infer_media_type("application/pdf") == "pdf"

    def test_unknown_type(self) -> None:
        """Unknown types should return 'unknown'."""
        assert _infer_media_type("text/plain") == "unknown"
        assert _infer_media_type("application/json") == "unknown"


# ============================================================================
# AgyClient Init Tests
# ============================================================================


class TestAgyClientInit:
    """AgyClient initialization tests."""

    def test_init_with_valid_mode(self, circuit_breaker: CircuitBreaker) -> None:
        """Valid mode should create AgyClient successfully."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        assert agy.mode == "auto"
        assert agy.backend_type == "agy_cli"

    def test_init_with_invalid_mode_raises(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Invalid mode should raise ValueError."""
        with pytest.raises(ValueError):
            AgyClient(mode="super_risky", circuit_breaker=circuit_breaker)

    def test_init_with_safe_mode(self, circuit_breaker: CircuitBreaker) -> None:
        """safe mode should create successfully."""
        agy = AgyClient(mode="safe", circuit_breaker=circuit_breaker)
        assert agy.mode == "safe"

    def test_init_with_unsafe_mode(self, circuit_breaker: CircuitBreaker) -> None:
        """unsafe mode should create successfully."""
        agy = AgyClient(mode="unsafe", circuit_breaker=circuit_breaker)
        assert agy.mode == "unsafe"

    def test_init_with_collaborative_mode(self, circuit_breaker: CircuitBreaker) -> None:
        """collaborative mode should create successfully."""
        agy = AgyClient(mode="collaborative", circuit_breaker=circuit_breaker)
        assert agy.mode == "collaborative"


# ============================================================================
# Health Check Tests
# ============================================================================


class TestHealthCheck:
    """check_health tests (mock subprocess)."""

    @pytest.fixture
    def agy_client(self, circuit_breaker: CircuitBreaker) -> AgyClient:
        """Create an AgyClient test instance."""
        return AgyClient(mode="auto", circuit_breaker=circuit_breaker)

    def test_check_health_agy_not_found(
        self, agy_client: AgyClient
    ) -> None:
        """check_health should report failure when agy CLI not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            status = agy_client.check_health()
            assert not status.ok
            assert "not found" in status.message.lower()

    def test_check_health_version_fails(
        self, agy_client: AgyClient
    ) -> None:
        """check_health should report failure when --version fails."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "command not found"
        with patch("subprocess.run", return_value=mock_proc):
            status = agy_client.check_health()
            assert not status.ok

    def test_check_health_version_timeout(
        self, agy_client: AgyClient
    ) -> None:
        """check_health should report failure on --version timeout."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(
            cmd=["agy", "--version"], timeout=10
        )):
            status = agy_client.check_health()
            assert not status.ok

    def test_check_health_flag_verification_fails(
        self, agy_client: AgyClient
    ) -> None:
        """check_health should report failure when flag verification fails."""
        version_proc = MagicMock()
        version_proc.returncode = 0
        version_proc.stdout = "agy v1.2.3"
        version_proc.stderr = ""

        with patch.object(agy_client, "_verify_flags") as mock_verify:
            mock_verify.return_value = {
                "flags": {
                    "--non-interactive": True,
                    "--output-format stream-json": False,
                    "--yolo": True,
                },
                "all_pass": False,
                "detail": "Flag test failed",
            }
            with patch("subprocess.run", return_value=version_proc):
                status = agy_client.check_health()
                assert not status.ok
                assert "not supported" in status.message.lower()

    def test_check_health_success(
        self, agy_client: AgyClient, circuit_breaker: CircuitBreaker
    ) -> None:
        """Simulate a successful complete health check."""
        version_proc = MagicMock()
        version_proc.returncode = 0
        version_proc.stdout = "agy v1.2.3"
        version_proc.stderr = ""

        fake_output = "\n".join([
            json.dumps({"type": "status", "stage": "start"}),
            json.dumps({"type": "text", "content": "OK"}),
            json.dumps({"type": "usage", "input_tokens": 10, "output_tokens": 2}),
        ])

        ping_proc = MagicMock()
        ping_proc.returncode = 0
        ping_proc.stdout = fake_output
        ping_proc.stderr = ""

        with patch.object(agy_client, "_verify_flags") as mock_verify:
            mock_verify.return_value = {
                "flags": {
                    "--non-interactive": True,
                    "--output-format stream-json": True,
                    "--yolo": True,
                },
                "all_pass": True,
                "detail": "All flags verified",
            }
            run_results = iter([version_proc, ping_proc])

            def side_effect(cmd, **kwargs):
                return next(run_results)

            with patch("subprocess.run", side_effect=side_effect):
                status = agy_client.check_health()
                assert status.ok is True
                assert status.model_available is True
                assert status.authenticated is True
                assert "healthy" in status.message.lower()

    def test_check_health_error_event_in_response(
        self, agy_client: AgyClient
    ) -> None:
        """check_health should report failure on error event in response."""
        version_proc = MagicMock()
        version_proc.returncode = 0
        version_proc.stdout = "agy v1.2.3"
        version_proc.stderr = ""

        fake_output = json.dumps({"type": "error", "message": "Service unavailable"})

        ping_proc = MagicMock()
        ping_proc.returncode = 0
        ping_proc.stdout = fake_output
        ping_proc.stderr = ""

        with patch.object(agy_client, "_verify_flags") as mock_verify:
            mock_verify.return_value = {
                "flags": {
                    "--non-interactive": True,
                    "--output-format stream-json": True,
                    "--yolo": True,
                },
                "all_pass": True,
                "detail": "OK",
            }
            run_results = iter([version_proc, ping_proc])

            def side_effect(cmd, **kwargs):
                return next(run_results)

            with patch("subprocess.run", side_effect=side_effect):
                status = agy_client.check_health()
                assert not status.ok
                assert "error" in status.message.lower()

    def test_check_health_no_text_in_response(
        self, agy_client: AgyClient
    ) -> None:
        """check_health should report failure when no text content in response."""
        version_proc = MagicMock()
        version_proc.returncode = 0
        version_proc.stdout = "agy v1.2.3"
        version_proc.stderr = ""

        fake_output = json.dumps({"type": "status", "stage": "start"})

        ping_proc = MagicMock()
        ping_proc.returncode = 0
        ping_proc.stdout = fake_output
        ping_proc.stderr = ""

        with patch.object(agy_client, "_verify_flags") as mock_verify:
            mock_verify.return_value = {
                "flags": {
                    "--non-interactive": True,
                    "--output-format stream-json": True,
                    "--yolo": True,
                },
                "all_pass": True,
                "detail": "OK",
            }
            run_results = iter([version_proc, ping_proc])

            def side_effect(cmd, **kwargs):
                return next(run_results)

            with patch("subprocess.run", side_effect=side_effect):
                status = agy_client.check_health()
                assert not status.ok
                assert "No text" in status.message

    def test_check_health_functional_test_timeout(
        self, agy_client: AgyClient
    ) -> None:
        """check_health should report failure on functional test timeout."""
        version_proc = MagicMock()
        version_proc.returncode = 0
        version_proc.stdout = "agy v1.2.3"
        version_proc.stderr = ""

        with patch.object(agy_client, "_verify_flags") as mock_verify:
            mock_verify.return_value = {
                "flags": {
                    "--non-interactive": True,
                    "--output-format stream-json": True,
                    "--yolo": True,
                },
                "all_pass": True,
                "detail": "OK",
            }
            run_results = iter([
                version_proc,
                subprocess.TimeoutExpired(cmd=["agy", "-p"], timeout=30),
            ])

            def side_effect(cmd, **kwargs):
                result = next(run_results)
                if isinstance(result, Exception):
                    raise result
                return result

            with patch("subprocess.run", side_effect=side_effect):
                status = agy_client.check_health()
                assert not status.ok
                assert "timed out" in status.message.lower()

    def test_check_health_agy_disappears(
        self, agy_client: AgyClient
    ) -> None:
        """check_health should catch FileNotFound error during health check."""
        version_proc = MagicMock()
        version_proc.returncode = 0
        version_proc.stdout = "agy v1.2.3"
        version_proc.stderr = ""

        with patch.object(agy_client, "_verify_flags") as mock_verify:
            mock_verify.return_value = {
                "flags": {
                    "--non-interactive": True,
                    "--output-format stream-json": True,
                    "--yolo": True,
                },
                "all_pass": True,
                "detail": "OK",
            }
            run_results = iter([
                version_proc,
                FileNotFoundError("agy not found"),
            ])

            def side_effect(cmd, **kwargs):
                result = next(run_results)
                if isinstance(result, Exception):
                    raise result
                return result

            with patch("subprocess.run", side_effect=side_effect):
                status = agy_client.check_health()
                assert not status.ok
                assert "disappeared" in status.message.lower()

    def test_check_health_functional_nonzero_exit(
        self, agy_client: AgyClient
    ) -> None:
        """check_health should report failure on functional test non-zero exit."""
        version_proc = MagicMock()
        version_proc.returncode = 0
        version_proc.stdout = "agy v1.2.3"
        version_proc.stderr = ""

        ping_proc = MagicMock()
        ping_proc.returncode = 1
        ping_proc.stdout = ""
        ping_proc.stderr = ""

        with patch.object(agy_client, "_verify_flags") as mock_verify:
            mock_verify.return_value = {
                "flags": {
                    "--non-interactive": True,
                    "--output-format stream-json": True,
                    "--yolo": True,
                },
                "all_pass": True,
                "detail": "OK",
            }
            run_results = iter([version_proc, ping_proc])

            def side_effect(cmd, **kwargs):
                return next(run_results)

            with patch("subprocess.run", side_effect=side_effect):
                status = agy_client.check_health()
                assert not status.ok

    def test_check_health_version_stdout_stderr(self) -> None:
        """When stdout is empty, version should be read from stderr."""
        cb = MagicMock()
        agy = AgyClient(mode="auto", circuit_breaker=cb)

        version_proc = MagicMock()
        version_proc.returncode = 0
        version_proc.stdout = ""
        version_proc.stderr = "agy v2.0.0"

        with patch.object(agy, "_verify_flags") as mock_verify:
            mock_verify.return_value = {
                "flags": {
                    "--non-interactive": True,
                    "--output-format stream-json": True,
                    "--yolo": True,
                },
                "all_pass": True,
                "detail": "OK",
            }

            jump_proc = MagicMock()
            jump_proc.returncode = 0
            jump_proc.stdout = json.dumps({"type": "text", "content": "OK"})
            jump_proc.stderr = ""

            run_results = iter([version_proc, jump_proc])

            def side_effect(cmd, **kwargs):
                return next(run_results)

            with patch("subprocess.run", side_effect=side_effect):
                status = agy.check_health()
                assert status.version == "agy v2.0.0"


# ============================================================================
# _verify_flags Tests
# ============================================================================


class TestVerifyFlags:
    """_verify_flags method tests."""

    def test_verify_flags_success(self, circuit_breaker: CircuitBreaker) -> None:
        """All three flags should pass with all_pass=True."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)

        fake_output = "\n".join([
            json.dumps({"type": "status", "stage": "start"}),
            json.dumps({"type": "text", "content": "FLAGS_OK"}),
            json.dumps({"type": "usage", "input_tokens": 5, "output_tokens": 1}),
        ])
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = fake_output
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = agy._verify_flags()
            assert result["all_pass"] is True
            for key in result["flags"]:
                assert result["flags"][key] is True

    def test_verify_flags_nonzero_exit(self, circuit_breaker: CircuitBreaker) -> None:
        """Non-zero exit from combined flag test should return all_pass=False."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "error: unrecognized flag"

        with patch("subprocess.run", return_value=mock_proc):
            result = agy._verify_flags()
            assert result["all_pass"] is False

    def test_verify_flags_timeout(self, circuit_breaker: CircuitBreaker) -> None:
        """Flag verification timeout should return all_pass=False."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(
            cmd=["agy"], timeout=15
        )):
            result = agy._verify_flags()
            assert result["all_pass"] is False
            assert "timed out" in result["detail"].lower()

    def test_verify_flags_file_not_found(self, circuit_breaker: CircuitBreaker) -> None:
        """Flag verification FileNotFound should return all_pass=False."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = agy._verify_flags()
            assert result["all_pass"] is False
            assert "not found" in result["detail"].lower()

    def test_verify_flags_json_decode_error_skip(self, circuit_breaker: CircuitBreaker) -> None:
        """JSON decode error lines should be skipped."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)

        fake_output = "\n".join([
            json.dumps({"type": "status", "stage": "start"}),
            "this is not json",
            json.dumps({"type": "text", "content": "FLAGS_OK"}),
        ])
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = fake_output
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = agy._verify_flags()
            assert result["all_pass"] is True

    def test_verify_flags_no_text_but_multiple_json(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """No FLAGS_OK text but multiple JSON lines should also pass."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)

        fake_output = "\n".join([
            json.dumps({"type": "status", "stage": "start"}),
            json.dumps({"type": "text", "content": "something else"}),
            json.dumps({"type": "usage"}),
        ])
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = fake_output
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = agy._verify_flags()
            assert result["all_pass"] is True


# ============================================================================
# _augment_prompt_with_context Tests
# ============================================================================


class TestAugmentPrompt:
    """_augment_prompt_with_context method tests."""

    def test_no_context_files_returns_original(self, circuit_breaker: CircuitBreaker) -> None:
        """Empty context files should return original prompt."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        result = agy._augment_prompt_with_context("Hello", [])
        assert result == "Hello"

    def test_text_file_augmented(self, circuit_breaker: CircuitBreaker, tmp_path) -> None:
        """Text file content should be prepended to prompt."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        result = agy._augment_prompt_with_context("User prompt", [str(test_file)])
        assert "test.py" in result
        assert "print('hello')" in result
        assert "User prompt" in result

    def test_file_not_found_handled(self, circuit_breaker: CircuitBreaker) -> None:
        """Non-existent files should generate placeholder."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        result = agy._augment_prompt_with_context(
            "User prompt", ["/nonexistent/file.py"]
        )
        assert "File not found" in result
        assert "User prompt" in result

    def test_non_text_file_handled(self, circuit_breaker: CircuitBreaker, tmp_path) -> None:
        """Non-text extension files should generate placeholder."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        test_file = tmp_path / "image.xyz"
        test_file.write_text("binary-like data")

        result = agy._augment_prompt_with_context(
            "User prompt", [str(test_file)]
        )
        assert "Non-text file" in result

    def test_multiple_files(self, circuit_breaker: CircuitBreaker, tmp_path) -> None:
        """Multiple files should all be prepended."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("content A")
        f2.write_text("content B")

        result = agy._augment_prompt_with_context(
            "User prompt", [str(f1), str(f2)]
        )
        assert "content A" in result
        assert "content B" in result

    def test_large_file_truncation(self, circuit_breaker: CircuitBreaker, tmp_path) -> None:
        """Files exceeding 200KB should be truncated."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        test_file = tmp_path / "large.py"
        large_content = "x" * 250 * 1024
        test_file.write_text(large_content)

        result = agy._augment_prompt_with_context(
            "User prompt", [str(test_file)]
        )
        assert "TRUNCATED" in result
        assert len(result) < 300 * 1024

    def test_read_error_handled(self, circuit_breaker: CircuitBreaker, tmp_path) -> None:
        """File read errors should generate error placeholder."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        test_file = tmp_path / "test.py"
        test_file.write_text("content")

        def mock_open_error(*args, **kwargs):
            raise OSError("Permission denied")

        with patch("builtins.open", side_effect=mock_open_error):
            result = agy._augment_prompt_with_context(
                "User prompt", [str(test_file)]
            )
        assert "Error reading file" in result


# ============================================================================
# _build_command Tests
# ============================================================================


class TestBuildCommand:
    """_build_command method tests."""

    def test_basic_command(self, circuit_breaker: CircuitBreaker) -> None:
        """Basic command should include all required flags."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        cmd = agy._build_command(
            prompt="Hello",
            system_prompt=None,
            model="gemini-2.5-flash",
            temperature=0.7,
            max_output_tokens=8192,
            media_inputs=[],
            extra_flags=None,
        )
        assert cmd[0] == "agy"
        assert "-p" in cmd
        assert "Hello" in cmd
        assert "--non-interactive" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--yolo" in cmd
        assert "--model" in cmd
        assert "gemini-2.5-flash" in cmd
        assert "--temperature" in cmd
        assert "--max-output-tokens" in cmd

    def test_with_system_prompt(self, circuit_breaker: CircuitBreaker) -> None:
        """system_prompt should add --system-prompt flag."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        cmd = agy._build_command(
            prompt="Hello",
            system_prompt="You are a helpful assistant",
            model="gemini-2.5-flash",
            temperature=0.7,
            max_output_tokens=1024,
            media_inputs=[],
            extra_flags=None,
        )
        assert "--system-prompt" in cmd
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "You are a helpful assistant"

    def test_with_image_media(self, circuit_breaker: CircuitBreaker) -> None:
        """Image media should use --image flag."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        mi = MediaInput(path="/tmp/test.png", mime_type="image/png")
        cmd = agy._build_command(
            prompt="Describe this image",
            system_prompt=None,
            model="gemini-2.5-flash",
            temperature=0.7,
            max_output_tokens=1024,
            media_inputs=[mi],
            extra_flags=None,
        )
        assert "--image" in cmd
        assert "/tmp/test.png" in cmd

    def test_with_pdf_media(self, circuit_breaker: CircuitBreaker) -> None:
        """PDF media (non File API) should use --file flag."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        mi = MediaInput(path="/tmp/doc.pdf", mime_type="application/pdf")
        cmd = agy._build_command(
            prompt="Summarize this PDF",
            system_prompt=None,
            model="gemini-2.5-flash",
            temperature=0.7,
            max_output_tokens=1024,
            media_inputs=[mi],
            extra_flags=None,
        )
        assert "--file" in cmd
        assert "/tmp/doc.pdf" in cmd

    def test_with_file_api_media(self, circuit_breaker: CircuitBreaker) -> None:
        """File API media with file_uri should use --file-uri flag."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        mi = MediaInput(
            path="/tmp/large.pdf",
            mime_type="application/pdf",
            use_file_api=True,
        )
        mi.file_uri = "gs://bucket/file.pdf"
        cmd = agy._build_command(
            prompt="Summarize",
            system_prompt=None,
            model="gemini-2.5-flash",
            temperature=0.7,
            max_output_tokens=1024,
            media_inputs=[mi],
            extra_flags=None,
        )
        assert "--file-uri" in cmd
        assert "gs://bucket/file.pdf" in cmd

    def test_with_audio_media(self, circuit_breaker: CircuitBreaker) -> None:
        """Audio media should use --file flag."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        mi = MediaInput(path="/tmp/audio.mp3", mime_type="audio/mpeg")
        cmd = agy._build_command(
            prompt="Transcribe",
            system_prompt=None,
            model="gemini-2.5-flash",
            temperature=0.7,
            max_output_tokens=1024,
            media_inputs=[mi],
            extra_flags=None,
        )
        assert "--file" in cmd

    def test_with_video_media(self, circuit_breaker: CircuitBreaker) -> None:
        """Video media should use --file flag."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        mi = MediaInput(path="/tmp/video.mp4", mime_type="video/mp4")
        cmd = agy._build_command(
            prompt="Analyze this video",
            system_prompt=None,
            model="gemini-2.5-flash",
            temperature=0.7,
            max_output_tokens=1024,
            media_inputs=[mi],
            extra_flags=None,
        )
        assert "--file" in cmd

    def test_with_extra_flags(self, circuit_breaker: CircuitBreaker) -> None:
        """extra_flags should be appended to end of command."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        cmd = agy._build_command(
            prompt="Hello",
            system_prompt=None,
            model="gemini-2.5-flash",
            temperature=0.7,
            max_output_tokens=1024,
            media_inputs=[],
            extra_flags=["--verbose", "--dry-run"],
        )
        assert "--verbose" in cmd
        assert "--dry-run" in cmd


# ============================================================================
# Stream Parse Tests
# ============================================================================


class TestStreamParse:
    """stream-json parse tests."""

    def test_parse_valid_stream_json(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Valid stream-json lines should be correctly parsed."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        lines = [
            json.dumps({"type": "status", "stage": "start"}),
            json.dumps({"type": "text", "content": "Hello"}),
            json.dumps({"type": "usage", "input_tokens": 5, "output_tokens": 1}),
        ]
        events = agy._parse_stream_json(lines)
        assert len(events) == 3
        assert events[0]["type"] == "status"
        assert events[1]["type"] == "text"

    def test_parse_empty_lines_skipped(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Empty lines should be skipped."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        events = agy._parse_stream_json(["", "  ", "\n"])
        assert len(events) == 0

    def test_parse_error_event_raises_stream_error(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Error event should raise AgyStreamParseError."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        lines = [json.dumps({"type": "error", "message": "Something broke"})]
        with pytest.raises(AgyStreamParseError) as exc_info:
            agy._parse_stream_json(lines)
        assert "error event" in str(exc_info.value).lower()

    def test_parse_unrecognized_type_marked(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Unrecognized message types should be marked."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        lines = [json.dumps({"type": "weird_custom", "content": "test"})]
        events = agy._parse_stream_json(lines)
        assert len(events) == 1
        assert events[0].get("_unrecognized_type") is True

    def test_parse_status_complete_transition(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """status stage=complete should trigger COMPLETE state transition."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        lines = [
            json.dumps({"type": "status", "stage": "start"}),
            json.dumps({"type": "status", "stage": "complete"}),
        ]
        events = agy._parse_stream_json(lines)
        assert len(events) == 2

    def test_parse_usage_triggers_complete(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """usage event should trigger COMPLETE from IDLE."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        lines = [json.dumps({"type": "usage", "input_tokens": 10, "output_tokens": 5})]
        events = agy._parse_stream_json(lines)
        assert len(events) == 1

    def test_parse_text_after_status(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Text event after status should be correctly parsed."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        lines = [
            json.dumps({"type": "status", "stage": "start"}),
            json.dumps({"type": "text", "content": "Hello"}),
            json.dumps({"type": "usage", "input_tokens": 5, "output_tokens": 1}),
        ]
        events = agy._parse_stream_json(lines)
        assert len(events) == 3
        assert events[1]["type"] == "text"

    def test_parse_json_decode_error_with_braces(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Invalid JSON with braces should trigger ERROR state."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        lines = [
            '{"type": "text", "content": "broken json"',  # invalid JSON with braces
        ]
        events = agy._parse_stream_json(lines)
        assert len(events) == 1

    def test_parse_stream_error_raises_on_parse_errors(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Accumulated parse errors should raise AgyStreamParseError."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        lines = [
            json.dumps({"type": "status", "stage": "start"}),
            '{"type": "broken", unquoted: value}',  # Invalid JSON (unquoted value)
        ]
        with pytest.raises(AgyStreamParseError) as exc_info:
            agy._parse_stream_json(lines)
        assert "parse error" in str(exc_info.value).lower()

    def test_parse_stream_error_event_at_end(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Error event at end should trigger AgyStreamParseError."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        lines = [json.dumps({"type": "error", "message": "fatal error"})]
        with pytest.raises(AgyStreamParseError) as exc_info:
            agy._parse_stream_json(lines)
        assert "error event" in str(exc_info.value).lower()


# ============================================================================
# Text Extraction Tests
# ============================================================================


class TestTextExtraction:
    """Text extraction tests."""

    def test_extract_text_from_events(self) -> None:
        """Aggregate text from multiple text events correctly."""
        events = [
            {"type": "text", "content": "Hello "},
            {"type": "text", "content": "World"},
        ]
        text = AgyClient._extract_text(events)
        assert text == "Hello World"

    def test_extract_text_empty(self) -> None:
        """Empty events should return empty string."""
        assert AgyClient._extract_text([]) == ""

    def test_extract_text_from_status_events(self) -> None:
        """Status event content should be extracted when long enough."""
        events = [
            {"type": "status", "content": "This is output from status event"},
        ]
        text = AgyClient._extract_text(events)
        assert text == "This is output from status event"

    def test_extract_text_from_status_text_field(self) -> None:
        """Status event text field should be used as fallback."""
        events = [
            {"type": "status", "text": "Alternative text field"},
        ]
        text = AgyClient._extract_text(events)
        assert text == "Alternative text field"

    def test_extract_text_short_status_content_ignored(self) -> None:
        """Short status content (<=5 chars) should be ignored."""
        events = [
            {"type": "status", "content": "short"},
        ]
        text = AgyClient._extract_text(events)
        assert text == ""


# ============================================================================
# Token Extraction Tests
# ============================================================================


class TestTokenExtraction:
    """Token extraction tests."""

    def test_extract_tokens_from_usage_event(self) -> None:
        """Extract token counts from usage event correctly."""
        events = [
            {"type": "usage", "input_tokens": 100, "output_tokens": 50},
        ]
        in_tok, out_tok, total = AgyClient._extract_tokens(events)
        assert in_tok == 100
        assert out_tok == 50
        assert total == 150

    def test_extract_tokens_no_events(self) -> None:
        """No events should return 0."""
        in_tok, out_tok, total = AgyClient._extract_tokens([])
        assert in_tok == 0
        assert out_tok == 0
        assert total == 0

    def test_extract_tokens_from_status_event(self) -> None:
        """Extract token counts from status event correctly."""
        events = [
            {"type": "status", "input_tokens": 30, "output_tokens": 15},
        ]
        in_tok, out_tok, total = AgyClient._extract_tokens(events)
        assert in_tok == 30
        assert out_tok == 15
        assert total == 45

    def test_extract_tokens_from_status_tokens_object(self) -> None:
        """Extract from nested tokens object in status event."""
        events = [
            {
                "type": "status",
                "tokens": {"input": 80, "output": 40},
            },
        ]
        in_tok, out_tok, total = AgyClient._extract_tokens(events)
        assert in_tok == 80
        assert out_tok == 40
        assert total == 120

    def test_extract_tokens_total_from_usage(self) -> None:
        """total_tokens from usage event should be consistent."""
        events = [
            {"type": "usage", "input_tokens": 0, "output_tokens": 0, "total_tokens": 200},
        ]
        in_tok, out_tok, total = AgyClient._extract_tokens(events)
        assert total == 200

    def test_extract_tokens_mixed_sources(self) -> None:
        """Mixed usage and status events should take maximum values."""
        events = [
            {"type": "usage", "input_tokens": 50, "output_tokens": 25},
            {"type": "status", "input_tokens": 100, "output_tokens": 50},
        ]
        in_tok, out_tok, total = AgyClient._extract_tokens(events)
        assert in_tok == 100
        assert out_tok == 50
        assert total == 150

    def test_extract_tokens_input_from_total_minus_output(self) -> None:
        """When input_tokens=0, should attempt total - output."""
        events = [
            {"type": "usage", "input_tokens": 0, "output_tokens": 50, "total_tokens": 120},
        ]
        in_tok, out_tok, total = AgyClient._extract_tokens(events)
        assert in_tok == 70


# ============================================================================
# Model and Finish Reason Tests
# ============================================================================


class TestExtractModelAndFinish:
    """_extract_model_and_finish method tests."""

    def test_default_values_when_no_events(self) -> None:
        """No events should return defaults."""
        model, finish = AgyClient._extract_model_and_finish(
            [], "gemini-2.5-flash"
        )
        assert model == "gemini-2.5-flash"
        assert finish == "UNKNOWN"

    def test_extract_from_status_event(self) -> None:
        """Extract model and finish_reason from status event."""
        events = [
            {
                "type": "status",
                "stage": "complete",
                "model": "gemini-2.5-pro",
                "finish_reason": "stop",
            },
        ]
        model, finish = AgyClient._extract_model_and_finish(
            events, "gemini-2.5-flash"
        )
        assert model == "gemini-2.5-pro"
        assert finish == "STOP"

    def test_extract_from_usage_event(self) -> None:
        """Extract model and finish_reason from usage event."""
        events = [
            {
                "type": "usage",
                "model": "gemini-2.5-flash",
                "finish_reason": "max_tokens",
            },
        ]
        model, finish = AgyClient._extract_model_and_finish(
            events, "gemini-2.5-pro"
        )
        assert model == "gemini-2.5-flash"
        assert finish == "MAX_TOKENS"

    def test_status_event_without_stage_complete(self) -> None:
        """Status event without complete stage should not set finish_reason."""
        events = [
            {
                "type": "status",
                "stage": "start",
                "model": "gemini-2.5-flash",
            },
        ]
        model, finish = AgyClient._extract_model_and_finish(
            events, "fallback-model"
        )
        assert model == "gemini-2.5-flash"
        assert finish == "UNKNOWN"


# ============================================================================
# Find Error Event Tests
# ============================================================================


class TestFindErrorEvent:
    """_find_error_event method tests."""

    def test_find_error_event_found(self) -> None:
        """Should return first error event."""
        events = [
            {"type": "status", "stage": "start"},
            {"type": "error", "message": "first error"},
            {"type": "error", "message": "second error"},
        ]
        found = AgyClient._find_error_event(events)
        assert found is not None
        assert found["message"] == "first error"

    def test_find_error_event_not_found(self) -> None:
        """No error events should return None."""
        events = [
            {"type": "status", "stage": "start"},
            {"type": "text", "content": "ok"},
        ]
        found = AgyClient._find_error_event(events)
        assert found is None

    def test_find_error_event_empty_list(self) -> None:
        """Empty list should return None."""
        assert AgyClient._find_error_event([]) is None


# ============================================================================
# Cost Calculation Tests
# ============================================================================


class TestCalculateCost:
    """_calculate_cost method tests."""

    def test_calculate_zero_tokens(self) -> None:
        """Zero tokens should give zero cost."""
        cost = AgyClient._calculate_cost(0, 0)
        assert cost == 0.0

    def test_calculate_cost_input_only(self) -> None:
        """Input-only token cost calculation."""
        cost = AgyClient._calculate_cost(1000, 0)
        assert cost == pytest.approx(0.00015, rel=0.01)

    def test_calculate_cost_output_only(self) -> None:
        """Output-only token cost calculation."""
        cost = AgyClient._calculate_cost(0, 1000)
        assert cost == pytest.approx(0.0006, rel=0.01)

    def test_calculate_cost_both(self) -> None:
        """Combined input and output token cost calculation."""
        cost = AgyClient._calculate_cost(500, 100)
        expected = (500 / 1000.0) * 0.00015 + (100 / 1000.0) * 0.0006
        assert cost == pytest.approx(expected, rel=0.01)


# ============================================================================
# Backoff Delay Tests
# ============================================================================


class TestBackoffDelay:
    """Backoff delay calculation tests."""

    def test_backoff_delay_increases(self) -> None:
        """Backoff delay should increase exponentially with attempts."""
        d0 = AgyClient._backoff_delay(0, 1000, 16000)
        d1 = AgyClient._backoff_delay(1, 1000, 16000)
        d2 = AgyClient._backoff_delay(2, 1000, 16000)
        assert 1000 <= d0 <= 1250
        assert 2000 <= d1 <= 2500
        assert 4000 <= d2 <= 5000

    def test_backoff_delay_capped(self) -> None:
        """Backoff delay should not exceed max_ms (with jitter)."""
        max_jitter = 8000 * 0.25
        delay = AgyClient._backoff_delay(10, 1000, 8000)
        assert delay <= int(8000 + max_jitter)


# ============================================================================
# Subprocess Mock Tests
# ============================================================================


class TestSubprocessMock:
    """Subprocess call mock tests."""

    def test_subprocess_file_not_found(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Subprocess should raise AgyNotInstalledError for FileNotFoundError."""
        with pytest.raises(AgyNotInstalledError):
            agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
            agy._spawn_and_monitor(["agy", "--version"], timeout_ms=5000)


# ============================================================================
# Send Signal Tests
# ============================================================================


class TestSendSignal:
    """_send_signal static method tests."""

    def test_send_signal_success(self) -> None:
        """Normal process should receive signal successfully."""
        proc = MagicMock()
        AgyClient._send_signal(proc, signal.SIGTERM)
        proc.send_signal.assert_called_once_with(signal.SIGTERM)

    def test_send_signal_process_lookup_error(self) -> None:
        """ProcessLookupError should be suppressed."""
        proc = MagicMock()
        proc.send_signal.side_effect = ProcessLookupError()
        # Should not raise
        AgyClient._send_signal(proc, signal.SIGTERM)

    def test_send_signal_other_exception(self) -> None:
        """Other exceptions should also be suppressed."""
        proc = MagicMock()
        proc.send_signal.side_effect = RuntimeError("unexpected")
        # Should not raise
        AgyClient._send_signal(proc, signal.SIGTERM)


# ============================================================================
# Circuit Breaker Integration Tests
# ============================================================================


class TestCircuitBreakerIntegration:
    """AgyClient circuit breaker integration tests."""

    def test_read_circuit_state_closed(self, circuit_breaker: CircuitBreaker) -> None:
        """Read state from CLOSED circuit breaker."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        state = agy._read_circuit_state()
        assert state == "CLOSED"

    def test_read_circuit_state_open(
        self, open_circuit_breaker: CircuitBreaker
    ) -> None:
        """Read state from OPEN circuit breaker."""
        agy = AgyClient(mode="auto", circuit_breaker=open_circuit_breaker)
        state = agy._read_circuit_state()
        assert state == "OPEN"

    def test_read_circuit_state_attribute_error(self) -> None:
        """Fallback to CLOSED when circuit breaker has no state attribute."""
        cb = MagicMock(spec=[])
        agy = AgyClient(mode="auto", circuit_breaker=cb)
        state = agy._read_circuit_state()
        assert state == "CLOSED"

    def test_circuit_cooldown_remaining_closed(
        self, circuit_breaker: CircuitBreaker
    ) -> None:
        """Cooldown should be 0 when circuit breaker CLOSED."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        remaining = agy._circuit_cooldown_remaining()
        assert remaining == 0.0

    def test_circuit_cooldown_remaining_open(
        self, open_circuit_breaker: CircuitBreaker
    ) -> None:
        """Cooldown should report remaining seconds when OPEN."""
        agy = AgyClient(mode="auto", circuit_breaker=open_circuit_breaker)
        remaining = agy._circuit_cooldown_remaining()
        assert remaining >= 0.0

    def test_record_success_calls_on_success(self, circuit_breaker: CircuitBreaker) -> None:
        """_record_success should call circuit breaker on_success."""
        cb = MagicMock()
        cb.on_success = MagicMock()
        agy = AgyClient(mode="auto", circuit_breaker=cb)
        agy._record_success()
        cb.on_success.assert_called_once()

    def test_record_success_no_method(self) -> None:
        """Gracefully handle circuit breaker without on_success."""
        cb = MagicMock(spec=[])
        agy = AgyClient(mode="auto", circuit_breaker=cb)
        # Should not raise
        agy._record_success()

    def test_record_failure_calls_on_failure(self, circuit_breaker: CircuitBreaker) -> None:
        """_record_failure should call circuit breaker on_failure."""
        cb = MagicMock()
        cb.on_failure = MagicMock()
        agy = AgyClient(mode="auto", circuit_breaker=cb)
        agy._record_failure("test error")
        cb.on_failure.assert_called_once_with(reason="test error")

    def test_circuit_transition_to(self, circuit_breaker: CircuitBreaker) -> None:
        """_circuit_transition_to should call breaker state transition."""
        cb = MagicMock()
        cb._transition_to = MagicMock()
        agy = AgyClient(mode="auto", circuit_breaker=cb)
        agy._circuit_transition_to("OPEN", reason="test trip")
        cb._transition_to.assert_called_once()

    def test_record_success_report_success_fallback(self) -> None:
        """Fallback to report_success method."""
        cb = MagicMock()
        cb.report_success = MagicMock()
        del cb.on_success
        agy = AgyClient(mode="auto", circuit_breaker=cb)
        agy._record_success()
        cb.report_success.assert_called_once()

    def test_record_failure_report_failure_fallback(self) -> None:
        """Fallback to report_failure method."""
        cb = MagicMock()
        cb.report_failure = MagicMock()
        del cb.on_failure
        agy = AgyClient(mode="auto", circuit_breaker=cb)
        agy._record_failure("fallback error")
        cb.report_failure.assert_called_once()


# ============================================================================
# Telemetry Tests
# ============================================================================


class TestTelemetry:
    """get_telemetry and reset_telemetry tests."""

    def test_get_telemetry_initial(self, circuit_breaker: CircuitBreaker) -> None:
        """Initial telemetry should all be zero."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        telemetry = agy.get_telemetry()
        assert telemetry["total_spawns"] == 0
        assert telemetry["total_successful_completions"] == 0
        assert telemetry["total_timeouts"] == 0
        assert telemetry["total_stream_parse_errors"] == 0
        assert telemetry["avg_response_time_ms"] == 0

    def test_get_telemetry_with_data(self, circuit_breaker: CircuitBreaker) -> None:
        """Telemetry should correctly report accumulated data."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        agy._total_spawns = 10
        agy._total_successful_completions = 8
        agy._total_timeouts = 1
        agy._total_latency_ms = 1600

        telemetry = agy.get_telemetry()
        assert telemetry["total_spawns"] == 10
        assert telemetry["total_successful_completions"] == 8
        assert telemetry["total_timeouts"] == 1
        assert telemetry["avg_response_time_ms"] == 200

    def test_reset_telemetry(self, circuit_breaker: CircuitBreaker) -> None:
        """reset_telemetry should zero all counters."""
        agy = AgyClient(mode="auto", circuit_breaker=circuit_breaker)
        agy._total_spawns = 10
        agy._total_successful_completions = 8
        agy._total_timeouts = 1
        agy._total_stream_parse_errors = 2
        agy._total_latency_ms = 1600
        agy._last_command = "some cmd"
        agy._last_exit_code = 0
        agy._last_stream_json_sample = [{"type": "text"}]

        agy.reset_telemetry()

        assert agy._total_spawns == 0
        assert agy._total_successful_completions == 0
        assert agy._total_timeouts == 0
        assert agy._total_stream_parse_errors == 0
        assert agy._total_latency_ms == 0
        assert agy._last_command is None
        assert agy._last_exit_code is None
        assert agy._last_stream_json_sample is None


# ============================================================================
# check_quota Tests
# ============================================================================


class TestCheckQuota:
    """check_quota method tests."""

    def test_check_quota_success(self, circuit_breaker: CircuitBreaker) -> None:
        """Quota check successful with AVAILABLE status."""
        from loop_antigravity.agy_client import AgyResult

        cb = MagicMock()
        cb.state = "CLOSED"
        agy = AgyClient(mode="auto", circuit_breaker=cb)

        mock_result = AgyResult(
            text="pong",
            tokens_input=3,
            tokens_output=1,
            stream_events=[
                {"type": "status", "quota": {
                    "rpm_used": 10, "rpm_limit": 100,
                    "tpd_used": 1000, "tpd_limit": 10000,
                    "current_usage_pct": 50,
                }},
                {"type": "text", "content": "pong"},
                {"type": "usage", "input_tokens": 3, "output_tokens": 1,
                 "quota": {"remaining": 500}},
            ],
        )

        with patch.object(agy, "invoke", create=True, return_value=mock_result):
            result = agy.check_quota()
            assert result.available is True
            assert result.status_code == "AVAILABLE"
            assert result.rpm_used == 10
            assert result.rpm_limit == 100
            assert result.tpd_used == 1000
            assert result.tpd_limit == 10000

    def test_check_quota_warning_threshold(self, circuit_breaker: CircuitBreaker) -> None:
        """Usage > 80% should return WARNING."""
        from loop_antigravity.agy_client import AgyResult

        cb = MagicMock()
        cb.state = "CLOSED"
        agy = AgyClient(mode="auto", circuit_breaker=cb)

        mock_result = AgyResult(
            text="pong",
            tokens_input=1,
            tokens_output=1,
            stream_events=[
                {"type": "status", "quota": {
                    "rpm_used": 85, "rpm_limit": 100,
                    "current_usage_pct": 90,
                }},
                {"type": "text", "content": "pong"},
                {"type": "usage", "input_tokens": 1, "output_tokens": 1},
            ],
        )

        with patch.object(agy, "invoke", create=True, return_value=mock_result):
            result = agy.check_quota()
            assert result.available is True
            assert result.status_code == "WARNING"

    def test_check_quota_exhaustion(self, circuit_breaker: CircuitBreaker) -> None:
        """Quota exhaustion should return EXHAUSTED."""
        cb = MagicMock()
        cb.state = "CLOSED"
        agy = AgyClient(mode="auto", circuit_breaker=cb)

        with patch.object(agy, "invoke", create=True) as mock_invoke:
            mock_invoke.side_effect = AgyQuotaExhausted(
                "quota exhausted",
                retry_after_seconds=120,
                estimated_recovery_at="2026-06-13T14:00:00Z",
            )
            result = agy.check_quota()
            assert result.available is False
            assert result.status_code == "EXHAUSTED"
            assert result.estimated_recovery_at == "2026-06-13T14:00:00Z"

    def test_check_quota_circuit_open(self, circuit_breaker: CircuitBreaker) -> None:
        """Circuit breaker open should return UNKNOWN."""
        cb = MagicMock()
        cb.state = "CLOSED"
        agy = AgyClient(mode="auto", circuit_breaker=cb)

        with patch.object(agy, "invoke", create=True) as mock_invoke:
            mock_invoke.side_effect = AgyCircuitOpenError()
            result = agy.check_quota()
            assert result.available is False
            assert result.status_code == "UNKNOWN"

    def test_check_quota_timeout(self, circuit_breaker: CircuitBreaker) -> None:
        """Timeout during quota check should return UNKNOWN."""
        cb = MagicMock()
        cb.state = "CLOSED"
        agy = AgyClient(mode="auto", circuit_breaker=cb)

        with patch.object(agy, "invoke", create=True) as mock_invoke:
            mock_invoke.side_effect = AgyTimeoutError(timeout_ms=30000)
            result = agy.check_quota()
            assert result.available is False
            assert result.status_code == "UNKNOWN"

    def test_check_quota_generic_exception(self, circuit_breaker: CircuitBreaker) -> None:
        """Generic exception catch-all should return UNKNOWN."""
        cb = MagicMock()
        cb.state = "CLOSED"
        agy = AgyClient(mode="auto", circuit_breaker=cb)

        with patch.object(agy, "invoke", create=True) as mock_invoke:
            mock_invoke.side_effect = ValueError("unexpected error")
            result = agy.check_quota()
            assert result.available is False
            assert result.status_code == "UNKNOWN"

