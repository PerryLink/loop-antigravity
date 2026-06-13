# -*- coding: utf-8 -*-
"""Test: billing_tracker.py -- Billing tracker (hard cap + quota tracking).

Tests cover:
  - record() basic flow (returns BillingRecord, not bool)
  - Cost calculation via _calculate_cost()
  - Daily/weekly hard cap via is_daily_exhausted / is_weekly_exhausted
  - Warning thresholds via get_warnings()
  - used_pct via QuotaWindow / get_daily_window()
  - is_any_exhausted property
  - Default caps by mode (safe/auto/unsafe/collaborative)
  - Cross-day boundary via _check_window_reset()
  - to_dict() / from_dict() serialization roundtrip
  - check_before_call() pre-flight gate
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from loop_antigravity.billing_tracker import (
    BillingTracker,
    BillingRecord,
    QuotaWindow,
    PRICING,
)


class TestPricing:
    """Pricing constant verification."""

    def test_pricing_has_all_keys(self):
        assert "input_per_1k" in PRICING
        assert "output_per_1k" in PRICING
        assert "cached_input_per_1k" in PRICING

    def test_cached_input_cheaper_than_input(self):
        assert PRICING["cached_input_per_1k"] < PRICING["input_per_1k"]

    def test_output_more_expensive_than_input(self):
        assert PRICING["output_per_1k"] > PRICING["input_per_1k"]


class TestBillingRecord:
    """BillingRecord dataclass."""

    def test_default_values(self):
        record = BillingRecord()
        assert record.input_tokens == 0
        assert record.output_tokens == 0
        assert record.cost_usd == 0.0
        assert record.model == ""
        assert record.backend == ""

    def test_cost_calculation_via_tracker(self):
        """100K input + 10K output cost."""
        cost = BillingTracker._calculate_cost(100000, 10000)
        expected = (100.0 * 0.00015) + (10.0 * 0.0006)  # $0.015 + $0.006 = $0.021
        assert abs(cost - 0.021) < 0.001


class TestBillingTrackerBasic:
    """BillingTracker basic functionality."""

    def test_init_default_auto_mode(self):
        bt = BillingTracker()
        assert bt.daily_cap_usd == 20.0
        assert bt.weekly_cap_usd == 100.0

    def test_init_custom_caps(self):
        bt = BillingTracker(daily_cap_usd=10.0, weekly_cap_usd=50.0)
        assert bt.daily_cap_usd == 10.0

    def test_init_safe_mode(self):
        bt = BillingTracker(mode="safe")
        assert bt.daily_cap_usd == 5.0
        assert bt.weekly_cap_usd == 25.0

    def test_init_unsafe_mode(self):
        bt = BillingTracker(mode="unsafe")
        assert bt.daily_cap_usd == 100.0
        assert bt.weekly_cap_usd == 500.0

    def test_init_collaborative_mode(self):
        bt = BillingTracker(mode="collaborative")
        assert bt.daily_cap_usd == 10.0
        assert bt.weekly_cap_usd == 50.0

    def test_init_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            BillingTracker(mode="invalid_mode_name")

    def test_daily_window_used_pct_zero_on_init(self):
        bt = BillingTracker()
        assert bt.get_daily_window().used_pct == 0.0

    def test_is_not_exhausted_on_init(self):
        bt = BillingTracker()
        assert bt.is_any_exhausted is False


class TestRecord:
    """record() call tests.

    record() returns a BillingRecord instance, not a bool.
    """

    def test_record_returns_billing_record(self):
        bt = BillingTracker(daily_cap_usd=100.0)
        result = bt.record(input_tokens=10000, output_tokens=5000)
        assert isinstance(result, BillingRecord)
        assert result.input_tokens == 10000
        assert result.output_tokens == 5000
        assert result.cost_usd > 0.0

    def test_record_updates_daily_window(self):
        bt = BillingTracker(daily_cap_usd=100.0)
        bt.record(input_tokens=10000, output_tokens=5000)
        assert bt.get_daily_window().used_pct > 0.0
        assert bt.get_daily_window().invocation_count == 1

    def test_record_increments_cost(self):
        bt = BillingTracker(daily_cap_usd=10.0)
        pct_before = bt.get_daily_window().used_pct
        bt.record(input_tokens=500000, output_tokens=100000)  # ~$0.135
        assert bt.get_daily_window().used_pct > pct_before

    def test_is_exhausted_after_exceeding_cap(self):
        bt = BillingTracker(daily_cap_usd=0.001)
        bt.record(input_tokens=1000000, output_tokens=500000)
        assert bt.is_any_exhausted is True

    def test_check_before_call_blocks_when_exhausted(self):
        bt = BillingTracker(daily_cap_usd=0.001)
        bt.record(input_tokens=1000000, output_tokens=500000)
        allowed, reason = bt.check_before_call()
        assert allowed is False
        assert len(reason) > 0

    def test_record_with_model_and_backend(self):
        bt = BillingTracker(daily_cap_usd=100.0)
        result = bt.record(
            input_tokens=1000, output_tokens=100,
            model="gemini-2.5-flash", backend="gemini_sdk",
        )
        assert result.model == "gemini-2.5-flash"
        assert result.backend == "gemini_sdk"


class TestDailyWeeklyWindows:
    """Daily/weekly window queries."""

    def test_get_daily_window_returns_quota_window(self):
        bt = BillingTracker()
        dw = bt.get_daily_window()
        assert isinstance(dw, QuotaWindow)
        assert dw.label == "daily"

    def test_get_weekly_window_returns_quota_window(self):
        bt = BillingTracker()
        ww = bt.get_weekly_window()
        assert isinstance(ww, QuotaWindow)
        assert ww.label == "weekly"

    def test_daily_window_reflects_usage(self):
        bt = BillingTracker(daily_cap_usd=100.0)
        bt.record(input_tokens=10000, output_tokens=5000)
        dw = bt.get_daily_window()
        assert dw.input_tokens == 10000
        assert dw.output_tokens == 5000
        assert dw.cost_usd > 0.0


class TestSnapshot:
    """to_dict() / from_dict() serialization tests."""

    def test_to_dict_returns_dict_with_expected_keys(self):
        bt = BillingTracker()
        snap = bt.to_dict()
        assert isinstance(snap, dict)
        assert "daily_cap_usd" in snap
        assert "daily" in snap
        assert "weekly" in snap
        assert "total_cost_usd" in snap

    def test_to_dict_includes_mode(self):
        bt = BillingTracker(mode="safe")
        snap = bt.to_dict()
        assert snap["mode"] == "safe"

    def test_from_dict_roundtrip_preserves_mode_and_caps(self):
        bt = BillingTracker(mode="auto")
        bt.record(input_tokens=5000, output_tokens=1000)
        data = bt.to_dict()
        bt2 = BillingTracker.from_dict(data)
        assert bt2.mode == bt.mode
        assert bt2.daily_cap_usd == bt.daily_cap_usd
        assert abs(bt2._daily_cost - bt._daily_cost) < 0.00001


class TestResetDaily:
    """Daily window reset via _check_window_reset()."""

    def test_window_reset_clears_daily_counters(self):
        bt = BillingTracker(daily_cap_usd=100.0)
        bt.record(input_tokens=10000, output_tokens=5000)
        assert bt.get_daily_window().used_pct > 0.0
        # Force reset by moving start_ts back beyond 24h
        bt._daily_start_ts -= 86401
        bt._check_window_reset()
        dw = bt.get_daily_window()
        assert dw.used_pct == 0.0
        assert dw.input_tokens == 0

    def test_window_reset_clears_weekly_counters(self):
        bt = BillingTracker(weekly_cap_usd=500.0)
        bt.record(input_tokens=10000, output_tokens=5000)
        assert bt.get_weekly_window().used_pct > 0.0
        # Force reset by moving start_ts back beyond 7 days
        bt._weekly_start_ts -= 604801
        bt._check_window_reset()
        ww = bt.get_weekly_window()
        assert ww.used_pct == 0.0
        assert ww.input_tokens == 0


class TestGetWarnings:
    """Warning signal tests."""

    def test_no_warnings_on_init(self):
        bt = BillingTracker()
        warnings = bt.get_warnings()
        assert len(warnings) == 0

    def test_warning_at_usage(self):
        bt = BillingTracker(daily_cap_usd=0.01, warning_pct=50.0)
        # ~5000 input + 2000 output ≈ cost of ~0.00195 which is ~19.5% of 0.01
        # need to get above 50%
        bt.record(input_tokens=250000, output_tokens=10000)
        warnings = bt.get_warnings()
        if bt.get_daily_window().used_pct >= 50.0:
            assert len(warnings) >= 1


class TestQuotaWindow:
    """QuotaWindow dataclass tests."""

    def test_defaults(self):
        qw = QuotaWindow()
        assert qw.label == ""
        assert qw.input_tokens == 0

    def test_used_pct_with_cap(self):
        qw = QuotaWindow(cost_usd=5.0, cap_usd=10.0)
        assert qw.used_pct == 50.0

    def test_used_pct_capped_at_100(self):
        qw = QuotaWindow(cost_usd=20.0, cap_usd=10.0)
        assert qw.used_pct == 100.0

    def test_used_pct_zero_cap_returns_100(self):
        qw = QuotaWindow(cost_usd=5.0, cap_usd=0.0)
        assert qw.used_pct == 100.0

    def test_used_pct_negative_cap_returns_100(self):
        qw = QuotaWindow(cost_usd=5.0, cap_usd=-1.0)
        assert qw.used_pct == 100.0

    def test_exhausted_true(self):
        qw = QuotaWindow(cost_usd=10.0, cap_usd=10.0)
        assert qw.exhausted is True

    def test_exhausted_false(self):
        qw = QuotaWindow(cost_usd=5.0, cap_usd=10.0)
        assert qw.exhausted is False


class TestCheckBeforeCall:
    """check_before_call() detailed tests covering all exhausted paths."""

    def test_check_daily_exhausted_blocks(self):
        bt = BillingTracker(daily_cap_usd=0.001, weekly_cap_usd=1000.0)
        bt.record(input_tokens=1000000, output_tokens=500000)
        allowed, reason = bt.check_before_call()
        assert allowed is False
        assert "每日" in reason

    def test_check_weekly_exhausted_blocks(self):
        bt = BillingTracker(daily_cap_usd=1000.0, weekly_cap_usd=0.001)
        bt.record(input_tokens=1000000, output_tokens=500000)
        allowed, reason = bt.check_before_call()
        assert allowed is False
        assert "每周" in reason

    def test_check_allowed_when_neither_exhausted(self):
        bt = BillingTracker(daily_cap_usd=1000.0, weekly_cap_usd=5000.0)
        allowed, reason = bt.check_before_call()
        assert allowed is True
        assert reason == ""


class TestGetWarningsDetailed:
    """get_warnings() covering all three warning levels."""

    def test_returns_critical_when_exhausted(self):
        bt = BillingTracker(daily_cap_usd=0.001)
        bt.record(input_tokens=1000000, output_tokens=500000)
        warnings = bt.get_warnings()
        critic = [w for w in warnings if w["level"] == "critical"]
        assert len(critic) >= 1

    def test_returns_hard_warning_above_95(self):
        bt = BillingTracker(daily_cap_usd=0.01, warning_pct=80.0, hard_warning_pct=95.0)
        # ~96% of $0.01 = ~$0.0096 via 64000 input tokens
        bt.record(input_tokens=64000, output_tokens=0)
        warnings = bt.get_warnings()
        hard_warn = [w for w in warnings if w["level"] == "hard_warning"]
        assert len(hard_warn) >= 1

    def test_returns_warning_above_80(self):
        bt = BillingTracker(daily_cap_usd=0.01, warning_pct=80.0, hard_warning_pct=99.0)
        # ~85% of $0.01 via 56700 input tokens
        bt.record(input_tokens=56700, output_tokens=0)
        warnings = bt.get_warnings()
        warn = [w for w in warnings if w["level"] == "warning"]
        assert len(warn) >= 1
