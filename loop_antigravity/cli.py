"""
loop_antigravity CLI 入口点。

提供命令行界面用于:
    - --init: 初始化 state.json
    - --check / --check-deps: 检查依赖和健康状态
    - --safe / --auto / --unsafe: 设置操作模式
    - --project / --location: 配置 GCP 项目和区域
    - --version: 显示版本信息

用法:
    python -m loop_antigravity.cli --check
    python -m loop_antigravity.cli --init --mode auto
    python -m loop_antigravity.cli --safe --project my-gcp-project
"""

from __future__ import annotations

import argparse
import sys
import os
import traceback

from loop_antigravity import __version__
from loop_antigravity.config import Config, VALID_MODES
from loop_antigravity.state_manager import StateManager
from loop_antigravity.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    Returns:
        配置好的 ArgumentParser 实例。
    """
    parser = argparse.ArgumentParser(
        prog="loop-antigravity",
        description=(
            "loop-antigravity -- 基于 Google Antigravity (agy CLI) 的\n"
            "目标驱动自主开发工具。利用 Gemini 3.5 Flash 的 1M token\n"
            "上下文窗口实现设计、实现、测试、验证的完整闭环。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  loop-antigravity --check\n"
            "  loop-antigravity --init --mode auto\n"
            "  loop-antigravity --safe --project my-project\n"
            "  python -m loop_antigravity.cli --version"
        ),
    )

    # 版本
    parser.add_argument(
        "--version", "-V", action="version",
        version=f"loop-antigravity v{__version__}",
    )

    # 模式选择
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--safe", action="store_const", const="safe", dest="mode",
        help="L1 Shield 模式（保守熔断，低计费上限）",
    )
    mode_group.add_argument(
        "--auto", action="store_const", const="auto", dest="mode",
        help="L2 Standard 模式（默认，平衡的熔断和计费）",
    )
    mode_group.add_argument(
        "--unsafe", action="store_const", const="unsafe", dest="mode",
        help="L3 Unlimited 模式（最宽松的熔断，仅在可信沙箱中使用）",
    )

    # 操作命令
    parser.add_argument(
        "--init", action="store_true",
        help="初始化 state.json（如果已存在则报错）",
    )
    parser.add_argument(
        "--check", "--check-deps", action="store_true", dest="check",
        help="检查所有依赖和核心组件健康状态",
    )

    # GCP 配置
    parser.add_argument(
        "--project", type=str, default=None,
        help="GCP 项目 ID",
    )
    parser.add_argument(
        "--location", type=str, default=None,
        help="GCP 区域（默认 us-central1）",
    )

    # 其他
    parser.add_argument(
        "--state-dir", type=str,
        default=".claude/loop-antigravity",
        help="state.json 存储目录（默认 .claude/loop-antigravity）",
    )

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    """执行 --init 命令。

    在指定的 state-dir 中创建初始 state.json。
    如果已存在则报错退出。

    Args:
        args: 解析后的命令行参数。

    Returns:
        退出码（0 成功，1 失败）。
    """
    state_dir = args.state_dir
    state_path = os.path.join(state_dir, "state.json")

    if os.path.exists(state_path):
        print(f"[ERROR] state.json 已存在: {state_path}")
        print("  如需重新初始化，请先删除或备份现有文件。")
        return 1

    mode = args.mode or "auto"
    try:
        config = Config(mode=mode)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1

    print(f"[init] 正在创建初始 state.json ...")
    print(f"  模式: {mode}")
    print(f"  目录: {os.path.abspath(state_dir)}")
    print(f"  模型: {config.agy.model}")

    sm = StateManager(base_dir=state_dir)

    # 使用 StateManager 的默认状态并应用命令行参数
    result = sm.read()
    state = result.data
    state["config"]["mode"] = mode
    state["config"]["model"] = config.agy.model
    state["config"]["timeout_ms"] = config.runtime.timeout_ms
    state["config"]["temperature"] = config.runtime.temperature
    state["config"]["max_output_tokens"] = config.runtime.max_output_tokens
    state["circuit_breaker"]["failure_threshold"] = config.failure_threshold
    state["circuit_breaker"]["cooldown_seconds"] = config.cooldown_seconds

    sm.write(state)
    print(f"[init] state.json 创建成功: {state_path}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """执行 --check 命令。

    检查:
      1. state.json 是否存在且有效
      2. agy CLI 是否已安装
      3. CircuitBreaker 是否正常

    Args:
        args: 解析后的命令行参数。

    Returns:
        退出码（0 全部通过，1 有失败项）。
    """
    print("=" * 50)
    print("loop-antigravity 依赖检查")
    print("=" * 50)

    all_ok = True
    state_dir = args.state_dir

    # 1. 检查 state.json
    print("\n[1/3] 检查 state.json ...")
    sm = StateManager(base_dir=state_dir)
    try:
        result = sm.read()
        state = result.data
        print(f"  [OK] state.json 存在且可解析")
        if result.from_backup:
            print(f"  [WARN] 从备份恢复 (state.json.bak)")
        print(f"  schema: {state.get('schema_version', 'unknown')}")
        print(f"  phase: {state.get('progress', {}).get('phase', 'unknown')}")
        print(f"  mode: {state.get('config', {}).get('mode', 'unknown')}")
    except Exception as e:
        print(f"  [FAIL] state.json 读取失败: {e}")
        all_ok = False

    # 2. 检查 CircuitBreaker
    print("\n[2/3] 检查 CircuitBreaker ...")
    try:
        mode = args.mode or state.get("config", {}).get("mode", "auto")
        cb = CircuitBreaker.for_mode(mode)
        print(f"  [OK] CircuitBreaker 已初始化 (mode={mode}, "
              f"threshold={cb.failure_threshold}, "
              f"cooldown={cb.cooldown_seconds}s)")
        print(f"  状态: {cb.state.value}")
    except Exception as e:
        print(f"  [FAIL] CircuitBreaker 初始化失败: {e}")
        all_ok = False

    # 3. 检查 agy CLI
    print("\n[3/3] 检查 agy CLI ...")
    try:
        from loop_antigravity.agy_client import AgyClient
        agy = AgyClient(mode="auto", circuit_breaker=cb)
        health = agy.check_health()
        if health.ok:
            print(f"  [OK] agy CLI 健康 (v{health.version})")
            flags = health.flags_supported
            for flag, ok in flags.items():
                status = "OK" if ok else "NOT SUPPORTED"
                print(f"    {flag}: {status}")
        else:
            print(f"  [FAIL] agy CLI 不健康: {health.message}")
            all_ok = False
    except Exception as e:
        print(f"  [FAIL] agy CLI 检查失败: {e}")
        all_ok = False

    print("\n" + "=" * 50)
    if all_ok:
        print("所有检查通过。loop-antigravity 已就绪。")
        return 0
    else:
        print("存在失败的检查项，请查看上方详细信息。")
        return 1


def main(argv: list[str] = None) -> int:
    """CLI 主入口点。

    Args:
        argv: 命令行参数列表。默认使用 sys.argv[1:]。

    Returns:
        退出码。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # 默认模式为 auto
    if args.mode is None:
        args.mode = "auto"

    try:
        if args.init:
            return cmd_init(args)
        elif args.check:
            return cmd_check(args)
        else:
            # 无命令时显示帮助
            parser.print_help()
            return 0

    except KeyboardInterrupt:
        print("\n[ABORT] 用户中断。")
        return 130
    except Exception as e:
        print(f"\n[FATAL] 未预期的错误: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
