"""Tests for backend_protocol.py -- GeminiBackend Protocol and data types."""

from __future__ import annotations

import pytest

from loop_antigravity.backend_protocol import (
    GeminiBackend,
    GeminiResult,
    HealthStatus,
    MediaInput,
    QuotaStatus,
)


# ============================================================================
# MediaInput tests
# ============================================================================


class TestMediaInput:
    """Tests for the MediaInput dataclass."""

    def test_media_input_construction(self) -> None:
        """MediaInput should be constructable with required fields."""
        mi = MediaInput(
            path="/tmp/img.png",
            mime_type="image/png",
            media_type="image",
            size_bytes=1024,
        )
        assert mi.path == "/tmp/img.png"
        assert mi.mime_type == "image/png"
        assert mi.media_type == "image"
        assert mi.size_bytes == 1024

    def test_media_input_default_use_file_api(self) -> None:
        """use_file_api should default to False."""
        mi = MediaInput(
            path="/tmp/img.png",
            mime_type="image/png",
            media_type="image",
            size_bytes=1024,
        )
        assert mi.use_file_api is False

    def test_media_input_use_file_api_true(self) -> None:
        """use_file_api can be set to True for large files."""
        mi = MediaInput(
            path="/tmp/big.pdf",
            mime_type="application/pdf",
            media_type="pdf",
            size_bytes=30_000_000,
            use_file_api=True,
        )
        assert mi.use_file_api is True

    def test_media_input_various_types(self) -> None:
        """MediaInput should support all media types."""
        for media_type in ("image", "pdf", "audio", "video", "text"):
            mi = MediaInput(
                path=f"/tmp/test.{media_type}",
                mime_type="application/octet-stream",
                media_type=media_type,
                size_bytes=100,
            )
            assert mi.media_type == media_type


# ============================================================================
# GeminiResult tests
# ============================================================================


class TestGeminiResult:
    """Tests for the GeminiResult dataclass."""

    def test_gemini_result_defaults(self) -> None:
        """GeminiResult should have correct default values."""
        result = GeminiResult()
        assert result.text == ""
        assert result.usage is None
        assert result.stop_reason == "end_turn"
        assert result.tool_calls == []
        assert result.reasoning is None
        assert result.duration_ms == 0
        assert result.raw_response is None

    def test_gemini_result_with_text(self) -> None:
        """GeminiResult should store text content."""
        result = GeminiResult(text="Hello, World!")
        assert result.text == "Hello, World!"

    def test_gemini_result_with_usage(self) -> None:
        """GeminiResult should store usage metadata."""
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        result = GeminiResult(text="ok", usage=usage)
        assert result.usage == usage

    def test_gemini_result_stop_reasons(self) -> None:
        """GeminiResult should accept all defined stop reasons."""
        for reason in ("end_turn", "max_tokens", "safety", "tool_use", "error"):
            result = GeminiResult(stop_reason=reason)
            assert result.stop_reason == reason

    def test_gemini_result_with_tool_calls(self) -> None:
        """GeminiResult should store tool call data."""
        tool_calls = [{"name": "search", "args": {"query": "test"}}]
        result = GeminiResult(tool_calls=tool_calls)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "search"

    def test_gemini_result_with_reasoning(self) -> None:
        """GeminiResult should store reasoning text."""
        result = GeminiResult(reasoning="Step-by-step reasoning...")
        assert result.reasoning == "Step-by-step reasoning..."

    def test_gemini_result_duration(self) -> None:
        """GeminiResult should track call duration in milliseconds."""
        result = GeminiResult(duration_ms=1500)
        assert result.duration_ms == 1500

    def test_gemini_result_raw_response(self) -> None:
        """GeminiResult can hold a raw response object."""
        raw = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
        result = GeminiResult(raw_response=raw)
        assert result.raw_response == raw


# ============================================================================
# HealthStatus tests
# ============================================================================


class TestHealthStatus:
    """Tests for the HealthStatus dataclass."""

    def test_health_status_defaults(self) -> None:
        """HealthStatus should have sensible defaults."""
        hs = HealthStatus()
        assert hs.ok is False
        assert hs.authenticated is False
        assert hs.version == ""
        assert hs.backend_type == ""
        assert hs.message == ""
        assert hs.checked_at == ""

    def test_health_status_healthy(self) -> None:
        """HealthStatus should represent a healthy backend."""
        hs = HealthStatus(
            ok=True,
            authenticated=True,
            version="1.0.0",
            backend_type="agy_cli",
            message="All systems operational",
            checked_at="2026-06-13T12:00:00Z",
        )
        assert hs.ok is True
        assert hs.authenticated is True
        assert hs.version == "1.0.0"
        assert hs.backend_type == "agy_cli"

    def test_health_status_unhealthy(self) -> None:
        """HealthStatus should represent an unhealthy backend."""
        hs = HealthStatus(
            ok=False,
            authenticated=False,
            message="Connection refused",
            checked_at="2026-06-13T12:00:00Z",
        )
        assert hs.ok is False
        assert hs.message == "Connection refused"

    def test_health_status_backend_types(self) -> None:
        """HealthStatus should accept both backend types."""
        for bt in ("agy_cli", "gemini_sdk"):
            hs = HealthStatus(backend_type=bt)
            assert hs.backend_type == bt


# ============================================================================
# QuotaStatus tests
# ============================================================================


