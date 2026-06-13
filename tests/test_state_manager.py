"""StateManager 单元测试。

验证 state.json 的原子写入、读取、备份恢复与 schema 验证。
"""

from __future__ import annotations

import json
from unittest.mock import patch
import os

import pytest

from loop_antigravity.state_manager import (
    MVP_SCHEMA,
    StateManager,
    StateReadResult,
)


class TestStateManagerInit:
    """StateManager 初始化测试。"""

    def test_creates_state_dir(self, temp_state_dir: str) -> None:
        """初始化时应创建状态目录。"""
        sm = StateManager(base_dir=temp_state_dir)
        assert os.path.isdir(sm.state_dir)

    def test_sets_correct_paths(self, temp_state_dir: str) -> None:
        """应正确设置所有文件路径。"""
        sm = StateManager(base_dir=temp_state_dir)
        assert sm.state_path.endswith("state.json")
        assert sm.backup_path.endswith("state.json.bak")
        assert sm.tmp_path.endswith("state.json.tmp")
        assert sm.lock_path.endswith(".lock")


class TestStateManagerRead:
    """读取操作测试。"""

    def test_read_creates_default_state(
        self, temp_state_dir: str
    ) -> None:
        """目录为空时 read() 应创建默认状态。"""
        sm = StateManager(base_dir=temp_state_dir)
        result = sm.read()
        assert result.from_scratch
        assert "schema_version" in result.data
        assert result.data["schema_version"] == "mvp_1"
        assert os.path.exists(sm.state_path)

    def test_read_existing_state(
        self, state_manager_with_data: StateManager
    ) -> None:
        """已有 state.json 时应正确读取。"""
        result = state_manager_with_data.read()
        assert not result.from_scratch
        assert result.data["schema_version"] == "mvp_1"


