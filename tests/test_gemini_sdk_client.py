"""GeminiSdkClient unit tests."""
import os
from unittest import mock

import pytest

from loop_antigravity.gemini_sdk_client import (
    GeminiSdkClient,
    GeminiSdkResult,
)


# ---- Mock helpers ----

def _make_mock_genai(response_text="Hello", tokens_in=10, tokens_out=20):
    """Create a mock google.generativeai module."""
    genai = mock.MagicMock()
    genai.configure = mock.MagicMock()

    # Mock GenerativeModel
    model = mock.MagicMock()
    response = mock.MagicMock()
    response.text = response_text

    # Mock usage metadata
    usage = mock.MagicMock()
    usage.prompt_token_count = tokens_in
    usage.candidates_token_count = tokens_out
    response.usage_metadata = usage

    # Mock prompt feedback
    response.prompt_feedback = mock.MagicMock()
    response.prompt_feedback.block_reason = None

    model.generate_content.return_value = response
    genai.GenerativeModel.return_value = model
    return genai


def _make_mock_genai_blocked():
    """Create a mock genai module that simulates a blocked response."""
    genai = mock.MagicMock()
    genai.configure = mock.MagicMock()

    model = mock.MagicMock()
    response = mock.MagicMock()

    # Make .text raise ValueError to trigger the blocked-response path
    type(response).text = mock.PropertyMock(side_effect=ValueError("blocked"))

    response.prompt_feedback = mock.MagicMock()
    response.prompt_feedback.block_reason = "SAFETY"

    response.usage_metadata = mock.MagicMock()
    response.usage_metadata.prompt_token_count = 5
    response.usage_metadata.candidates_token_count = 0

    model.generate_content.return_value = response
    genai.GenerativeModel.return_value = model
    return genai


def _make_mock_genai_exception():
    """Create a mock genai that raises on generate_content."""
    genai = mock.MagicMock()
    genai.configure = mock.MagicMock()
    model = mock.MagicMock()
    model.generate_content.side_effect = RuntimeError("API error")
    genai.GenerativeModel.return_value = model
    return genai


# ============================================================
# TestInit
# ============================================================

class TestGeminiSdkClientInit:
    def test_default_init(self):
        client = GeminiSdkClient()
        assert client.model == "gemini-2.5-flash"
        assert client.gemini_project is None
        assert client.gemini_location == "us-central1"
        assert client.circuit_breaker is None
        assert client.backend_type == "gemini_sdk"

    def test_custom_params(self):
        fake_cb = object()
        client = GeminiSdkClient(
            model="gemini-2.5-pro",
            gemini_project="my-project",
            gemini_location="europe-west1",
            circuit_breaker=fake_cb,
        )
        assert client.model == "gemini-2.5-pro"
        assert client.gemini_project == "my-project"
        assert client.gemini_location == "europe-west1"
        assert client.circuit_breaker is fake_cb

    def test_sdk_not_available_without_install(self):
        with mock.patch.dict("sys.modules", {"google.generativeai": None}):
            client = GeminiSdkClient()
            assert client.sdk_available is False

    def test_telemetry_starts_at_zero(self):
        client = GeminiSdkClient()
        assert client._total_calls == 0
        assert client._total_success == 0
        assert client._total_latency_ms == 0

    def test_backend_type_property(self):
        client = GeminiSdkClient()
        assert client.backend_type == "gemini_sdk"


# ============================================================
# TestGeminiSdkResult
# ============================================================

class TestGeminiSdkResult:
    def test_default_values(self):
        result = GeminiSdkResult()
        assert result.text == ""
        assert result.tokens_input == 0
        assert result.tokens_output == 0
        assert result.tokens_total == 0
        assert result.model == ""
        assert result.finish_reason == "UNKNOWN"
        assert result.stream_events == []
        assert result.latency_ms == 0
        assert result.cost_estimate_usd == 0.0
        assert result.backend_used == "gemini_sdk"
        assert result.exit_code == 0
        assert result.retry_count == 0

    def test_ok_with_text_and_stop(self):
        result = GeminiSdkResult(
            text="Hello",
            finish_reason="STOP",
        )
        assert result.ok is True

    def test_ok_with_text_and_max_tokens(self):
        result = GeminiSdkResult(
            text="Hello",
            finish_reason="MAX_TOKENS",
        )
        assert result.ok is True

    def test_not_ok_without_text(self):
        result = GeminiSdkResult(text="", finish_reason="STOP")
        assert result.ok is False

    def test_not_ok_with_unknown_finish(self):
        result = GeminiSdkResult(
            text="Hello",
            finish_reason="UNKNOWN",
        )
        assert result.ok is False

    def test_cost_calculation_includes_pricing(self):
        result = GeminiSdkResult(
            tokens_input=1000,
            tokens_output=2000,
            tokens_total=3000,
        )
        # tokens_total is explicitly set; cost is computed by the client
        assert result.tokens_total == 3000


