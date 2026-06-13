"""
pytest 共享 fixtures 和配置。

提供:
    - 临时 state.json 目录 fixture
    - CircuitBreaker fixture
    - Config fixture
    - mock agy CLI 子进程 fixture
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Generator

import pytest

from loop_antigravity.config import Config
from loop_antigravity.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    FailureCategory,
)
from loop_antigravity.state_manager import StateManager


# ============================================================================
# 临时目录 fixtures
# ============================================================================


@pytest.fixture
def temp_state_dir(tmp_path: Path) -> Generator[str, None, None]:
    """创建一个临时状态目录用于测试。

    测试结束后自动清理。

    Yields:
        临时目录的绝对路径字符串。
    """
    state_dir = tmp_path / "loop-antigravity"
    state_dir.mkdir(parents=True, exist_ok=True)
    yield str(state_dir)


@pytest.fixture
def state_manager(temp_state_dir: str) -> StateManager:
    """创建一个指向临时目录的 StateManager。

    Args:
        temp_state_dir: 临时状态目录 fixture。

    Returns:
        StateManager 实例。
    """
    return StateManager(base_dir=temp_state_dir)


# ============================================================================
# CircuitBreaker fixtures
# ============================================================================


@pytest.fixture
def cb_config() -> CircuitBreakerConfig:
    """创建一个测试用的 CircuitBreakerConfig。

    使用较短的阈值和冷却时间以加速测试。

    Returns:
        CircuitBreakerConfig 实例。
    """
    return CircuitBreakerConfig(
        failure_threshold=3,
        cooldown_seconds=0.5,
        max_cooldown_seconds=5.0,
        half_open_max_requests=1,
        log_path=os.path.join(
            tempfile.gettempdir(), "test_circuit_breaker.log"
        ),
    )


@pytest.fixture
def circuit_breaker(cb_config: CircuitBreakerConfig) -> CircuitBreaker:
    """创建一个处于 CLOSED 状态的 CircuitBreaker。

    Args:
        cb_config: CircuitBreakerConfig fixture。

    Returns:
        CircuitBreaker 实例。
    """
    cb = CircuitBreaker(cb_config)
    # 确保处于 CLOSED 状态
    assert cb.is_closed
    return cb


@pytest.fixture
def open_circuit_breaker(cb_config: CircuitBreakerConfig) -> CircuitBreaker:
    """创建一个处于 OPEN 状态的 CircuitBreaker。

    Args:
        cb_config: CircuitBreakerConfig fixture。

    Returns:
        处于 OPEN 状态的 CircuitBreaker 实例。
    """
    cb = CircuitBreaker(cb_config)
    for i in range(cb_config.failure_threshold):
        cb.report_failure(FailureCategory.SERVER_ERROR, f"fixture failure {i}")
    assert cb.is_open
    return cb


# ============================================================================
# Config fixtures
# ============================================================================


@pytest.fixture
def config_auto() -> Config:
    """创建一个 L2 auto 模式的 Config。

    Returns:
        Config 实例。
    """
    return Config(mode="auto")


@pytest.fixture
def config_safe() -> Config:
    """创建一个 L1 safe 模式的 Config。

    Returns:
        Config 实例。
    """
    return Config(mode="safe")


# ============================================================================
# state.json fixtures
# ============================================================================


@pytest.fixture
def sample_state() -> dict:
    """提供一个有效的示例 state.json 字典。

    Returns:
        符合 MVP schema 的状态字典。
    """
    return {
        "schema_version": "mvp_1",
        "progress": {
            "phase": "mvp_init",
            "cycle": 1,
            "convergence_counter": 0,
        },
        "config": {
            "mode": "auto",
            "model": "gemini-2.5-flash",
            "timeout_ms": 300000,
            "temperature": 0.7,
            "max_output_tokens": 8192,
        },
        "circuit_breaker": {
            "state": "CLOSED",
            "consecutive_failures": 0,
            "failure_threshold": 5,
            "cooldown_seconds": 30,
            "opened_at": None,
            "last_failure_at": None,
            "last_failure_reason": None,
            "last_probe_at": None,
            "total_trips": 0,
        },
        "mvp_result": {
            "status": None,
            "agy_version": None,
            "tokens_input": 0,
            "tokens_output": 0,
            "latency_ms": 0,
            "output_file": "artifacts/hello.py",
            "output_file_exists": False,
            "error_message": None,
            "completed_at": None,
        },
        "housekeeping": {
            "invocation_count": 0,
            "lock_file": ".claude/loop-antigravity/.lock",
        },
    }


@pytest.fixture
def state_manager_with_data(
    temp_state_dir: str, sample_state: dict
) -> StateManager:
    """创建一个包含示例状态数据的 StateManager。

    Args:
        temp_state_dir: 临时状态目录 fixture。
        sample_state: 示例状态字典 fixture。

    Returns:
        包含预写入 state.json 的 StateManager 实例。
    """
    sm = StateManager(base_dir=temp_state_dir)
    sm.write(sample_state)
    return sm