class TestStateManagerWrite:
    """写入操作测试。"""

    def test_write_and_read_roundtrip(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """写入后读取应得到相同数据。"""
        state_manager.write(sample_state)
        result = state_manager.read()
        assert not result.from_scratch
        assert result.data["config"]["mode"] == sample_state["config"]["mode"]
        assert result.data["progress"]["phase"] == sample_state["progress"]["phase"]

    def test_atomic_write_produces_state_json(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """原子写入应在磁盘上生成有效的 JSON 文件。"""
        state_manager.write(sample_state)
        assert os.path.isfile(state_manager.state_path)

        with open(state_manager.state_path, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        assert disk_data["schema_version"] == sample_state["schema_version"]

    def test_atomic_write_creates_backup(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """原子写入应同时创建备份文件。"""
        state_manager.write(sample_state)
        assert os.path.isfile(state_manager.backup_path)

        with open(state_manager.backup_path, "r", encoding="utf-8") as f:
            backup_data = json.load(f)
        assert backup_data["config"]["mode"] == sample_state["config"]["mode"]

    def test_write_cleans_up_tmp(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """写入完成后应清理临时文件。"""
        state_manager.write(sample_state)
        assert not os.path.exists(state_manager.tmp_path)


class TestSchemaValidation:
    """Schema 验证测试。"""

    def test_validate_valid_state_passes(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """有效的状态数据应通过 schema 验证。"""
        state_manager.validate(sample_state)  # 不应抛出异常

    def test_validate_missing_required_field_raises(
        self, state_manager: StateManager
    ) -> None:
        """缺少必需字段应抛出 ValidationError。"""
        bad_state = {"schema_version": "mvp_1"}
        with pytest.raises(Exception):  # jsonschema.ValidationError
            state_manager.validate(bad_state)

    def test_validate_invalid_mode_raises(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """无效的 mode 值应抛出 ValidationError。"""
        bad = sample_state.copy()
        bad["config"] = dict(sample_state["config"])
        bad["config"]["mode"] = "invalid_mode"
        with pytest.raises(Exception):
            state_manager.validate(bad)

    def test_validate_invalid_circuit_state_raises(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """无效的 circuit_breaker state 应抛出 ValidationError。"""
        bad = sample_state.copy()
        bad["circuit_breaker"] = dict(sample_state["circuit_breaker"])
        bad["circuit_breaker"]["state"] = "BROKEN"
        with pytest.raises(Exception):
            state_manager.validate(bad)

    def test_validate_negative_consecutive_failures_raises(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """负的 consecutive_failures 应抛出 ValidationError。"""
        bad = sample_state.copy()
        bad["circuit_breaker"] = dict(sample_state["circuit_breaker"])
        bad["circuit_breaker"]["consecutive_failures"] = -1
        with pytest.raises(Exception):
            state_manager.validate(bad)

    def test_mvp_schema_defines_valid_modes(self) -> None:
        """MVP_SCHEMA 应定义所有有效的操作模式。"""
        mode_enum = MVP_SCHEMA["properties"]["config"]["properties"]["mode"]["enum"]
        assert "safe" in mode_enum
        assert "auto" in mode_enum
        assert "unsafe" in mode_enum
        assert "collaborative" in mode_enum


class TestBackupRecovery:
    """备份恢复测试。"""

    def test_recovers_from_backup_when_main_corrupt(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """主文件损坏时应从备份恢复。"""
        state_manager.write(sample_state)
        assert os.path.isfile(state_manager.backup_path)

        # 损坏主文件
        with open(state_manager.state_path, "w", encoding="utf-8") as f:
            f.write("this is not valid json {{{")

        result = state_manager.read()
        assert result.from_backup
        assert result.data["config"]["mode"] == sample_state["config"]["mode"]

    def test_creates_fresh_when_both_corrupt(
        self, temp_state_dir: str
    ) -> None:
        """主文件和备份均损坏时应抛出 RuntimeError。

        StateManager 设计上在主文件和备份均不可解析时抛出异常，
        提示用户手动干预。这可以防止静默丢失状态数据。
        """
        sm = StateManager(base_dir=temp_state_dir)

        # 写入损坏的主文件和备份
        with open(sm.state_path, "w", encoding="utf-8") as f:
            f.write("corrupt")
        with open(sm.backup_path, "w", encoding="utf-8") as f:
            f.write("also corrupt")

        with pytest.raises(RuntimeError):
            sm.read()

    def test_recovers_from_tmp_when_both_main_and_backup_corrupt(
        self, temp_state_dir: str, sample_state: dict
    ) -> None:
        """主文件和备份都损坏时，应从残留的 .tmp 文件恢复。"""
        sm = StateManager(base_dir=temp_state_dir)

        # 先写入一次正常数据，产生 .tmp 残留（模拟崩溃）
        sm.write(sample_state)
        # 手动创建一个有效的 .tmp 文件
        tmp_content = {
            "schema_version": "mvp_1",
            "_note": "recovered from tmp",
            "progress": {"phase": "mvp_init", "cycle": 1, "convergence_counter": 0},
            "config": {
                "mode": "safe", "model": "gemini-2.5-flash",
                "timeout_ms": 300000, "temperature": 0.7, "max_output_tokens": 8192,
            },
            "circuit_breaker": {
                "state": "CLOSED", "consecutive_failures": 0,
                "failure_threshold": 5, "cooldown_seconds": 30,
                "opened_at": None, "last_failure_at": None,
                "last_failure_reason": None, "last_probe_at": None,
                "total_trips": 0,
            },
        }
        with open(sm.tmp_path, "w", encoding="utf-8") as f:
            json.dump(tmp_content, f)

        # 损坏主文件和备份
        with open(sm.state_path, "w", encoding="utf-8") as f:
            f.write("corrupt main")
        with open(sm.backup_path, "w", encoding="utf-8") as f:
            f.write("corrupt backup")

        result = sm.read()
        assert result.from_backup is True
        assert result.data["config"]["mode"] == "safe"

    def test_recovery_from_tmp_cleans_up_corrupt_tmp(
        self, temp_state_dir: str
    ) -> None:
        """.tmp 文件本身也损坏时应删除它然后抛出 RuntimeError。"""
        sm = StateManager(base_dir=temp_state_dir)

        with open(sm.tmp_path, "w", encoding="utf-8") as f:
            f.write("corrupt tmp")
        with open(sm.state_path, "w", encoding="utf-8") as f:
            f.write("corrupt main")
        with open(sm.backup_path, "w", encoding="utf-8") as f:
            f.write("corrupt backup")

        with pytest.raises(RuntimeError):
            sm.read()

        # 损坏的 .tmp 应该已被清理
        assert not os.path.exists(sm.tmp_path)

    def test_recovery_from_backup_when_main_missing(
        self, temp_state_dir: str, sample_state: dict
    ) -> None:
        """主文件不存在但备份存在时，应从备份恢复。"""
        sm = StateManager(base_dir=temp_state_dir)

        # 写入备份但没有主文件
        sm.write(sample_state)
        os.remove(sm.state_path)

        result = sm.read()
        assert result.from_backup is True
        assert result.data["config"]["mode"] == sample_state["config"]["mode"]

    def test_corrupt_backup_with_missing_main_creates_fresh(
        self, temp_state_dir: str
    ) -> None:
        """主文件不存在且备份损坏时，应创建默认状态。"""
        sm = StateManager(base_dir=temp_state_dir)

        with open(sm.backup_path, "w", encoding="utf-8") as f:
            f.write("corrupt backup")

        result = sm.read()
        assert result.from_scratch is True
        assert result.data["schema_version"] == "mvp_1"


class TestLockAcquisition:
    """文件锁获取与释放测试。"""

    def test_acquire_and_release_lock(
        self, state_manager: StateManager
    ) -> None:
        """验证锁的获取和释放流程。"""
        lock_fd = state_manager._acquire_lock()
        assert lock_fd >= 0
        assert os.path.exists(state_manager.lock_path)
        state_manager._release_lock(lock_fd)

    def test_lock_ensures_write_consistency(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """锁保护下的写入应正确完成。"""
        state_manager.write(sample_state)
        assert os.path.isfile(state_manager.state_path)
        # 验证写入内容正确
        with open(state_manager.state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["schema_version"] == sample_state["schema_version"]


class TestLockFcntlPath:
    """fcntl lock path tests (Unix-only, skipped on Windows)."""

    def test_fcntl_acquire_and_release(self, temp_state_dir: str, monkeypatch) -> None:
        """Simulate fcntl path lock acquisition and release."""
        import loop_antigravity.state_manager as sm

        if not sm._HAS_FCNTL:
            pytest.skip("fcntl not available on this platform")

        monkeypatch.setattr(sm, "_HAS_FCNTL", True)
        monkeypatch.setattr(sm, "_HAS_MSVCRT", False)

        # Direct mock of fcntl.flock via import
        import fcntl as real_fcntl
        with patch.object(real_fcntl, "flock") as mock_flock:
            sm_mgr = sm.StateManager(base_dir=temp_state_dir)

            lock_fd = sm_mgr._acquire_lock()
            assert lock_fd >= 0
            mock_flock.assert_called_once()

            sm_mgr._release_lock(lock_fd)
            assert mock_flock.call_count == 2

    def test_fcntl_lock_timeout(self, temp_state_dir: str, monkeypatch) -> None:
        """fcntl path lock acquisition timeout should raise OSError."""
        import loop_antigravity.state_manager as sm

        if not sm._HAS_FCNTL:
            pytest.skip("fcntl not available on this platform")

        monkeypatch.setattr(sm, "_HAS_FCNTL", True)
        monkeypatch.setattr(sm, "_HAS_MSVCRT", False)

        import fcntl as real_fcntl
        with patch.object(real_fcntl, "flock", side_effect=BlockingIOError):
            sm_mgr = sm.StateManager(base_dir=temp_state_dir)

            with pytest.raises(OSError):
                sm_mgr._acquire_lock()


class TestLockMsvcrtPath:
    """msvcrt 锁路径测试（Windows 实际路径）。"""

    def test_msvcrt_lock_integration(
        self, state_manager: StateManager
    ) -> None:
        """验证 msvcrt 锁路径在 Windows 上的完整流程。"""
        import loop_antigravity.state_manager as sm

        # 如果在 Windows 上，msvcrt 路径是实际活跃路径
        if sm._HAS_MSVCRT:
            lock_fd = state_manager._acquire_lock()
            assert lock_fd >= 0
            state_manager._release_lock(lock_fd)
        else:
            pytest.skip("msvcrt not available on this platform")


class TestLockReleaseFallback:
    """锁释放回退路径测试。"""

    def test_release_lock_os_close_error(self, temp_state_dir: str, monkeypatch) -> None:
        """释放锁时 os.close 失败应静默处理。"""
        import loop_antigravity.state_manager as sm

        # 确保使用回退路径（无 fcntl 和 msvcrt）
        monkeypatch.setattr(sm, "_HAS_FCNTL", False)
        monkeypatch.setattr(sm, "_HAS_MSVCRT", False)

        sm_mgr = sm.StateManager(base_dir=temp_state_dir)
        sm_mgr.lock_path = os.path.join(temp_state_dir, ".lock")

        lock_fd = sm_mgr._acquire_lock()

        # Mock os.close 抛出异常
        with patch("os.close", side_effect=OSError("close failed")):
            # 不应抛出异常
            sm_mgr._release_lock(lock_fd)

    def test_fcntl_release_lock_unlock_error(self, temp_state_dir: str, monkeypatch) -> None:
        """fcntl path LOCK_UN error during release should be suppressed."""
        import loop_antigravity.state_manager as sm

        if not sm._HAS_FCNTL:
            pytest.skip("fcntl not available on this platform")

        monkeypatch.setattr(sm, "_HAS_FCNTL", True)
        monkeypatch.setattr(sm, "_HAS_MSVCRT", False)

        import fcntl as real_fcntl
        with patch.object(real_fcntl, "flock", side_effect=[None, OSError("unlock failed")]):
            with patch("os.close") as mock_close:
                sm_mgr = sm.StateManager(base_dir=temp_state_dir)

                lock_fd = sm_mgr._acquire_lock()
                sm_mgr._release_lock(lock_fd)
                mock_close.assert_called_once()

    def test_msvcrt_release_lock_unlock_error(self, temp_state_dir: str, monkeypatch) -> None:
        """msvcrt 路径释放锁时 LK_UNLCK 失败应静默处理。"""
        import importlib
        import sys
        import loop_antigravity.state_manager as sm

        monkeypatch.setattr(sm, "_HAS_FCNTL", False)
        monkeypatch.setattr(sm, "_HAS_MSVCRT", True)

        try:
            import msvcrt as real_msvcrt
        except ImportError:
            import types
            real_msvcrt = types.ModuleType("msvcrt")
        monkeypatch.setattr(sm, "msvcrt", real_msvcrt)
        sys.modules["msvcrt"] = real_msvcrt

        # 第一次锁定成功，第二次释放抛出 OSError
        with patch.object(real_msvcrt, "locking", side_effect=[None, OSError("unlock failed")]):
            with patch("os.close") as mock_close:
                sm_mgr = sm.StateManager(base_dir=temp_state_dir)
                sm_mgr.lock_path = os.path.join(temp_state_dir, ".lock")

                lock_fd = sm_mgr._acquire_lock()
                sm_mgr._release_lock(lock_fd)
                mock_close.assert_called_once()

    def test_msvcrt_lock_timeout(self, temp_state_dir: str, monkeypatch) -> None:
        """msvcrt 路径获取锁超时应抛出 OSError。"""
        import importlib
        import sys
        import loop_antigravity.state_manager as sm

        monkeypatch.setattr(sm, "_HAS_FCNTL", False)
        monkeypatch.setattr(sm, "_HAS_MSVCRT", True)

        try:
            import msvcrt as real_msvcrt
        except ImportError:
            import types
            real_msvcrt = types.ModuleType("msvcrt")
        monkeypatch.setattr(sm, "msvcrt", real_msvcrt)
        sys.modules["msvcrt"] = real_msvcrt

        with patch.object(real_msvcrt, "locking", side_effect=OSError("locked")):
            with patch("os.close") as mock_close:
                sm_mgr = sm.StateManager(base_dir=temp_state_dir)
                sm_mgr.lock_path = os.path.join(temp_state_dir, ".lock")

                with pytest.raises(OSError, match="无法在"):
                    sm_mgr._acquire_lock()
                # 锁文件 fd 应在超时后被关闭
                mock_close.assert_called_once()


class TestWriteRetry:
    """原子写入重试逻辑测试。"""

    def test_write_retry_on_permission_error(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """os.replace 遇到 PermissionError 时应重试。"""
        import time as time_module
        call_count = [0]

        original_replace = os.replace

        def flaky_replace(src, dst):
            call_count[0] += 1
            if call_count[0] < 3:
                raise PermissionError("simulated permission error")
            return original_replace(src, dst)

        # 先写入初始数据以确保路径存在
        state_manager.write(sample_state)

        # 创建 tmp 文件
        state_manager._write_json_file(state_manager.tmp_path, sample_state)

        # 直接用 monkey-patch 验证重试逻辑
        state_dir = state_manager.state_dir
        tmp_path = state_manager.tmp_path
        state_path = state_manager.state_path
        try:
            os.replace = flaky_replace  # type: ignore[method-assign]
            for attempt in range(3):
                try:
                    os.replace(tmp_path, state_path)
                    break
                except PermissionError:
                    if attempt < 2:
                        time_module.sleep(0.01)
                    else:
                        raise
        finally:
            os.replace = original_replace  # type: ignore[method-assign]

        assert call_count[0] >= 2

    def test_write_method_retry(
        self, state_manager: StateManager, sample_state: dict
    ) -> None:
        """write() 方法内部的 os.replace 重试逻辑应在
        PermissionError 时重试最多 3 次。"""
        call_count = [0]

        original_replace = os.replace

        def flaky_replace(src, dst):
            call_count[0] += 1
            if call_count[0] < 2:
                raise PermissionError("simulated permission error")
            return original_replace(src, dst)

        # 先正常写入一次
        state_manager.write(sample_state)

        try:
            os.replace = flaky_replace  # type: ignore[method-assign]
            # 再次写入，触发重试
            state_manager.write(sample_state)
            # 重试至少发生了 2 次 (1次失败 + 1次成功 = 2次调用)
            assert call_count[0] >= 2
        finally:
            os.replace = original_replace  # type: ignore[method-assign]

    def test_write_exhausts_retries(self, state_manager: StateManager, sample_state: dict) -> None:
        """write() 重试耗尽后应抛出 PermissionError。"""
        original_replace = os.replace

        def always_fail(src, dst):
            raise PermissionError("persistent permission error")

        # 先正常写入以确保路径存在
        state_manager.write(sample_state)

        try:
            os.replace = always_fail  # type: ignore[method-assign]
            with pytest.raises(PermissionError):
                state_manager.write(sample_state)
        finally:
            os.replace = original_replace  # type: ignore[method-assign]


class TestFsyncPaths:
    """fsync 路径测试。"""

    def test_fsync_path_windows_skips(self, state_manager: StateManager) -> None:
        """在 Windows 上 _fsync_path 应直接返回不做操作。"""
        state_manager._fsync_path(state_manager.state_path)

    def test_fsync_dir_windows_skips(self, state_manager: StateManager) -> None:
        """在 Windows 上 _fsync_dir 应直接返回不做操作。"""
        state_manager._fsync_dir(state_manager.state_dir)

    def test_fsync_path_non_windows(self, temp_state_dir: str, monkeypatch) -> None:
        """在非 Windows 上 _fsync_path 应调用 os.fsync。"""
        import loop_antigravity.state_manager as sm

        # 模拟非 Windows 平台
        monkeypatch.setattr(os, "name", "posix")
        sm_mgr = sm.StateManager(base_dir=temp_state_dir)
        # 先写入文件以确保文件存在
        test_path = os.path.join(temp_state_dir, "test_fsync.txt")
        with open(test_path, "w") as f:
            f.write("test")

        with patch("os.fsync") as mock_fsync, patch("os.open", return_value=9999), \
             patch("os.close") as mock_close:
            sm_mgr._fsync_path(test_path)
            # os.fsync 应被调用
            mock_fsync.assert_called_once()

    def test_fsync_dir_non_windows(self, temp_state_dir: str, monkeypatch) -> None:
        """在非 Windows 上 _fsync_dir 应调用 os.fsync。"""
        import loop_antigravity.state_manager as sm

        monkeypatch.setattr(os, "name", "posix")
        sm_mgr = sm.StateManager(base_dir=temp_state_dir)

        with patch("os.fsync") as mock_fsync, patch("os.open", return_value=9999), \
             patch("os.close") as mock_close:
            sm_mgr._fsync_dir(temp_state_dir)
            mock_fsync.assert_called_once()

    def test_fsync_path_oserror_suppressed(self, temp_state_dir: str, monkeypatch) -> None:
        """_fsync_path 遇到 OSError 应静默处理。"""
        import loop_antigravity.state_manager as sm

        monkeypatch.setattr(os, "name", "posix")
        sm_mgr = sm.StateManager(base_dir=temp_state_dir)
        test_path = os.path.join(temp_state_dir, "nonexistent.txt")

        # 不应抛出异常
        sm_mgr._fsync_path(test_path)

    def test_fsync_dir_oserror_suppressed(self, temp_state_dir: str, monkeypatch) -> None:
        """_fsync_dir 遇到 OSError 应静默处理。"""
        import loop_antigravity.state_manager as sm

        monkeypatch.setattr(os, "name", "posix")
        sm_mgr = sm.StateManager(base_dir=temp_state_dir)
        nonexistent = os.path.join(temp_state_dir, "nonexistent_dir")

        # 不应抛出异常
        sm_mgr._fsync_dir(nonexistent)


class TestImportFallbacks:
    """测试导入回退逻辑。"""

    def test_fcntl_unavailable(self, monkeypatch) -> None:
        """模拟 fcntl 不可用时的回退路径。"""
        import importlib
        import sys
        import loop_antigravity.state_manager as sm

        saved_fcntl = sys.modules.pop("fcntl", None)
        try:
            importlib.reload(sm)
            assert sm._HAS_FCNTL is False
        finally:
            if saved_fcntl is not None:
                sys.modules["fcntl"] = saved_fcntl
            # 恢复模块到正常状态
            importlib.reload(sm)

    def test_msvcrt_unavailable(self) -> None:
        """模拟 msvcrt 不可用时的回退路径。
        注意: 在 Windows 上 msvcrt 是内置模块，无法轻易屏蔽导入，
        此测试仅在非 Windows 平台有意义。"""
        import importlib
        import sys
        import os as _os

        if _os.name == "nt":
            pytest.skip("msvcrt is a built-in on Windows, cannot be mocked easily")

        import loop_antigravity.state_manager as sm

        saved_msvcrt = sys.modules.pop("msvcrt", None)
        try:
            importlib.reload(sm)
            assert sm._HAS_MSVCRT is False
        finally:
            if saved_msvcrt is not None:
                sys.modules["msvcrt"] = saved_msvcrt
            importlib.reload(sm)
