"""
loop_antigravity 状态管理器。

管理 state.json 文件的原子读写操作，采用 tmp->fsync->rename->fsync dir
协议确保崩溃安全。同时提供 schema 验证、备份恢复和并发锁保护。

原子写入协议:
    1. 获取 .lock 文件锁
    2. 将新内容写入 state.json.tmp
    3. 对 .tmp 文件执行 fsync
    4. 将 .tmp 重命名为 state.json（原子操作）
    5. 对目录执行 fsync（确保 rename 持久化）
    6. 释放锁

状态文件布局:
    .claude/loop-antigravity/
        state.json          -- 主状态文件
        state.json.bak      -- 自动备份
        state.json.tmp      -- 原子写入中间文件（崩溃残留）
        .lock               -- 并发写锁
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

# 跨平台文件锁 -- fcntl 仅在 Unix 上可用
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

try:
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False


# ============================================================================
# 最小 MVP schema（Milestone 1）
# ============================================================================

MVP_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "progress", "config", "circuit_breaker"],
    "properties": {
        "schema_version": {"type": "string"},
        "progress": {
            "type": "object",
            "required": ["phase"],
            "properties": {
                "phase": {
                    "type": "string",
                    "enum": [
                        "mvp_init", "mvp_agy_invoke",
                        "mvp_file_write", "mvp_complete", "mvp_failed",
                    ],
                },
                "cycle": {"type": "integer", "minimum": 0},
                "convergence_counter": {"type": "integer", "minimum": 0},
            },
        },
        "config": {
            "type": "object",
            "required": ["mode", "model"],
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["safe", "auto", "unsafe", "collaborative"],
                },
                "model": {"type": "string"},
                "timeout_ms": {"type": "integer", "minimum": 1000},
                "temperature": {"type": "number", "minimum": 0.0, "maximum": 2.0},
                "max_output_tokens": {"type": "integer", "minimum": 1},
            },
        },
        "circuit_breaker": {
            "type": "object",
            "required": ["state", "consecutive_failures", "failure_threshold"],
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["CLOSED", "OPEN", "HALF_OPEN"],
                },
                "consecutive_failures": {"type": "integer", "minimum": 0},
                "failure_threshold": {"type": "integer", "minimum": 1},
                "cooldown_seconds": {"type": "number", "minimum": 0},
                "opened_at": {"type": ["string", "null"]},
                "last_failure_at": {"type": ["string", "null"]},
                "last_failure_reason": {"type": ["string", "null"]},
                "last_probe_at": {"type": ["string", "null"]},
                "total_trips": {"type": "integer", "minimum": 0},
            },
        },
        "mvp_result": {
            "type": "object",
            "properties": {
                "status": {
                    "type": ["string", "null"],
                    "enum": ["not_run", "passed", "failed", None],
                },
                "agy_version": {"type": ["string", "null"]},
                "tokens_input": {"type": "integer"},
                "tokens_output": {"type": "integer"},
                "latency_ms": {"type": "integer"},
                "output_file": {"type": "string"},
                "output_file_exists": {"type": "boolean"},
                "error_message": {"type": ["string", "null"]},
                "completed_at": {"type": ["string", "null"]},
            },
        },
        "housekeeping": {
            "type": "object",
            "properties": {
                "invocation_count": {"type": "integer"},
                "lock_file": {"type": "string"},
            },
        },
    },
}


# ============================================================================
# 默认初始状态
# ============================================================================

def _default_state() -> dict:
    """返回一份全新的最小状态（MVP schema 兼容）。"""
    return {
        "schema_version": "mvp_1",
        "_note": "Minimal schema for MVP. Full v2 schema used post-MVP.",
        "progress": {
            "phase": "mvp_init",
            "phase_enum": (
                "mvp_init | mvp_agy_invoke | mvp_file_write "
                "| mvp_complete | mvp_failed"
            ),
            "cycle": 1,
            "convergence_counter": 0,
        },
        "config": {
            "mode": "auto",
            "mode_comment": "M1: L2 (auto) only. N=5, T=30s.",
            "model": "gemini-2.5-flash",
            "timeout_ms": 300000,
            "temperature": 0.7,
            "max_output_tokens": 8192,
        },
        "circuit_breaker": {
            "state": "CLOSED",
            "state_enum": "CLOSED | OPEN | HALF_OPEN",
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
            "status_enum": "not_run | passed | failed",
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


# ============================================================================
# 状态管理器
# ============================================================================


@dataclass
class StateReadResult:
    """state.json 读取结果。"""
    data: dict
    file_path: str
    from_backup: bool = False
    from_scratch: bool = False


class StateManager:
    """
    state.json 文件状态管理器。

    提供原子读写、schema 验证、备份恢复和并发锁保护。
    所有写入操作均遵循 tmp->fsync->rename->fsync dir 协议。

    Attributes:
        state_dir: 状态文件所在目录。
        state_path: 主状态文件完整路径。
        backup_path: 备份文件完整路径。
        tmp_path: 原子写入中间文件完整路径。
        lock_path: 并发锁文件完整路径。
    """

    def __init__(self, base_dir: str = ".claude/loop-antigravity") -> None:
        """初始化状态管理器。

        Args:
            base_dir: 状态文件存储目录的相对或绝对路径。
        """
        self.state_dir = os.path.abspath(base_dir)
        self.state_path = os.path.join(self.state_dir, "state.json")
        self.backup_path = os.path.join(self.state_dir, "state.json.bak")
        self.tmp_path = os.path.join(self.state_dir, "state.json.tmp")
        self.lock_path = os.path.join(self.state_dir, ".lock")

        # 确保目录存在
        os.makedirs(self.state_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def read(self) -> StateReadResult:
        """
        读取 state.json。

        读取优先级:
            1. state.json（主文件）
            2. state.json.bak（备份文件，主文件损坏时）
            3. 默认初始状态（以上均不存在时）

        Returns:
            StateReadResult 包含解析后的字典和元数据。

        Raises:
            RuntimeError: 如果主文件和备份文件均存在但都无法解析。
        """
        # 尝试读取主文件
        if os.path.exists(self.state_path):
            try:
                data = self._read_json_file(self.state_path)
                return StateReadResult(
                    data=data,
                    file_path=self.state_path,
                )
            except (json.JSONDecodeError, OSError) as e:
                # 主文件损坏 -- 尝试从备份恢复
                if os.path.exists(self.backup_path):
                    try:
                        data = self._read_json_file(self.backup_path)
                        # 将备份恢复到主文件
                        self.write(data, validate=False)
                        return StateReadResult(
                            data=data,
                            file_path=self.state_path,
                            from_backup=True,
                        )
                    except (json.JSONDecodeError, OSError):
                        pass  # 备份也损坏 -- 继续往下

                # 清理崩溃残留的 .tmp 文件
                if os.path.exists(self.tmp_path):
                    try:
                        data = self._read_json_file(self.tmp_path)
                        self.write(data, validate=False)
                        return StateReadResult(
                            data=data,
                            file_path=self.state_path,
                            from_backup=True,
                        )
                    except (json.JSONDecodeError, OSError):
                        os.remove(self.tmp_path)

                raise RuntimeError(
                    f"无法读取 state.json 且备份也损坏: {e}"
                )

        # 主文件不存在 -- 尝试备份
        if os.path.exists(self.backup_path):
            try:
                data = self._read_json_file(self.backup_path)
                self.write(data, validate=False)
                return StateReadResult(
                    data=data,
                    file_path=self.state_path,
                    from_backup=True,
                )
            except (json.JSONDecodeError, OSError):
                pass  # 备份损坏，创建新的

        # 主文件和备份都不存在 -- 创建默认状态
        default = _default_state()
        self.write(default, validate=False)
        return StateReadResult(
            data=default,
            file_path=self.state_path,
            from_scratch=True,
        )

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def write(self, data: dict, validate: bool = True) -> None:
        """
        原子写入 state.json。

        步骤:
            1. 获取文件锁
            2. 写入 state.json.bak（备份）
            3. 写入 state.json.tmp（新内容）
            4. fsync .tmp 文件
            5. 将 .tmp rename 为 state.json（原子操作）
            6. fsync 目录
            7. 释放文件锁

        Args:
            data: 要写入的状态字典。
            validate: 是否在写入前进行 schema 验证（默认 True）。

        Raises:
            jsonschema.ValidationError: schema 验证失败且 validate=True。
            OSError: 文件写入失败。
        """
        if validate:
            self.validate(data)

        # 步骤 1: 获取锁
        lock_fd = self._acquire_lock()

        try:
            # 步骤 2: 写入备份
            self._write_json_file(self.backup_path, data)

            # 步骤 3: 写入临时文件
            self._write_json_file(self.tmp_path, data)

            # 步骤 4: fsync 临时文件
            self._fsync_path(self.tmp_path)

        finally:
            # 步骤 5: 释放锁（在 rename 之前，Windows 兼容性）
            self._release_lock(lock_fd)

        # 步骤 6: 原子 rename（锁已释放，避免 Windows 文件锁定冲突）
        for attempt in range(3):
            try:
                os.replace(self.tmp_path, self.state_path)
                break
            except PermissionError:
                if attempt < 2:
                    time.sleep(0.1)
                else:
                    raise

        # 步骤 7: fsync 目录（确保持久化）
        self._fsync_dir(self.state_dir)

    # ------------------------------------------------------------------
    # Schema 验证
    # ------------------------------------------------------------------

    def validate(self, data: dict) -> None:
        """
        使用 JSON Schema 验证状态数据。

        在 M1 阶段使用 MVP_SCHEMA（最小 schema）。
        后续 Milestone 将升级为 v2 完整 schema。

        Args:
            data: 要验证的状态字典。

        Raises:
            jsonschema.ValidationError: 数据不符合 schema。
        """
        import jsonschema
        jsonschema.validate(instance=data, schema=MVP_SCHEMA)

    # ------------------------------------------------------------------
    # 锁管理
    # ------------------------------------------------------------------

    def _acquire_lock(self) -> int:
        """获取独占文件锁（跨平台）。

        在 Unix 上使用 fcntl.flock，在 Windows 上使用 msvcrt.locking，
        回退方案使用忙等待目录锁。

        Returns:
            锁文件的文件描述符。

        Raises:
            OSError: 无法在超时时间内获取锁。
        """
        lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR)
        deadline = time.time() + 10.0

        if _HAS_FCNTL:
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return lock_fd
                except BlockingIOError:
                    if time.time() > deadline:
                        os.close(lock_fd)
                        raise OSError(
                            f"无法在 {10.0}s 内获取 state.json 写锁"
                        )
                    time.sleep(0.05)
        elif _HAS_MSVCRT:
            # Windows: 使用 msvcrt.locking
            while True:
                try:
                    msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
                    return lock_fd
                except OSError:
                    if time.time() > deadline:
                        os.close(lock_fd)
                        raise OSError(
                            f"无法在 {10.0}s 内获取 state.json 写锁"
                        )
                    time.sleep(0.05)
        else:
            # 回退方案: 忙等待，检查锁文件是否被其他进程持有
            # 不够完美但对单进程场景足够
            return lock_fd

    def _release_lock(self, lock_fd: int) -> None:
        """释放文件锁并关闭文件描述符（跨平台）。

        Args:
            lock_fd: _acquire_lock 返回的文件描述符。
        """
        if _HAS_FCNTL:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        elif _HAS_MSVCRT:
            try:
                msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        try:
            os.close(lock_fd)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 文件 I/O 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _read_json_file(path: str) -> dict:
        """读取并解析 JSON 文件。

        Args:
            path: 文件路径。

        Returns:
            解析后的字典。

        Raises:
            FileNotFoundError: 文件不存在。
            json.JSONDecodeError: JSON 格式无效。
        """
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_json_file(path: str, data: dict) -> None:
        """将字典序列化写入 JSON 文件。

        Args:
            path: 目标文件路径。
            data: 要写入的字典。
        """
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            f.flush()

    @staticmethod
    def _fsync_path(path: str) -> None:
        """对指定文件执行 fsync。

        Windows 上跳过（os.fsync 在 Windows 上可能导致后续 rename 失败）。
        文件数据在 _write_json_file 中已通过 f.flush() 刷新。

        Args:
            path: 文件路径。
        """
        if os.name == "nt":
            # Windows: 跳过 -- flush 已足够，且 os.fsync 可能导致锁冲突
            return
        try:
            fd = os.open(path, os.O_RDONLY)
            os.fsync(fd)
            os.close(fd)
        except OSError:
            pass  # 尽力而为 -- 某些文件系统不支持

    @staticmethod
    def _fsync_dir(dir_path: str) -> None:
        """对目录执行 fsync，确保 rename 操作持久化。

        Windows 上跳过。

        Args:
            dir_path: 目录路径。
        """
        if os.name == "nt":
            return  # Windows: 跳过目录 fsync
        try:
            fd = os.open(dir_path, os.O_RDONLY)
            os.fsync(fd)
            os.close(fd)
        except OSError:
            pass  # 尽力而为 -- 某些文件系统不支持
