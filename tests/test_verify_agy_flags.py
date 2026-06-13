"""Tests for verify_agy_flags.py -- agy CLI critical flag verification."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from loop_antigravity.verify_agy_flags import (
    main,
    verify_all,
    verify_flag_non_interactive,
    verify_flag_stream_json,
    verify_flag_yolo,
)


# ============================================================================
# verify_flag_non_interactive tests
# ============================================================================


class TestVerifyFlagNonInteractive:
    """Tests for verify_flag_non_interactive()."""

    def test_success(self) -> None:
        """Should return True when subprocess succeeds with output."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = "some output"

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = verify_flag_non_interactive(binary="test-agy", timeout_sec=5)
            assert result is True
            mock_run.assert_called_once()

    def test_failure_bad_returncode(self) -> None:
        """Should return False when subprocess has non-zero returncode."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout.strip.return_value = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = verify_flag_non_interactive(binary="test-agy")
            assert result is False

    def test_failure_empty_output(self) -> None:
        """Should return False when stdout is empty."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = verify_flag_non_interactive(binary="test-agy")
            assert result is False

    def test_failure_exception(self) -> None:
        """Should return False on subprocess exception."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(
                cmd=["test-agy"], timeout=5)):
            result = verify_flag_non_interactive(binary="test-agy")
            assert result is False

    def test_uses_correct_flags(self) -> None:
        """Should pass correct CLI flags to subprocess."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = "pong"

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            verify_flag_non_interactive(binary="my-agy", timeout_sec=10)
            call_args = mock_run.call_args[0][0]
            assert "--non-interactive" in call_args
            assert "--yolo" in call_args
            assert "--output-format" in call_args
            assert "stream-json" in call_args
            assert call_args[0] == "my-agy"


# ============================================================================
# verify_flag_stream_json tests
# ============================================================================


