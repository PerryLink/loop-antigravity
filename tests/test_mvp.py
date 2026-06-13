"""MVP 集成测试。

验证 mvp_helloworld 模块的核心流程: 状态检查、熔断器、Gemini 连通性。
使用 mock 避免依赖外部 agy CLI 和 GCloud 环境。
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from loop_antigravity.config import Config
from loop_antigravity.mvp_helloworld import (
    _check_agy_cli,
    _check_gcloud_auth,
    _update_state,
    _verify_circuit_breaker,
    mvp_run,
    main,
)
from loop_antigravity.state_manager import StateManager


class TestMVPHelperFunctions:
    """MVP 辅助函数单元测试。"""

    def test_check_agy_cli_not_found(self) -> None:
        """agy CLI 不存在时应返回 False。"""
        with patch("shutil.which", return_value=None):
            assert not _check_agy_cli()

    def test_check_agy_cli_found(self) -> None:
        """agy CLI 存在时应返回 True。"""
        with patch("shutil.which", return_value="/usr/bin/agy"):
            assert _check_agy_cli()

    def test_check_gcloud_auth_success(self) -> None:
        """GCloud 认证成功时应返回 True。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "ya29.fake-token-value\n"
        mock_proc.stderr = ""
        with patch("subprocess.run", return_value=mock_proc):
            assert _check_gcloud_auth()

    def test_check_gcloud_auth_failure(self) -> None:
        """GCloud 认证失败时应返回 False。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "ERROR: not authenticated"
        with patch("subprocess.run", return_value=mock_proc):
            assert not _check_gcloud_auth()

    def test_verify_circuit_breaker_returns_true(self) -> None:
        """CircuitBreaker 验证成功应返回 True。"""
        assert _verify_circuit_breaker()

    def test_update_state_writes_success(self, state_manager: StateManager) -> None:
        """_update_state 应将成功结果写入 state.json。"""
        _update_state(state_manager, all_ok=True)
        result = state_manager.read()
        assert result.data["progress"]["phase"] == "mvp_complete"
        assert result.data["mvp_result"]["status"] == "passed"
        assert result.data["mvp_result"]["completed_at"] is not None

    def test_update_state_writes_failure(self, state_manager: StateManager) -> None:
        """_update_state 应将失败结果写入 state.json。"""
        _update_state(state_manager, all_ok=False)
        result = state_manager.read()
        assert result.data["progress"]["phase"] == "mvp_failed"
        assert result.data["mvp_result"]["status"] == "failed"


class TestMVPRun:
    """mvp_run 集成流程测试。"""

    def test_mvp_run_with_all_checks_passing(
        self, state_manager: StateManager
    ) -> None:
        """所有检查通过时 mvp_run 应返回 True。"""
        config = Config(mode="auto")

        mock_health = MagicMock()
        mock_health.ok = True
        mock_health.version = "1.0.0-test"
        mock_health.message = "healthy"

        with patch(
            "loop_antigravity.mvp_helloworld._check_agy_cli",
            return_value=True,
        ), patch(
            "loop_antigravity.mvp_helloworld._check_gcloud_auth",
            return_value=True,
        ), patch(
            "loop_antigravity.mvp_helloworld._test_gemini_ping",
            return_value=True,
        ):
            ok = mvp_run(config, state_manager)
            assert ok

        result = state_manager.read()
        assert result.data["progress"]["phase"] == "mvp_complete"
        assert result.data["mvp_result"]["status"] == "passed"

    def test_mvp_run_with_failing_checks(
        self, state_manager: StateManager
    ) -> None:
        """部分检查失败时 mvp_run 应返回 False。"""
        config = Config(mode="auto")

        with patch(
            "loop_antigravity.mvp_helloworld._check_agy_cli",
            return_value=False,
        ):
            ok = mvp_run(config, state_manager)
            assert not ok

        result = state_manager.read()
        assert result.data["progress"]["phase"] == "mvp_failed"
        assert result.data["mvp_result"]["status"] == "failed"

    def test_mvp_run_always_updates_state(
        self, state_manager: StateManager
    ) -> None:
        """无论成功或失败，mvp_run 都应更新 state.json。"""
        config = Config(mode="auto")

        with patch(
            "loop_antigravity.mvp_helloworld._check_agy_cli",
            return_value=False,
        ):
            mvp_run(config, state_manager)

        assert os.path.exists(state_manager.state_path)
        result = state_manager.read()
        assert "mvp_result" in result.data

    def test_mvp_run_circuit_breaker_fails(
        self, state_manager: StateManager
    ) -> None:
        """CircuitBreaker 验证失败时应记录到失败列表。"""
        config = Config(mode="auto")

        with patch(
            "loop_antigravity.mvp_helloworld._verify_circuit_breaker",
            return_value=False,
        ), patch(
            "loop_antigravity.mvp_helloworld._check_agy_cli",
            return_value=True,
        ), patch(
            "loop_antigravity.mvp_helloworld._check_gcloud_auth",
            return_value=True,
        ), patch(
            "loop_antigravity.mvp_helloworld._test_gemini_ping",
            return_value=True,
        ):
            ok = mvp_run(config, state_manager)
            assert not ok

        result = state_manager.read()
        assert result.data["progress"]["phase"] == "mvp_failed"


class TestMVPCLI:
    """CLI 入口测试。"""

    def test_main_init_flag(self, temp_state_dir: str) -> None:
        """--init 标志应创建 state.json（mock 化 agy 检查）。"""
        state_path = os.path.join(temp_state_dir, "state.json")

        with patch(
            "loop_antigravity.mvp_helloworld._check_agy_cli",
            return_value=True,
        ), patch(
            "loop_antigravity.mvp_helloworld._check_gcloud_auth",
            return_value=True,
        ), patch(
            "loop_antigravity.mvp_helloworld._test_gemini_ping",
            return_value=True,
        ):
            ret = main(["--init", "--state-dir", temp_state_dir,
                         "--mode", "auto"])
        assert os.path.exists(state_path)
        assert ret == 0

    def test_main_init_existing_state(self, temp_state_dir: str) -> None:
        """--init 在 state.json 已存在时应提示已存在。"""
        state_path = os.path.join(temp_state_dir, "state.json")
        # 先创建一个 state.json
        sm = StateManager(base_dir=temp_state_dir)
        sm.read()  # 创建默认状态

        with patch(
            "loop_antigravity.mvp_helloworld._check_agy_cli",
            return_value=True,
        ), patch(
            "loop_antigravity.mvp_helloworld._check_gcloud_auth",
            return_value=True,
        ), patch(
            "loop_antigravity.mvp_helloworld._test_gemini_ping",
            return_value=True,
        ):
            ret = main(["--init", "--state-dir", temp_state_dir,
                         "--mode", "auto"])
        assert ret == 0

    def test_main_help(self) -> None:
        """--help 应正常退出。"""
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_main_invalid_mode(self) -> None:
        """无效 mode 应触发 argparse 退出 (code=2)。"""
        with pytest.raises(SystemExit) as exc:
            main(["--mode", "invalid"])
        assert exc.value.code == 2

    def test_main_invalid_config_value(self) -> None:
        """Config 构造时 ValueError 应被捕获并返回 1。"""
        with patch("loop_antigravity.mvp_helloworld.Config",
                   side_effect=ValueError("bad config")), \
             patch("loop_antigravity.mvp_helloworld._check_agy_cli",
                   return_value=True), \
             patch("loop_antigravity.mvp_helloworld._check_gcloud_auth",
                   return_value=True), \
             patch("loop_antigravity.mvp_helloworld._test_gemini_ping",
                   return_value=True):
            ret = main(["--mode", "auto",
                        "--state-dir", "/tmp/test"])
            assert ret == 1


class TestGCloudAuthCheck:
    """GCloud 认证检查错误路径测试。"""

    def test_gcloud_not_installed(self) -> None:
        """gcloud CLI 未安装应返回 False。"""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert not _check_gcloud_auth()

    def test_gcloud_auth_timeout(self) -> None:
        """gcloud auth 命令超时应返回 False。"""
        import subprocess
        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired("gcloud", 15)):
            assert not _check_gcloud_auth()


class TestGeminiPing:
    """Gemini ping 连通性测试。"""

    def test_gemini_ping_ok(self) -> None:
        """AgyClient.check_health 返回 ok 时应返回 True。"""
        from loop_antigravity.mvp_helloworld import _test_gemini_ping
        config = Config(mode="auto")
        mock_health = MagicMock()
        mock_health.ok = True
        mock_health.version = "1.0.0"
        mock_health.message = "healthy"

        with patch(
            "loop_antigravity.agy_client.AgyClient.check_health",
            return_value=mock_health,
        ):
            assert _test_gemini_ping(config)

    def test_gemini_ping_not_ok(self) -> None:
        """AgyClient.check_health 返回不健康时应返回 False。"""
        from loop_antigravity.mvp_helloworld import _test_gemini_ping
        config = Config(mode="auto")
        mock_health = MagicMock()
        mock_health.ok = False
        mock_health.message = "unhealthy"

        with patch(
            "loop_antigravity.agy_client.AgyClient.check_health",
            return_value=mock_health,
        ):
            assert not _test_gemini_ping(config)

    def test_gemini_ping_exception(self) -> None:
        """Gemini ping 抛出异常时应返回 False（非致命）。"""
        from loop_antigravity.mvp_helloworld import _test_gemini_ping
        config = Config(mode="auto")

        with patch(
            "loop_antigravity.agy_client.AgyClient.check_health",
            side_effect=RuntimeError("connection failed"),
        ):
            assert not _test_gemini_ping(config)
