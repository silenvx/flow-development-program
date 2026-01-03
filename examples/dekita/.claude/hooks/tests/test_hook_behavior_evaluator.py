#!/usr/bin/env python3
"""Tests for hook-behavior-evaluator.py."""

import importlib.util
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from lib.session import create_hook_context

# hook-behavior-evaluator.py has hyphens, so we need dynamic import
HOOK_PATH = Path(__file__).parent.parent / "hook-behavior-evaluator.py"
_spec = importlib.util.spec_from_file_location("hook_behavior_evaluator", HOOK_PATH)
hook_module = importlib.util.module_from_spec(_spec)
sys.modules["hook_behavior_evaluator"] = hook_module
_spec.loader.exec_module(hook_module)


class TestParseExpectedBlockRate:
    """Tests for parse_expected_block_rate function."""

    def test_range_format(self):
        """Test parsing range format like '5-10%'."""
        min_rate, max_rate = hook_module.parse_expected_block_rate("5-10%")
        assert min_rate == pytest.approx(0.05)
        assert max_rate == pytest.approx(0.10)

    def test_range_without_percent(self):
        """Test parsing range without percent sign."""
        min_rate, max_rate = hook_module.parse_expected_block_rate("10-30")
        assert min_rate == pytest.approx(0.10)
        assert max_rate == pytest.approx(0.30)

    def test_single_value(self):
        """Test parsing single value with margin."""
        min_rate, max_rate = hook_module.parse_expected_block_rate("10%")
        assert min_rate == pytest.approx(0.05)  # 10% - 5%
        assert max_rate == pytest.approx(0.15)  # 10% + 5%

    def test_empty_string(self):
        """Test empty string returns full range."""
        min_rate, max_rate = hook_module.parse_expected_block_rate("")
        assert min_rate == 0.0
        assert max_rate == 1.0

    def test_invalid_format(self):
        """Test invalid format returns full range."""
        min_rate, max_rate = hook_module.parse_expected_block_rate("invalid")
        assert min_rate == 0.0
        assert max_rate == 1.0

    def test_zero_percent(self):
        """Test 0% rate."""
        min_rate, max_rate = hook_module.parse_expected_block_rate("0%")
        assert min_rate == 0.0
        assert max_rate == pytest.approx(0.05)

    def test_decimal_values(self):
        """Test decimal values like '1.5-2.5%'."""
        min_rate, max_rate = hook_module.parse_expected_block_rate("1.5-2.5%")
        assert min_rate == pytest.approx(0.015)
        assert max_rate == pytest.approx(0.025)


class TestLoadSessionLogs:
    """Tests for load_session_logs function."""

    def test_empty_log_entries(self):
        """Test with no log entries."""
        with patch.object(hook_module, "read_all_session_log_entries", return_value=[]):
            logs = hook_module.load_session_logs()
            assert logs == []

    def test_loads_recent_logs(self):
        """Test loading recent logs within session window."""
        now = datetime.now(UTC)
        recent_ts = (now - timedelta(minutes=30)).isoformat()
        old_ts = (now - timedelta(minutes=120)).isoformat()

        mock_entries = [
            {"timestamp": recent_ts, "hook": "test-hook", "decision": "approve"},
            {"timestamp": old_ts, "hook": "old-hook", "decision": "block"},
        ]

        with patch.object(hook_module, "read_all_session_log_entries", return_value=mock_entries):
            logs = hook_module.load_session_logs(session_window_minutes=60)
            assert len(logs) == 1
            assert logs[0]["hook"] == "test-hook"

    def test_handles_invalid_timestamps(self):
        """Test handling of entries with invalid timestamps."""
        now = datetime.now(UTC)
        recent_ts = now.isoformat()

        mock_entries = [
            {"timestamp": recent_ts, "hook": "valid-hook", "decision": "approve"},
            {"timestamp": "invalid", "hook": "bad-timestamp", "decision": "block"},
            {"timestamp": recent_ts, "hook": "another-hook", "decision": "block"},
        ]

        with patch.object(hook_module, "read_all_session_log_entries", return_value=mock_entries):
            logs = hook_module.load_session_logs(session_window_minutes=60)
            assert len(logs) == 2