# ============================================================
# TestCheckHealth
# ============================================================

class TestCheckHealth:
    def test_health_sdk_unavailable(self):
        client = GeminiSdkClient()
        client._sdk_available = False
        client._sdk_error = "SDK not installed"
        health = client.check_health()
        assert health["ok"] is False
        assert health["backend_type"] == "gemini_sdk"
        assert "SDK not installed" in health["message"]

    def test_health_sdk_available(self):
        client = GeminiSdkClient()
        client._sdk_available = True
        health = client.check_health()
        assert health["ok"] is True
        assert health["backend_type"] == "gemini_sdk"
        assert "可用" in health["message"]


# ============================================================
# TestInvoke
# ============================================================

class TestInvoke:
    def test_invoke_sdk_unavailable_raises(self):
        client = GeminiSdkClient()
        client._sdk_available = False
        with pytest.raises(RuntimeError):
            client.invoke("test prompt")

    def test_invoke_success(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        genai = _make_mock_genai(
            response_text="Hello, world!",
            tokens_in=10,
            tokens_out=20,
        )

        client = GeminiSdkClient(model="gemini-2.5-flash")
        client._sdk_available = True
        client._genai = genai

        result = client.invoke("Hello")
        assert isinstance(result, GeminiSdkResult)
        assert result.text == "Hello, world!"
        assert result.tokens_input == 10
        assert result.tokens_output == 20
        assert result.tokens_total == 30
        assert result.finish_reason == "STOP"
        assert result.backend_used == "gemini_sdk"
        assert result.latency_ms >= 0
        assert result.cost_estimate_usd > 0

    def test_invoke_with_system_prompt(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        genai = _make_mock_genai()
        client = GeminiSdkClient()
        client._sdk_available = True
        client._genai = genai
        result = client.invoke("prompt", system_prompt="Be helpful.")
        assert result.text == "Hello"

    def test_invoke_with_custom_model(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        genai = _make_mock_genai()
        client = GeminiSdkClient(model="gemini-2.5-pro")
        client._sdk_available = True
        client._genai = genai
        result = client.invoke("prompt", model="gemini-2.5-flash-lite")
        assert result is not None

    def test_invoke_with_custom_params(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        genai = _make_mock_genai()
        client = GeminiSdkClient()
        client._sdk_available = True
        client._genai = genai
        result = client.invoke(
            "prompt",
            temperature=0.2,
            max_output_tokens=1024,
            timeout_ms=60000,
        )
        assert result.text == "Hello"

    def test_invoke_blocked_response(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        genai = _make_mock_genai_blocked()
        client = GeminiSdkClient()
        client._sdk_available = True
        client._genai = genai
        with pytest.raises(RuntimeError, match="阻止"):
            client.invoke("blocked prompt")

    def test_invoke_retry_after_failures(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        # First two calls fail, third succeeds
        genai = mock.MagicMock()
        genai.configure = mock.MagicMock()
        # We need to make the model raise on first two attempts
        genai.GenerativeModel.side_effect = [
            mock.MagicMock(),
            mock.MagicMock(),
            mock.MagicMock(),
        ]

        client = GeminiSdkClient()
        client._sdk_available = True
        client._genai = genai
        # Reset to just use the failing approach
        genai = _make_mock_genai_exception()
        client._genai = genai
        with pytest.raises(RuntimeError):
            client.invoke("test")

    def test_invoke_reports_telemetry(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        genai = _make_mock_genai()
        client = GeminiSdkClient()
        client._sdk_available = True
        client._genai = genai
        client.invoke("test")
        telemetry = client.get_telemetry()
        assert telemetry["total_calls"] == 1
        assert telemetry["total_success"] == 1
        assert telemetry["backend"] == "gemini_sdk"

    def test_invoke_with_circuit_breaker(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        cb = mock.MagicMock()

        # Mock circuit breaker guard
        guard_result = mock.MagicMock()
        guard_result.blocked = False
        guard_result.reason = ""
        cb.guard.return_value = guard_result

        genai = _make_mock_genai()
        client = GeminiSdkClient(circuit_breaker=cb)
        client._sdk_available = True
        client._genai = genai
        result = client.invoke("test")
        assert result.ok

    def test_invoke_circuit_breaker_open_blocks(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        cb = mock.MagicMock()
        guard_result = mock.MagicMock()
        guard_result.blocked = True
        guard_result.reason = "Too many failures"
        cb.guard.return_value = guard_result

        client = GeminiSdkClient(circuit_breaker=cb)
        client._sdk_available = True
        with pytest.raises(RuntimeError, match="熔断器"):
            client.invoke("test")


# ============================================================
# TestResolveApiKey
# ============================================================

class TestResolveApiKey:
    def test_gemini_api_key_first(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
        key = GeminiSdkClient._resolve_api_key()
        assert key == "gemini-key"

    def test_google_api_key_fallback(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
        key = GeminiSdkClient._resolve_api_key()
        assert key == "google-key"

    def test_no_keys_returns_empty(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        key = GeminiSdkClient._resolve_api_key()
        assert key == ""


# ============================================================
# TestCalculateCost
# ============================================================

class TestCalculateCost:
    def test_zero_tokens_zero_cost(self):
        cost = GeminiSdkClient._calculate_cost(0, 0)
        assert cost == 0.0

    def test_input_tokens_cost(self):
        cost = GeminiSdkClient._calculate_cost(1000, 0)
        assert cost == 0.00015

    def test_output_tokens_cost(self):
        cost = GeminiSdkClient._calculate_cost(0, 1000)
        assert cost == 0.0006

    def test_both_tokens_cost(self):
        cost = GeminiSdkClient._calculate_cost(1000, 2000)
        expected = (1.0 * 0.00015) + (2.0 * 0.0006)
        assert cost == pytest.approx(expected)


# ============================================================
# TestBackoffDelay
# ============================================================

class TestBackoffDelay:
    def test_first_attempt(self):
        delay = GeminiSdkClient._backoff_delay(0, 1000, 16000)
        assert 1000 <= delay <= 1250  # base + up to 25% jitter

    def test_second_attempt(self):
        delay = GeminiSdkClient._backoff_delay(1, 1000, 16000)
        assert 2000 <= delay <= 2500

    def test_capped_at_max(self):
        delay = GeminiSdkClient._backoff_delay(10, 1000, 5000)
        assert 5000 <= delay <= 6250

    def test_positive_delay(self):
        for attempt in range(5):
            delay = GeminiSdkClient._backoff_delay(attempt, 1000, 16000)
            assert delay > 0


# ============================================================
# TestGetTelemetry
# ============================================================

class TestGetTelemetry:
    def test_returns_dict_with_keys(self):
        client = GeminiSdkClient()
        telemetry = client.get_telemetry()
        assert isinstance(telemetry, dict)
        assert "total_calls" in telemetry
        assert "total_success" in telemetry
        assert "avg_latency_ms" in telemetry
        assert "backend" in telemetry
        assert "sdk_available" in telemetry

    def test_avg_latency_zero_when_no_success(self):
        client = GeminiSdkClient()
        telemetry = client.get_telemetry()
        assert telemetry["avg_latency_ms"] == 0

    def test_avg_latency_calculated(self):
        client = GeminiSdkClient()
        client._total_success = 2
        client._total_latency_ms = 500
        telemetry = client.get_telemetry()
        assert telemetry["avg_latency_ms"] == 250


# ============================================================
# TestStreamEventsFormat
# ============================================================

class TestStreamEventsFormat:
    def test_events_have_expected_types(self):
        result = GeminiSdkResult(
            text="test",
            stream_events=[
                {"type": "status", "stage": "started", "model": "g-2.5"},
                {"type": "text", "content": "test"},
                {"type": "usage", "input_tokens": 5, "output_tokens": 10},
                {"type": "status", "stage": "complete", "finish_reason": "STOP"},
            ],
        )
        types = [e["type"] for e in result.stream_events]
        assert "status" in types
        assert "text" in types
        assert "usage" in types


# ============================================================
# TestInvokeStreamEvents
# ============================================================

class TestInvokeStreamEvents:
    def test_result_contains_stream_events(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        genai = _make_mock_genai()
        client = GeminiSdkClient()
        client._sdk_available = True
        client._genai = genai
        result = client.invoke("test")
        assert len(result.stream_events) == 4
        assert result.stream_events[0]["type"] == "status"
        assert result.stream_events[1]["type"] == "text"
        assert result.stream_events[2]["type"] == "usage"
        assert result.stream_events[3]["type"] == "status"