class TestQuotaStatus:
    """Tests for the QuotaStatus dataclass."""

    def test_quota_status_defaults(self) -> None:
        """QuotaStatus should have sensible defaults."""
        qs = QuotaStatus()
        assert qs.available is True
        assert qs.remaining == -1
        assert qs.limit == -1
        assert qs.reset_at == ""
        assert qs.message == ""

    def test_quota_status_available(self) -> None:
        """QuotaStatus should represent available quota."""
        qs = QuotaStatus(
            available=True,
            remaining=100,
            limit=1000,
            reset_at="2026-06-14T00:00:00Z",
            message="OK",
        )
        assert qs.available is True
        assert qs.remaining == 100
        assert qs.limit == 1000
        assert qs.reset_at == "2026-06-14T00:00:00Z"

    def test_quota_status_exhausted(self) -> None:
        """QuotaStatus should represent exhausted quota."""
        qs = QuotaStatus(
            available=False,
            remaining=0,
            limit=1000,
            message="Quota exhausted",
        )
        assert qs.available is False
        assert qs.remaining == 0
        assert qs.message == "Quota exhausted"


# ============================================================================
# GeminiBackend Protocol tests
# ============================================================================


class TestGeminiBackendProtocol:
    """Tests for the GeminiBackend Protocol (runtime_checkable)."""

    def test_protocol_is_runtime_checkable(self) -> None:
        """GeminiBackend should be decorated with @runtime_checkable."""
        from typing import runtime_checkable
        assert hasattr(GeminiBackend, "_is_runtime_protocol")

    def test_class_implementing_protocol_passes_check(self) -> None:
        """A class that implements all protocol methods should match."""

        class MockBackend:
            @property
            def backend_type(self) -> str:
                return "mock"

            def invoke(self, prompt, *, system_prompt=None,
                       context_files=None, media_inputs=None,
                       model="gemini-2.5-flash", temperature=0.7,
                       max_output_tokens=8192, timeout_ms=300_000):
                return GeminiResult(text="mock response")

            def check_health(self):
                return HealthStatus(ok=True)

            def check_quota(self):
                return QuotaStatus(available=True)

        backend = MockBackend()
        assert isinstance(backend, GeminiBackend)

    def test_empty_class_fails_check(self) -> None:
        """A class with no methods should not match the protocol."""

        class EmptyBackend:
            pass

        backend = EmptyBackend()
        assert not isinstance(backend, GeminiBackend)

    def test_partial_implementation_fails_check(self) -> None:
        """A class missing a method should not match."""

        class PartialBackend:
            @property
            def backend_type(self) -> str:
                return "partial"

            def check_health(self):
                return HealthStatus(ok=True)

        backend = PartialBackend()
        assert not isinstance(backend, GeminiBackend)

    def test_protocol_methods_are_callable(self) -> None:
        """Protocol methods should be visible as attributes."""
        assert hasattr(GeminiBackend, "invoke")
        assert hasattr(GeminiBackend, "check_health")
        assert hasattr(GeminiBackend, "check_quota")
        assert hasattr(GeminiBackend, "backend_type")


# ============================================================================
# Integration: backend-protocol data flow
# ============================================================================


class TestBackendProtocolDataFlow:
    """End-to-end tests simulating how upper layers use the protocol."""

    def test_full_invoke_flow(self) -> None:
        """Simulate a complete invoke -> result -> quota check flow."""

        class TestBackend:
            @property
            def backend_type(self) -> str:
                return "test"

            def invoke(self, prompt, *, system_prompt=None,
                       context_files=None, media_inputs=None,
                       model="gemini-2.5-flash", temperature=0.7,
                       max_output_tokens=8192, timeout_ms=300_000):
                return GeminiResult(
                    text=f"Echo: {prompt}",
                    usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
                    stop_reason="end_turn",
                    duration_ms=42,
                )

            def check_health(self):
                return HealthStatus(
                    ok=True,
                    authenticated=True,
                    version="0.1.0",
                    backend_type=self.backend_type,
                    checked_at="2026-06-13T00:00:00Z",
                )

            def check_quota(self):
                return QuotaStatus(available=True, remaining=999, limit=1000)

        backend = TestBackend()
        assert isinstance(backend, GeminiBackend)

        # Health check
        health = backend.check_health()
        assert health.ok is True
        assert health.backend_type == "test"

        # Invoke
        result = backend.invoke("hello")
        assert "Echo: hello" in result.text
        assert result.usage is not None
        assert result.usage["total_tokens"] == 8

        # Quota check
        quota = backend.check_quota()
        assert quota.available is True
        assert quota.remaining == 999

    def test_media_inputs_passed_through(self) -> None:
        """MediaInput list should be passed to invoke without error."""

        media = [
            MediaInput(path="/tmp/a.png", mime_type="image/png",
                       media_type="image", size_bytes=500),
        ]

        class TestBackend:
            backend_type = "test"

            def invoke(self, prompt, *, system_prompt=None,
                       context_files=None, media_inputs=None,
                       model="gemini-2.5-flash", temperature=0.7,
                       max_output_tokens=8192, timeout_ms=300_000):
                assert media_inputs is not None
                assert len(media_inputs) == 1
                assert media_inputs[0].path == "/tmp/a.png"
                return GeminiResult(text="ok")

            def check_health(self):
                return HealthStatus()

            def check_quota(self):
                return QuotaStatus()

        backend = TestBackend()
        result = backend.invoke("describe this image", media_inputs=media)
        assert result.text == "ok"