class TestDetectSilentFailures:
    """Tests for detect_silent_failures function."""

    def test_detects_error_in_reason(self):
        """Test detecting Error: pattern in reason."""
        logs = [
            {"hook": "test-hook", "decision": "approve", "reason": "Error: something failed"},
            {"hook": "test-hook", "decision": "approve", "reason": "Error: another failure"},
        ]
        issues = hook_module.detect_silent_failures(logs)
        assert len(issues) == 1
        assert issues[0]["type"] == "silent_failure"
        assert issues[0]["hook"] == "test-hook"
        assert issues[0]["count"] == 2

    def test_detects_exception_pattern(self):
        """Test detecting Exception pattern."""
        logs = [
            {"hook": "hook-a", "decision": "approve", "reason": "Exception occurred"},
        ]
        issues = hook_module.detect_silent_failures(logs)
        assert len(issues) == 1

    def test_detects_timeout(self):
        """Test detecting timeout pattern."""
        logs = [
            {"hook": "hook-a", "decision": "approve", "reason": "timeout waiting for response"},
        ]
        issues = hook_module.detect_silent_failures(logs)
        assert len(issues) == 1

    def test_ignores_normal_reasons(self):
        """Test that normal reasons are not flagged."""
        logs = [
            {"hook": "hook-a", "decision": "approve", "reason": "Not relevant"},
            {"hook": "hook-b", "decision": "block", "reason": "Branch is protected"},
        ]
        issues = hook_module.detect_silent_failures(logs)
        assert len(issues) == 0

    def test_excludes_self_hook(self):
        """Test that self hook is excluded."""
        logs = [
            {"hook": "hook-behavior-evaluator", "decision": "approve", "reason": "Error: test"},
        ]
        issues = hook_module.detect_silent_failures(logs)
        assert len(issues) == 0


class TestDetectBlockRateAnomalies:
    """Tests for detect_block_rate_anomalies function."""

    def test_detects_high_block_rate(self):
        """Test detecting block rate higher than expected."""
        now = datetime.now(UTC)
        logs = [
            {"hook": "test-hook", "decision": "block", "_parsed_timestamp": now} for _ in range(8)
        ] + [
            {"hook": "test-hook", "decision": "approve", "_parsed_timestamp": now} for _ in range(2)
        ]
        # 80% block rate, expected is 5-10%
        metadata = {
            "hooks": {
                "test-hook": {
                    "expected_block_rate": "5-10%",
                    "status": "active",
                }
            }
        }

        issues = hook_module.detect_block_rate_anomalies(logs, metadata)
        assert len(issues) == 1
        assert issues[0]["type"] == "block_rate_anomaly"
        assert issues[0]["actual_rate"] == 80.0
        assert "高い" in issues[0]["message"]

    def test_detects_low_block_rate(self):
        """Test detecting block rate lower than expected."""
        now = datetime.now(UTC)
        logs = [
            {"hook": "test-hook", "decision": "approve", "_parsed_timestamp": now}
            for _ in range(10)
        ]
        # 0% block rate, expected is 50-70%
        metadata = {
            "hooks": {
                "test-hook": {
                    "expected_block_rate": "50-70%",
                    "status": "active",
                }
            }
        }

        issues = hook_module.detect_block_rate_anomalies(logs, metadata)
        assert len(issues) == 1
        assert "低い" in issues[0]["message"]

    def test_ignores_within_expected_range(self):
        """Test that rates within expected range are not flagged."""
        now = datetime.now(UTC)
        logs = [
            {"hook": "test-hook", "decision": "block", "_parsed_timestamp": now} for _ in range(2)
        ] + [
            {"hook": "test-hook", "decision": "approve", "_parsed_timestamp": now} for _ in range(8)
        ]
        # 20% block rate, expected is 10-30%
        metadata = {
            "hooks": {
                "test-hook": {
                    "expected_block_rate": "10-30%",
                    "status": "active",
                }
            }
        }

        issues = hook_module.detect_block_rate_anomalies(logs, metadata)
        assert len(issues) == 0

    def test_ignores_hooks_without_metadata(self):
        """Test that hooks without metadata are ignored."""
        now = datetime.now(UTC)
        logs = [
            {"hook": "unknown-hook", "decision": "block", "_parsed_timestamp": now}
            for _ in range(10)
        ]
        metadata = {"hooks": {}}

        issues = hook_module.detect_block_rate_anomalies(logs, metadata)
        assert len(issues) == 0

    def test_ignores_low_execution_count(self):
        """Test that hooks with few executions are ignored."""
        now = datetime.now(UTC)
        logs = [
            {"hook": "test-hook", "decision": "block", "_parsed_timestamp": now} for _ in range(3)
        ]
        # Only 3 executions, need MIN_EXECUTIONS_FOR_RATE_CHECK (5)
        metadata = {
            "hooks": {
                "test-hook": {
                    "expected_block_rate": "0-5%",
                    "status": "active",
                }
            }
        }

        issues = hook_module.detect_block_rate_anomalies(logs, metadata)
        assert len(issues) == 0


