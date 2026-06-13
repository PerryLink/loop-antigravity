"""Config 配置管理单元测试。

验证配置加载、模式选择、环境变量覆盖与序列化往返。
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from loop_antigravity.config import (
    BILLING_CAPS,
    CIRCUIT_BREAKER_THRESHOLDS,
    Config,
    VALID_MODES,
)


class TestConfigInit:
    """Config 初始化测试。"""

    def test_default_mode_is_auto(self) -> None:
        """默认模式应为 auto。"""
        cfg = Config()
        assert cfg.mode == "auto"

    def test_all_valid_modes(self) -> None:
        """所有有效模式应成功初始化。"""
        for mode in VALID_MODES:
            cfg = Config(mode=mode)
            assert cfg.mode == mode

    def test_invalid_mode_raises(self) -> None:
        """无效模式应抛出 ValueError。"""
        with pytest.raises(ValueError, match="无效的操作模式"):
            Config(mode="dangerous")

    def test_safe_mode_thresholds(self) -> None:
        """safe 模式应使用低阈值和长冷却时间。"""
        cfg = Config(mode="safe")
        assert cfg.failure_threshold == 2
        assert cfg.cooldown_seconds == 120.0

    def test_auto_mode_thresholds(self) -> None:
        """auto 模式应使用默认阈值。"""
        cfg = Config(mode="auto")
        assert cfg.failure_threshold == 5
        assert cfg.cooldown_seconds == 30.0

    def test_unsafe_mode_thresholds(self) -> None:
        """unsafe 模式应使用高阈值和短冷却时间。"""
        cfg = Config(mode="unsafe")
        assert cfg.failure_threshold == 20
        assert cfg.cooldown_seconds == 5.0

    def test_collaborative_mode_thresholds(self) -> None:
        """collaborative 模式应使用中等阈值。"""
        cfg = Config(mode="collaborative")
        assert cfg.failure_threshold == 3
        assert cfg.cooldown_seconds == 60.0


class TestConfigDefaults:
    """默认值测试。"""

    def test_agy_defaults(self) -> None:
        """agy 默认值应正确。"""
        cfg = Config()
        assert cfg.agy.agy_path == "agy"
        assert cfg.agy.model == "gemini-2.5-flash"
        assert cfg.agy.gemini_location == "us-central1"

    def test_billing_caps_exist_for_all_modes(self) -> None:
        """所有模式应有计费上限配置。"""
        for mode in VALID_MODES:
            caps = BILLING_CAPS.get(mode)
            assert caps is not None, f"模式 {mode} 缺少计费上限"
            assert "daily" in caps
            assert "weekly" in caps

    def test_circuit_breaker_thresholds_exist_for_all_modes(self) -> None:
        """所有模式应有熔断器阈值配置。"""
        for mode in VALID_MODES:
            thresholds = CIRCUIT_BREAKER_THRESHOLDS.get(mode)
            assert thresholds is not None, f"模式 {mode} 缺少熔断器阈值"
            assert "failure_threshold" in thresholds
            assert "cooldown_seconds" in thresholds


class TestConfigEnvOverride:
    """环境变量覆盖测试。"""

    def test_env_agy_path(self) -> None:
        """LOOP_AG_AGY_PATH 环境变量应覆盖 agy_path。"""
        with patch.dict(os.environ, {"LOOP_AG_AGY_PATH": "/custom/agy"}):
            cfg = Config()
            assert cfg.agy.agy_path == "/custom/agy"

    def test_env_model(self) -> None:
        """LOOP_AG_MODEL 环境变量应覆盖 model。"""
        with patch.dict(os.environ, {"LOOP_AG_MODEL": "gemini-2.5-pro"}):
            cfg = Config()
            assert cfg.agy.model == "gemini-2.5-pro"

    def test_env_daily_cap(self) -> None:
        """LOOP_AG_DAILY_CAP 环境变量应覆盖每日上限。"""
        with patch.dict(os.environ, {"LOOP_AG_DAILY_CAP": "42.0"}):
            cfg = Config(mode="auto")
            assert cfg.billing.daily_cap_usd == 42.0

    def test_explicit_param_overrides_env(self) -> None:
        """显式参数应高于环境变量。"""
        with patch.dict(os.environ, {"LOOP_AG_AGY_PATH": "/env/path"}):
            cfg = Config(agy_path="/explicit/path")
            assert cfg.agy.agy_path == "/explicit/path"


class TestConfigSerialization:
    """序列化测试。"""

    def test_to_dict_has_required_keys(self) -> None:
        """to_dict 应包含所有核心配置键。"""
        cfg = Config(mode="auto")
        d = cfg.to_dict()
        assert "mode" in d
        assert "model" in d
        assert "daily_cap_usd" in d
        assert "max_cycles" in d
        assert "timeout_ms" in d

    def test_from_dict_roundtrip(self) -> None:
        """to_dict -> from_dict 往返应保持一致性。"""
        cfg1 = Config(mode="safe", gemini_project="test-proj")
        d = cfg1.to_dict()
        cfg2 = Config.from_dict(d)
        assert cfg2.mode == cfg1.mode
        assert cfg2.agy.gemini_project == cfg1.agy.gemini_project

    def test_from_dict_defaults(self) -> None:
        """from_dict 缺少 key 时应使用默认值。"""
        cfg = Config.from_dict({"mode": "auto"})
        assert cfg.agy.model == "gemini-2.5-flash"

    def test_to_dict_includes_billing(self) -> None:
        """to_dict 应包含计费配置。"""
        cfg = Config(mode="unsafe")
        d = cfg.to_dict()
        assert d["daily_cap_usd"] == BILLING_CAPS["unsafe"]["daily"]
        assert d["weekly_cap_usd"] == BILLING_CAPS["unsafe"]["weekly"]


class TestConfigRetryAccessors:
    """重试配置属性访问测试。"""

    def test_retry_accessors_auto(self) -> None:
        """auto 模式的重试配置属性。"""
        cfg = Config(mode="auto")
        assert cfg.retry_base_delay_ms == 1000
        assert cfg.retry_max_delay_ms == 16000
        assert cfg.retry_max_attempts == 5

    def test_retry_accessors_safe(self) -> None:
        """safe 模式的重试配置属性。"""
        cfg = Config(mode="safe")
        assert cfg.retry_base_delay_ms == 2000
        assert cfg.retry_max_delay_ms == 30000
        assert cfg.retry_max_attempts == 2