class TestVerifyFlagStreamJson:
    """Tests for verify_flag_stream_json()."""

    def test_success_with_valid_json_lines(self) -> None:
        """Should return (True, detail) when >=2 lines parse as JSON."""
        lines = [
            json.dumps({"type": "text", "content": "ok"}),
            json.dumps({"type": "text", "content": "yes"}),
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = "\n".join(lines)

        with patch("subprocess.run", return_value=mock_proc):
            ok, detail = verify_flag_stream_json(binary="test-agy")
            assert ok is True
            assert "JSON lines parsed" in detail
            assert "2" in detail

    def test_failure_insufficient_json(self) -> None:
        """Should return False when fewer than 2 JSON lines."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = json.dumps({"type": "text", "content": "only one"})

        with patch("subprocess.run", return_value=mock_proc):
            ok, detail = verify_flag_stream_json(binary="test-agy")
            assert ok is False

    def test_failure_nonzero_exit(self) -> None:
        """Should return False on non-zero exit code."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout.strip.return_value = ""

        with patch("subprocess.run", return_value=mock_proc):
            ok, detail = verify_flag_stream_json(binary="test-agy")
            assert ok is False
            assert "exit=1" in detail

    def test_failure_all_lines_malformed(self) -> None:
        """Should return False when no line parses as valid JSON."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = "not json\nstill not json"

        with patch("subprocess.run", return_value=mock_proc):
            ok, detail = verify_flag_stream_json(binary="test-agy")
            assert ok is False

    def test_mixed_valid_invalid_lines(self) -> None:
        """Should count only valid JSON lines."""
        lines = [
            json.dumps({"type": "text", "content": "ok"}),
            "garbage line",
            json.dumps({"type": "text", "content": "yes"}),
            "more garbage",
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = "\n".join(lines)

        with patch("subprocess.run", return_value=mock_proc):
            ok, detail = verify_flag_stream_json(binary="test-agy")
            assert ok is True  # 2 valid JSON lines >= 2

    def test_exception_handling(self) -> None:
        """Should return (False, str) on exception."""
        with patch("subprocess.run", side_effect=OSError("file not found")):
            ok, detail = verify_flag_stream_json(binary="test-agy")
            assert ok is False
            assert "file not found" in detail


# ============================================================================
# verify_flag_yolo tests
# ============================================================================


class TestVerifyFlagYolo:
    """Tests for verify_flag_yolo()."""

    def test_success_with_text_output(self) -> None:
        """Should return (True, detail) when text is found in stream-json."""
        lines = [
            json.dumps({"type": "text", "content": "hello yolo world"}),
            json.dumps({"type": "result", "content": "done"}),
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = "\n".join(lines)

        with patch("subprocess.run", return_value=mock_proc):
            ok, detail = verify_flag_yolo(binary="test-agy")
            assert ok is True
            assert "YOLO mode accepted" in detail

    def test_success_with_text_in_later_line(self) -> None:
        """Should find text even if it's not in the first line."""
        lines = [
            json.dumps({"type": "status", "content": "thinking"}),
            json.dumps({"type": "text", "content": "hello yolo world"}),
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = "\n".join(lines)

        with patch("subprocess.run", return_value=mock_proc):
            ok, detail = verify_flag_yolo(binary="test-agy")
            assert ok is True

    def test_failure_no_text_content(self) -> None:
        """Should return False when no 'text' type with content is found."""
        lines = [
            json.dumps({"type": "status", "content": ""}),
            json.dumps({"type": "result", "content": ""}),
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = "\n".join(lines)

        with patch("subprocess.run", return_value=mock_proc):
            ok, detail = verify_flag_yolo(binary="test-agy")
            assert ok is False

    def test_failure_nonzero_exit(self) -> None:
        """Should return False on non-zero exit code."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout.strip.return_value = ""

        with patch("subprocess.run", return_value=mock_proc):
            ok, detail = verify_flag_yolo(binary="test-agy")
            assert ok is False
            assert "exit=1" in detail

    def test_failure_timeout(self) -> None:
        """Should return False on timeout (interactive prompt suspected)."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(
                cmd=["test-agy"], timeout=5)):
            ok, detail = verify_flag_yolo(binary="test-agy")
            assert ok is False
            assert "Timed out" in detail

    def test_failure_generic_exception(self) -> None:
        """Should return (False, str) on generic exception."""
        with patch("subprocess.run", side_effect=ValueError("bad arg")):
            ok, detail = verify_flag_yolo(binary="test-agy")
            assert ok is False
            assert "bad arg" in detail

    def test_empty_text_content_ignored(self) -> None:
        """Should ignore text entries with empty content."""
        lines = [
            json.dumps({"type": "text", "content": ""}),
            json.dumps({"type": "text", "content": "   "}),
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout.strip.return_value = "\n".join(lines)

        with patch("subprocess.run", return_value=mock_proc):
            ok, detail = verify_flag_yolo(binary="test-agy")
            assert ok is False  # all content is whitespace/empty


# ============================================================================
# verify_all tests
# ============================================================================


class TestVerifyAll:
    """Tests for verify_all() orchestration function."""

    def test_all_pass(self) -> None:
        """Should return all_pass=True when all three flags pass."""
        with patch(
            "loop_antigravity.verify_agy_flags.verify_flag_non_interactive",
            return_value=True,
        ), patch(
            "loop_antigravity.verify_agy_flags.verify_flag_stream_json",
            return_value=(True, "2 JSON lines parsed out of 2"),
        ), patch(
            "loop_antigravity.verify_agy_flags.verify_flag_yolo",
            return_value=(True, "YOLO mode accepted"),
        ):
            results = verify_all(binary="test-agy", timeout_sec=10)
            assert results["all_pass"] is True
            assert results["binary"] == "test-agy"
            assert results["flags"]["--non-interactive"] is True
            assert results["flags"]["--output-format stream-json"] is True
            assert results["flags"]["--yolo"] is True
            assert "checked_at" in results
            assert "duration_ms" in results

    def test_some_fail(self) -> None:
        """Should return all_pass=False when any flag fails."""
        with patch(
            "loop_antigravity.verify_agy_flags.verify_flag_non_interactive",
            return_value=False,
        ), patch(
            "loop_antigravity.verify_agy_flags.verify_flag_stream_json",
            return_value=(True, "2 JSON lines"),
        ), patch(
            "loop_antigravity.verify_agy_flags.verify_flag_yolo",
            return_value=(True, "YOLO mode accepted"),
        ):
            results = verify_all()
            assert results["all_pass"] is False
            assert results["flags"]["--non-interactive"] is False
            assert results["flags"]["--output-format stream-json"] is True

    def test_all_fail(self) -> None:
        """Should return all_pass=False when all flags fail."""
        with patch(
            "loop_antigravity.verify_agy_flags.verify_flag_non_interactive",
            return_value=False,
        ), patch(
            "loop_antigravity.verify_agy_flags.verify_flag_stream_json",
            return_value=(False, "error"),
        ), patch(
            "loop_antigravity.verify_agy_flags.verify_flag_yolo",
            return_value=(False, "timeout"),
        ):
            results = verify_all()
            assert results["all_pass"] is False
            assert not any(results["flags"].values())

    def test_details_contain_messages(self) -> None:
        """Should include human-readable details for each flag."""
        with patch(
            "loop_antigravity.verify_agy_flags.verify_flag_non_interactive",
            return_value=True,
        ), patch(
            "loop_antigravity.verify_agy_flags.verify_flag_stream_json",
            return_value=(True, "2 JSON lines parsed out of 2"),
        ), patch(
            "loop_antigravity.verify_agy_flags.verify_flag_yolo",
            return_value=(False, "exit=1"),
        ):
            results = verify_all()
            assert "PASS" in results["details"]["--non-interactive"]
            assert "PASS" in results["details"]["--output-format stream-json"]
            assert "FAIL" in results["details"]["--yolo"]

    def test_binary_passed_to_sub_verifications(self) -> None:
        """Should forward binary path to each flag verifier."""
        with patch(
            "loop_antigravity.verify_agy_flags.verify_flag_non_interactive",
        ) as mock_ni, patch(
            "loop_antigravity.verify_agy_flags.verify_flag_stream_json",
        ) as mock_sj, patch(
            "loop_antigravity.verify_agy_flags.verify_flag_yolo",
        ) as mock_yolo:
            mock_ni.return_value = True
            mock_sj.return_value = (True, "ok")
            mock_yolo.return_value = (True, "ok")

            verify_all(binary="/usr/local/bin/agy", timeout_sec=20)
            mock_ni.assert_called_once_with("/usr/local/bin/agy", 20)
            mock_sj.assert_called_once_with("/usr/local/bin/agy", 20)
            mock_yolo.assert_called_once_with("/usr/local/bin/agy", 20)


# ============================================================================
# main() CLI entry point tests
# ============================================================================


class TestMainFunction:
    """Tests for the main() CLI entry point."""

    def test_main_all_pass_text_output(self, capsys) -> None:
        """Should print text output and exit 0 on all pass."""
        with patch(
            "loop_antigravity.verify_agy_flags.verify_all",
            return_value={
                "flags": {
                    "--non-interactive": True,
                    "--output-format stream-json": True,
                    "--yolo": True,
                },
                "all_pass": True,
                "details": {
                    "--non-interactive": "PASS",
                    "--output-format stream-json": "PASS (2 JSON lines)",
                    "--yolo": "PASS (YOLO mode accepted)",
                },
                "binary": "agy",
                "checked_at": "2026-06-13T00:00:00Z",
                "duration_ms": 123,
            },
        ), patch("sys.argv", ["verify_agy_flags.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "ALL 3 FLAGS VERIFIED" in captured.out

    def test_main_some_fail_text_output(self, capsys) -> None:
        """Should print failure message and exit 1 on failure."""
        with patch(
            "loop_antigravity.verify_agy_flags.verify_all",
            return_value={
                "flags": {
                    "--non-interactive": False,
                    "--output-format stream-json": True,
                    "--yolo": True,
                },
                "all_pass": False,
                "details": {
                    "--non-interactive": "FAIL: requires interactive input",
                    "--output-format stream-json": "PASS (2 JSON lines)",
                    "--yolo": "PASS (YOLO mode accepted)",
                },
                "binary": "agy",
                "checked_at": "2026-06-13T00:00:00Z",
                "duration_ms": 456,
            },
        ), patch("sys.argv", ["verify_agy_flags.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "VERIFICATION FAILED" in captured.out

    def test_main_json_output(self, capsys) -> None:
        """Should output JSON when --json flag is passed."""
        with patch(
            "loop_antigravity.verify_agy_flags.verify_all",
            return_value={
                "flags": {
                    "--non-interactive": True,
                    "--output-format stream-json": True,
                    "--yolo": True,
                },
                "all_pass": True,
                "details": {},
                "binary": "agy",
                "checked_at": "2026-06-13T00:00:00Z",
                "duration_ms": 0,
            },
        ), patch("sys.argv", ["verify_agy_flags.py", "--json"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["all_pass"] is True
        assert parsed["binary"] == "agy"

    def test_main_custom_binary_and_timeout(self) -> None:
        """Should forward --binary and --timeout arguments."""
        with patch(
            "loop_antigravity.verify_agy_flags.verify_all",
        ) as mock_verify_all, patch(
            "sys.argv",
            ["verify_agy_flags.py", "--binary", "/opt/agy", "--timeout", "30", "--json"],
        ):
            mock_verify_all.return_value = {
                "flags": {},
                "all_pass": True,
                "details": {},
                "binary": "/opt/agy",
                "checked_at": "",
                "duration_ms": 0,
            }
            with pytest.raises(SystemExit):
                main()
            mock_verify_all.assert_called_once_with(binary="/opt/agy", timeout_sec=30)