class TestDetectBlockLoops:
    """Tests for detect_block_loops function."""

    def test_detects_loop(self):
        """Test detecting block loop pattern."""
        now = datetime.now(UTC)
        # 6 blocks within 30 seconds
        logs = [
            {
                "hook": "test-hook",
                "decision": "block",
                "_parsed_timestamp": now + timedelta(seconds=i * 5),
            }
            for i in range(6)
        ]

        issues = hook_module.detect_block_loops(logs)
        assert len(issues) == 1
        assert issues[0]["type"] == "block_loop"
        assert issues[0]["hook"] == "test-hook"

    def test_ignores_spread_blocks(self):
        """Test that spread-out blocks are not flagged."""
        now = datetime.now(UTC)
        # 6 blocks but spread across 10 minutes (600 seconds)
        logs = [
            {
                "hook": "test-hook",
                "decision": "block",
                "_parsed_timestamp": now + timedelta(seconds=i * 100),
            }
            for i in range(6)
        ]

        issues = hook_module.detect_block_loops(logs)
        assert len(issues) == 0

    def test_ignores_approves(self):
        """Test that approve decisions don't count toward loop."""
        now = datetime.now(UTC)
        logs = [
            {
                "hook": "test-hook",
                "decision": "approve",
                "_parsed_timestamp": now + timedelta(seconds=i),
            }
            for i in range(10)
        ]

        issues = hook_module.detect_block_loops(logs)
        assert len(issues) == 0

    def test_ignores_below_threshold(self):
        """Test that blocks below threshold are not flagged."""
        now = datetime.now(UTC)
        # Only 4 blocks (threshold is 5)
        logs = [
            {
                "hook": "test-hook",
                "decision": "block",
                "_parsed_timestamp": now + timedelta(seconds=i),
            }
            for i in range(4)
        ]

        issues = hook_module.detect_block_loops(logs)
        assert len(issues) == 0


