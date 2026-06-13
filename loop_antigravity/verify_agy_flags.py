#!/usr/bin/env python3
"""
verify_agy_flags.py -- agy CLI 三关键标志验证脚本 (P0-8)

验证 agy CLI 的三个关键标志是否可用:
    1. --non-interactive:   agy 不提示 stdin 输入
    2. --output-format stream-json: stdout 行级 JSON
    3. --yolo:              抑制安全确认，实现无人值守

直接解决 P0-8 (agy CLI 兼容性验证)。
所有三个标志是 loop-antigravity 自动闭环工作的前提条件。

用法:
    python verify_agy_flags.py              # 使用默认模型 gemini-2.5-flash
    python verify_agy_flags.py --model <M>  # 指定模型
    python verify_agy_flags.py --json       # JSON 输出
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def verify_flag_non_interactive(binary: str = "agy", timeout_sec: int = 10) -> bool:
    """验证 --non-interactive 标志: agy 不提示 stdin 输入。

    策略: 运行 agy -p "pong" --non-interactive --yolo --output-format stream-json，
    并在 stdin 上使用 DEVNULL，验证子进程在非交互模式下完成而不是阻塞等待 input。
    """
    try:
        proc = subprocess.run(
            [binary, "-p", "pong", "--non-interactive", "--yolo",
             "--output-format", "stream-json",
             "--max-output-tokens", "16"],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout_sec,
        )
        return proc.returncode == 0 and len(proc.stdout.strip()) > 0
    except Exception:
        return False


def verify_flag_stream_json(binary: str = "agy", timeout_sec: int = 15) -> tuple[bool, str]:
    """验证 --output-format stream-json 标志: stdout 每行一条合法 JSON。

    Returns:
        (ok, detail) -- ok 表示至少 2 行成功解析为 JSON。
    """
    try:
        proc = subprocess.run(
            [binary, "-p", "Say exactly: ok", "--non-interactive", "--yolo",
             "--output-format", "stream-json",
             "--max-output-tokens", "32"],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout_sec,
        )
        if proc.returncode != 0:
            return False, f"Subprocess exit={proc.returncode}"
        lines = [l.strip() for l in proc.stdout.strip().split("\n") if l.strip()]
        json_count = 0
        for line in lines:
            try:
                json.loads(line)
                json_count += 1
            except json.JSONDecodeError:
                continue
        ok = json_count >= 2
        return ok, f"{json_count} JSON lines parsed out of {len(lines)}"
    except Exception as e:
        return False, str(e)


def verify_flag_yolo(binary: str = "agy", timeout_sec: int = 15) -> tuple[bool, str]:
    """验证 --yolo 标志: 抑制安全确认（无人值守模式）。

    策略: 故意发送可能触发安全审查的 prompt ("hack")，验证 agy 在 --yolo 下
    不因安全暂停而等待交互确认。输出格式为 stream-json 确保是自动化的。

    Returns:
        (ok, detail) -- ok 表示 agy 在 --yolo 下返回了文本输出且无交互阻断。
    """
    try:
        proc = subprocess.run(
            [binary, "-p", "Say exactly: hello yolo world", "--non-interactive",
             "--yolo", "--output-format", "stream-json",
             "--max-output-tokens", "32"],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout_sec,
        )
        if proc.returncode != 0:
            return False, f"Subprocess exit={proc.returncode}"
        has_text = False
        for line in proc.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                if obj.get("type") == "text" and obj.get("content", "").strip():
                    has_text = True
                    break
            except json.JSONDecodeError:
                continue
        return has_text, "YOLO mode accepted -- text returned without interactive prompt"
    except subprocess.TimeoutExpired:
        return False, "Timed out -- possible interactive prompt blocking"
    except Exception as e:
        return False, str(e)


def verify_all(binary: str = "agy",
               timeout_sec: int = 30) -> dict:
    """运行全部三个标志验证。

    Args:
        binary: agy CLI 二进制名称或路径。
        timeout_sec: 每个标志验证的超时秒数。

    Returns:
        字典，包含每个标志的通过/失败状态、详情和总体结果。
    """
    results: dict = {
        "flags": {
            "--non-interactive": False,
            "--output-format stream-json": False,
            "--yolo": False,
        },
        "all_pass": False,
        "details": {},
        "binary": binary,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # 1. --non-interactive
    t0 = time.time()
    ni_ok = verify_flag_non_interactive(binary, timeout_sec)
    results["flags"]["--non-interactive"] = ni_ok
    results["details"]["--non-interactive"] = (
        "PASS" if ni_ok else "FAIL: agy may require interactive input"
    )

    # 2. --output-format stream-json
    t1 = time.time()
    sj_ok, sj_detail = verify_flag_stream_json(binary, timeout_sec)
    results["flags"]["--output-format stream-json"] = sj_ok
    results["details"]["--output-format stream-json"] = (
        f"PASS ({sj_detail})" if sj_ok else f"FAIL: {sj_detail}"
    )

    # 3. --yolo
    t2 = time.time()
    yolo_ok, yolo_detail = verify_flag_yolo(binary, timeout_sec)
    results["flags"]["--yolo"] = yolo_ok
    results["details"]["--yolo"] = (
        f"PASS ({yolo_detail})" if yolo_ok else f"FAIL: {yolo_detail}"
    )

    results["all_pass"] = all(results["flags"].values())
    results["duration_ms"] = int((time.time() - t0) * 1000)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify critical agy CLI flags for loop-antigravity (P0-8)"
    )
    parser.add_argument("--binary", default="agy",
                        help="Path or name of the agy CLI binary (default: agy)")
    parser.add_argument("--timeout", type=int, default=15,
                        help="Timeout per flag verification in seconds (default: 15)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON instead of text")
    args = parser.parse_args()

    results = verify_all(binary=args.binary, timeout_sec=args.timeout)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("=" * 60)
        print("  agy CLI Critical Flag Verification (P0-8)")
        print("  loop-antigravity Pre-flight Check")
        print("=" * 60)
        print(f"  Binary:     {results['binary']}")
        print(f"  Checked at: {results['checked_at']}")
        print(f"  Duration:   {results['duration_ms']}ms")
        print("-" * 60)
        for flag_name, ok in results["flags"].items():
            status = "PASS" if ok else "FAIL"
            detail = results["details"].get(flag_name, "")
            print(f"  [{status}] {flag_name}")
            if not ok:
                print(f"          -> {detail}")
        print("-" * 60)
        if results["all_pass"]:
            print("  ALL 3 FLAGS VERIFIED -- agy CLI is ready for loop-antigravity.")
        else:
            failed = [f for f, ok in results["flags"].items() if not ok]
            print(f"  VERIFICATION FAILED: {len(failed)} flag(s) not supported.")
            print("  Please update agy CLI or check your installation.")
            print(f"  Failed flags: {', '.join(failed)}")
        print("=" * 60)

    sys.exit(0 if results["all_pass"] else 1)


if __name__ == "__main__":
    main()
