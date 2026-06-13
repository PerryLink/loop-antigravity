"""M1 MVP 门禁验证模块。

验证 core loop 最小端到端闭环:
    1. agy CLI 可用性检查
    2. GCloud 认证检查
    3. CircuitBreaker CLOSED->OPEN->HALF_OPEN->CLOSED 状态转换
    4. Gemini ping 连通性测试
    5. state.json 结果更新

用法:
    python -m loop_antigravity.mvp_helloworld --init
    python -m loop_antigravity.mvp_helloworld --mode auto
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

from loop_antigravity import __version__
from loop_antigravity.config import Config
from loop_antigravity.state_manager import StateManager
from loop_antigravity.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    FailureCategory,
)
from loop_antigravity.agy_client import AgyClient


def _check_agy_cli() -> bool:
    """检查 agy CLI 是否在 PATH 上可用。

    Returns:
        True 表示 agy 可执行文件已找到。
    """
    path = shutil.which("agy")
    if path:
        print(f"  [OK] agy CLI 位于: {path}")
        return True
    print("  [FAIL] agy CLI 未找到，请安装: pip install google-antigravity")
    return False


def _check_gcloud_auth() -> bool:
    """检查 GCloud 认证状态。

    通过 gcloud auth print-access-token 验证当前凭证。

    Returns:
        True 表示已认证且 token 有效。
    """
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            print("  [OK] GCloud 已认证")
            return True
        print(f"  [FAIL] GCloud 未认证: {result.stderr.strip()[:200]}")
        return False
    except FileNotFoundError:
        print("  [FAIL] gcloud CLI 未安装，请安装 Google Cloud SDK")
        return False
    except subprocess.TimeoutExpired:
        print("  [FAIL] gcloud auth 命令超时")
        return False


def _verify_circuit_breaker() -> bool:
    """验证 CircuitBreaker 完整状态转换链。

    CLOSED -> OPEN -> HALF_OPEN -> CLOSED 链路验证。

    Returns:
        True 表示所有状态转换正确。
    """
    cfg = CircuitBreakerConfig(failure_threshold=2, cooldown_seconds=0.3)
    cb = CircuitBreaker(cfg)
    phase = "CLOSED"
    print(f"  初始: {phase}")

    # 1. CLOSED -> OPEN: 连续失败触发熔断
    for i in range(cfg.failure_threshold):
        cb.report_failure(FailureCategory.SERVER_ERROR, f"测试失败 #{i}")
    if not cb.is_open:
        print("  [FAIL] CLOSED -> OPEN 转换失败")
        return False
    print(f"  [OK] CLOSED -> OPEN (consecutive_failures={cb.consecutive_failures})")

    # 2. guard() 在冷却期内应拒绝请求
    gr = cb.guard()
    if not gr.blocked:
        print("  [FAIL] OPEN 状态下 guard() 应阻止请求")
        return False
    print(f"  [OK] OPEN 状态下 guard() 正确阻止请求")

    # 3. 等待冷却 -> guard() 自动转换到 HALF_OPEN
    time.sleep(0.4)
    gr = cb.guard()
    if gr.blocked:
        print(f"  [FAIL] 冷却结束后 guard() 不应阻止: {gr.reason}")
        return False
    if cb.state != CircuitState.HALF_OPEN:
        print(f"  [FAIL] 应进入 HALF_OPEN 但当前: {cb.state.value}")
        return False
    print("  [OK] OPEN -> HALF_OPEN (冷却结束)")

    # 4. HALF_OPEN -> CLOSED: 探测成功
    cb.report_success()
    if not cb.is_closed:
        print(f"  [FAIL] 探测成功后应回到 CLOSED: {cb.state.value}")
        return False
    print("  [OK] HALF_OPEN -> CLOSED (探测成功)")

    return True


def _test_gemini_ping(config: Config) -> bool:
    """通过 AgyClient.check_health() 测试 Gemini 连通性。

    Args:
        config: 运行时配置。

    Returns:
        True 表示 Gemini API 可达且正常响应。
    """
    try:
        cb = CircuitBreaker.for_mode(config.mode)
        agy = AgyClient(mode=config.mode, circuit_breaker=cb)
        health = agy.check_health()
        if health.ok:
            print(f"  [OK] Gemini ping 成功 (agy v{health.version})")
            return True
        print(f"  [WARN] Gemini ping 失败: {health.message}")
        return False
    except Exception as e:
        print(f"  [WARN] Gemini 调用异常 (非致命): {e}")
        return False


def _update_state(sm: StateManager, all_ok: bool) -> None:
    """将 MVP 结果写入 state.json。

    Args:
        sm: StateManager 实例。
        all_ok: 所有门禁检查是否通过。
    """
    result = sm.read()
    state = result.data
    state["progress"]["phase"] = "mvp_complete" if all_ok else "mvp_failed"
    state["mvp_result"]["status"] = "passed" if all_ok else "failed"
    state["mvp_result"]["completed_at"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
    )
    sm.write(state)
    print(f"  [OK] state.json 已更新 (phase={state['progress']['phase']})")


def mvp_run(config: Config, sm: StateManager) -> bool:
    """执行完整 MVP 门禁验证流程。

    依次执行: agy CLI 检查、GCloud 认证、CircuitBreaker 状态转换、
    Gemini ping 测试，并将结果写入 state.json。

    Args:
        config: 运行时配置实例。
        sm: StateManager 实例。

    Returns:
        True 表示所有门禁项通过。
    """
    print("=" * 50)
    print(f"loop-antigravity MVP 门禁 v{__version__}")
    print(f"模式: {config.mode} | 模型: {config.agy.model}")
    print(f"状态目录: {sm.state_dir}")
    print("=" * 50)

    cb_ok = _verify_circuit_breaker()
    agy_ok = _check_agy_cli()

    gcloud_ok = False
    gemini_ok = False
    if agy_ok:
        gcloud_ok = _check_gcloud_auth()
        if gcloud_ok:
            gemini_ok = _test_gemini_ping(config)
    else:
        print("[跳过] GCloud 认证检查 (agy CLI 不可用)")
        print("[跳过] Gemini ping 测试 (agy CLI 不可用)")

    all_ok = all([cb_ok, agy_ok, gcloud_ok, gemini_ok])
    _update_state(sm, all_ok)

    print("=" * 50)
    if all_ok:
        print("MVP HELLOWORLD PASSED")
    else:
        failures = []
        if not cb_ok:
            failures.append("circuit_breaker")
        if not agy_ok:
            failures.append("agy_cli")
        if not gcloud_ok:
            failures.append("gcloud_auth")
        if not gemini_ok:
            failures.append("gemini_ping")
        print(f"MVP HELLOWORLD FAILED (失败项: {failures})")
    print("=" * 50)
    return all_ok


def main(argv: list[str] = None) -> int:
    """MVP 门禁 CLI 入口。

    Args:
        argv: 命令行参数列表，默认 sys.argv[1:]。

    Returns:
        退出码 (0=通过, 1=失败)。
    """
    parser = argparse.ArgumentParser(prog="mvp-helloworld")
    parser.add_argument(
        "--init", action="store_true",
        help="初始化 state.json (如已存在则跳过)",
    )
    parser.add_argument(
        "--mode", default="auto",
        choices=("safe", "auto", "unsafe", "collaborative"),
        help="操作模式 (默认 auto)",
    )
    parser.add_argument(
        "--state-dir", default=".claude/loop-antigravity",
        help="状态文件目录",
    )
    args = parser.parse_args(argv)

    try:
        config = Config(mode=args.mode)
    except ValueError as e:
        print(f"[ERROR] 无效配置: {e}")
        return 1

    sm = StateManager(base_dir=args.state_dir)

    if args.init:
        result = sm.read()
        if result.from_scratch:
            print(f"[init] 已创建新 state.json: {sm.state_path}")
        else:
            print(f"[init] state.json 已存在: {sm.state_path}")

    ok = mvp_run(config, sm)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