class TestDetectMissingHooks:
    """Tests for detect_missing_hooks function."""

    def test_detects_missing_pretooluse_hook(self):
        """Test detecting PreToolUse hook that never executed."""
        logs = [
            {"hook": "other-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "missing-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
                "other-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 1
        assert issues[0]["type"] == "missing_execution"
        assert issues[0]["hook"] == "missing-hook"

    def test_detects_missing_posttooluse_hook(self):
        """Test detecting PostToolUse hook that never executed."""
        # Need at least one PreToolUse/PostToolUse hook executed to trigger check
        logs = [
            {"hook": "other-pretool-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "post-hook": {
                    "status": "active",
                    "trigger": "PostToolUse:Bash",
                },
                "other-pretool-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 1
        assert issues[0]["hook"] == "post-hook"

    def test_ignores_stop_hooks(self):
        """Test that Stop hooks are not flagged as missing."""
        # Need at least one PreToolUse/PostToolUse hook executed to trigger check
        logs = [
            {"hook": "pretool-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "stop-hook": {
                    "status": "active",
                    "trigger": "Stop",
                },
                "pretool-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 0

    def test_ignores_session_start_hooks(self):
        """Test that SessionStart hooks are not flagged as missing.

        Note: This is implicit behavior - the check only targets PreToolUse/PostToolUse
        hooks, so SessionStart (and other non-tool hooks) are naturally excluded.
        This test documents that expected behavior.
        """
        # Need at least one PreToolUse/PostToolUse hook executed to trigger check
        logs = [
            {"hook": "pretool-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "session-hook": {
                    "status": "active",
                    "trigger": "SessionStart",
                },
                "pretool-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 0

    def test_ignores_inactive_hooks(self):
        """Test that inactive hooks are not flagged."""
        # Need at least one PreToolUse/PostToolUse hook executed to trigger check
        logs = [
            {"hook": "active-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "disabled-hook": {
                    "status": "disabled",
                    "trigger": "PreToolUse:Bash",
                },
                "active-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 0

    def test_ignores_executed_hooks(self):
        """Test that executed hooks are not flagged."""
        logs = [
            {"hook": "test-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "test-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 0

    def test_skip_missing_check_flag(self):
        """Test that hooks with skip_missing_check=true are not flagged."""
        # Need at least one PreToolUse/PostToolUse hook executed to trigger check
        logs = [
            {"hook": "executed-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "skipped-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                    "skip_missing_check": True,
                },
                "regular-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
                "executed-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 1
        assert issues[0]["hook"] == "regular-hook"

    def test_skip_missing_check_false(self):
        """Test that hooks with skip_missing_check=false are still flagged."""
        # Need at least one PreToolUse/PostToolUse hook executed to trigger check
        logs = [
            {"hook": "executed-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "test-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                    "skip_missing_check": False,
                },
                "executed-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 1

    def test_excludes_self_hook(self):
        """Test that hook-behavior-evaluator itself is excluded."""
        # Need at least one PreToolUse/PostToolUse hook executed to trigger check
        logs = [
            {"hook": "other-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "hook-behavior-evaluator": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
                "other-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 0

    def test_skips_when_no_tool_usage(self):
        """Test that check is skipped when no PreToolUse/PostToolUse hooks executed.

        This prevents false positives for sessions that only have SessionStart
        or Stop hooks executed (Issue #659).
        """
        # Only SessionStart hook executed - no tool usage
        logs = [
            {"hook": "session-start-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "session-start-hook": {
                    "status": "active",
                    "trigger": "SessionStart",
                },
                "pretool-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        # Should return empty list because no tool usage occurred
        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 0

    def test_skips_when_only_stop_hooks_executed(self):
        """Test that check is skipped when only Stop hooks are in logs."""
        logs = [
            {"hook": "stop-hook", "decision": "approve"},
        ]
        metadata = {
            "hooks": {
                "stop-hook": {
                    "status": "active",
                    "trigger": "Stop",
                },
                "pretool-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        # Should return empty list because no tool usage occurred
        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 0

    def test_skips_when_empty_logs(self):
        """Test that check is skipped when logs are empty."""
        logs = []
        metadata = {
            "hooks": {
                "pretool-hook": {
                    "status": "active",
                    "trigger": "PreToolUse:Bash",
                },
            }
        }

        # Should return empty list because no tool usage occurred
        issues = hook_module.detect_missing_hooks(logs, metadata)
        assert len(issues) == 0


class TestFormatReport:
    """Tests for format_report function."""

    def test_empty_issues(self):
        """Test that empty issues returns empty string."""
        report = hook_module.format_report([], 100)
        assert report == ""

    def test_formats_single_issue(self):
        """Test formatting single issue."""
        issues = [
            {
                "type": "silent_failure",
                "hook": "test-hook",
                "message": "test-hook で 2 件のエラーが発生",
            }
        ]
        report = hook_module.format_report(issues, 50)
        assert "Hook 動作評価レポート" in report
        assert "エラー検出" in report
        assert "test-hook" in report

    def test_formats_multiple_issue_types(self):
        """Test formatting multiple issue types."""
        issues = [
            {
                "type": "silent_failure",
                "hook": "hook-a",
                "message": "hook-a error",
            },
            {
                "type": "block_rate_anomaly",
                "hook": "hook-b",
                "message": "hook-b rate issue",
            },
        ]
        report = hook_module.format_report(issues, 100)
        assert "エラー検出" in report
        assert "Block 率異常" in report


class TestMain:
    """Tests for main function."""

    def test_main_no_logs(self):
        """Test main with no logs."""
        import io

        with patch.object(hook_module, "read_all_session_log_entries", return_value=[]):
            with patch("sys.stdin.read", return_value="{}"):
                with patch.object(hook_module, "log_hook_execution"):
                    captured = io.StringIO()
                    with patch("sys.stdout", captured):
                        hook_module.main()
                    output = captured.getvalue()
                    result = json.loads(output)
                    assert result["decision"] == "approve"

    def test_main_with_stop_hook_active(self):
        """Test main skips when stop_hook_active is set."""
        with patch("sys.stdin.read", return_value='{"stop_hook_active": true}'):
            import io

            captured = io.StringIO()
            with patch("sys.stdout", captured):
                hook_module.main()
            output = captured.getvalue()
            result = json.loads(output)
            # Issue #1607: Now uses print_continue_and_log_skip which returns {"continue": True}
            assert result.get("continue") is True or result.get("decision") == "approve"
            assert "systemMessage" not in result


class TestLogBehaviorAnomalies:
    """Tests for log_behavior_anomalies function (Issue #1317).

    Issue #1840: Updated to use session-specific log files.
    """

    TEST_SESSION_ID = "test-session-12345678"

    def _get_session_log_file(self, tmp_path: Path) -> Path:
        """Get the session-specific log file path."""
        return tmp_path / f"behavior-anomalies-{self.TEST_SESSION_ID}.jsonl"

    def test_logs_silent_failure(self):
        """Test logging silent failure issues."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(hook_module, "METRICS_LOG_DIR", tmp_path):
                ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
                issues = [
                    {
                        "type": "silent_failure",
                        "hook": "test-hook",
                        "count": 3,
                        "examples": ["Error: test1", "Error: test2"],
                    }
                ]
                hook_module.log_behavior_anomalies(ctx, issues, 100)

                log_file = self._get_session_log_file(tmp_path)
                assert log_file.exists()
                entry = json.loads(log_file.read_text().strip())
                assert entry["type"] == "silent_failure"
                assert entry["hook"] == "test-hook"
                assert entry["count"] == 3
                assert len(entry["examples"]) == 2

    def test_logs_block_rate_anomaly(self):
        """Test logging block rate anomaly issues."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(hook_module, "METRICS_LOG_DIR", tmp_path):
                ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
                issues = [
                    {
                        "type": "block_rate_anomaly",
                        "hook": "rate-hook",
                        "actual_rate": 85.5,
                        "expected_range": "5-10%",
                        "total": 100,
                        "block_count": 85,
                    }
                ]
                hook_module.log_behavior_anomalies(ctx, issues, 500)

                log_file = self._get_session_log_file(tmp_path)
                entry = json.loads(log_file.read_text().strip())
                assert entry["type"] == "block_rate_anomaly"
                assert entry["actual_rate"] == 85.5
                assert entry["expected_range"] == "5-10%"
                assert entry["total"] == 100
                assert entry["block_count"] == 85

    def test_logs_missing_execution(self):
        """Test logging missing execution issues."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(hook_module, "METRICS_LOG_DIR", tmp_path):
                ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
                issues = [
                    {
                        "type": "missing_execution",
                        "hook": "missing-hook",
                        "trigger": "PreToolUse:Bash",
                    }
                ]
                hook_module.log_behavior_anomalies(ctx, issues, 200)

                log_file = self._get_session_log_file(tmp_path)
                entry = json.loads(log_file.read_text().strip())
                assert entry["type"] == "missing_execution"
                assert entry["trigger"] == "PreToolUse:Bash"

    def test_logs_block_loop(self):
        """Test logging block loop issues."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(hook_module, "METRICS_LOG_DIR", tmp_path):
                ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
                issues = [
                    {
                        "type": "block_loop",
                        "hook": "loop-hook",
                        "count": 7,
                        "window_seconds": 60,
                    }
                ]
                hook_module.log_behavior_anomalies(ctx, issues, 300)

                log_file = self._get_session_log_file(tmp_path)
                entry = json.loads(log_file.read_text().strip())
                assert entry["type"] == "block_loop"
                assert entry["count"] == 7
                assert entry["window_seconds"] == 60

    def test_empty_issues_no_log(self):
        """Test that empty issues list creates no log."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(hook_module, "METRICS_LOG_DIR", tmp_path):
                ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
                hook_module.log_behavior_anomalies(ctx, [], 100)

                log_file = self._get_session_log_file(tmp_path)
                assert not log_file.exists()

    def test_multiple_issues_logged(self):
        """Test that multiple issues are logged as separate entries."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(hook_module, "METRICS_LOG_DIR", tmp_path):
                ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
                issues = [
                    {"type": "silent_failure", "hook": "hook-a", "count": 1, "examples": []},
                    {"type": "block_rate_anomaly", "hook": "hook-b", "actual_rate": 50},
                ]
                hook_module.log_behavior_anomalies(ctx, issues, 100)

                log_file = self._get_session_log_file(tmp_path)
                lines = log_file.read_text().strip().split("\n")
                assert len(lines) == 2
                assert json.loads(lines[0])["type"] == "silent_failure"
                assert json.loads(lines[1])["type"] == "block_rate_anomaly"

    def test_common_fields_present(self):
        """Test that timestamp and analyzed_logs are included in all entries."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(hook_module, "METRICS_LOG_DIR", tmp_path):
                ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
                issues = [
                    {"type": "silent_failure", "hook": "test-hook", "count": 1, "examples": []}
                ]
                hook_module.log_behavior_anomalies(ctx, issues, 42)

                log_file = self._get_session_log_file(tmp_path)
                entry = json.loads(log_file.read_text().strip())
                assert "timestamp" in entry
                assert isinstance(entry["timestamp"], str)
                assert len(entry["timestamp"]) > 0
                assert entry["analyzed_logs"] == 42

    def test_logs_all_issues_without_truncation(self):
        """Test that all issues are logged without MAX_ISSUES_TO_REPORT truncation."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(hook_module, "METRICS_LOG_DIR", tmp_path):
                ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
                # Create more issues than MAX_ISSUES_TO_REPORT (which is 10)
                issues = [
                    {"type": "silent_failure", "hook": f"hook-{i}", "count": i, "examples": []}
                    for i in range(15)
                ]
                hook_module.log_behavior_anomalies(ctx, issues, 100)

                log_file = self._get_session_log_file(tmp_path)
                lines = log_file.read_text().strip().split("\n")
                assert len(lines) == 15  # All issues should be logged

    def test_examples_truncated_to_three(self):
        """Test that silent_failure examples are truncated to 3 entries."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(hook_module, "METRICS_LOG_DIR", tmp_path):
                ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
                issues = [
                    {
                        "type": "silent_failure",
                        "hook": "test-hook",
                        "count": 5,
                        "examples": ["Error 1", "Error 2", "Error 3", "Error 4", "Error 5"],
                    }
                ]
                hook_module.log_behavior_anomalies(ctx, issues, 100)

                log_file = self._get_session_log_file(tmp_path)
                entry = json.loads(log_file.read_text().strip())
                assert len(entry["examples"]) == 3
                assert entry["examples"] == ["Error 1", "Error 2", "Error 3"]
