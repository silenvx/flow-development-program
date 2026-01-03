#!/usr/bin/env python3
"""Tests for session-issue-integrity-check.py."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from conftest import load_hook_module
from lib.session import create_hook_context

# Load the hook module
session_issue_integrity_check = load_hook_module("session-issue-integrity-check")


class TestGetIssuesFromExecutionLog:
    """Tests for get_issues_from_execution_log function."""

    def test_no_log_file(self, tmp_path: Path) -> None:
        """Returns empty set when log file doesn't exist."""
        with patch.object(session_issue_integrity_check, "_get_log_dir", return_value=tmp_path):
            result = session_issue_integrity_check.get_issues_from_execution_log("test-session-id")
            assert result == set()

    def test_parses_p2_issue(self, tmp_path: Path) -> None:
        """Extracts P2 issue numbers from log."""
        log_dir = tmp_path / "execution"
        log_dir.mkdir(parents=True)
        # Use session-specific log file format
        log_file = log_dir / "hook-execution-test-session.jsonl"
        log_file.write_text(
            json.dumps(
                {
                    "session_id": "test-session",
                    "hook": "issue-creation-tracker",
                    "decision": "approve",
                    "reason": "Recorded P2 issue #1234 - implement after current task",
                }
            )
            + "\n"
        )

        with patch.object(session_issue_integrity_check, "_get_log_dir", return_value=log_dir):
            result = session_issue_integrity_check.get_issues_from_execution_log("test-session")
            assert result == {1234}

    def test_parses_issue_without_priority(self, tmp_path: Path) -> None:
        """Extracts issue numbers without priority label."""
        log_dir = tmp_path / "execution"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "hook-execution-test-session.jsonl"
        log_file.write_text(
            json.dumps(
                {
                    "session_id": "test-session",
                    "hook": "issue-creation-tracker",
                    "decision": "approve",
                    "reason": "Recorded issue #5678 - no priority set",
                }
            )
            + "\n"
        )

        with patch.object(session_issue_integrity_check, "_get_log_dir", return_value=log_dir):
            result = session_issue_integrity_check.get_issues_from_execution_log("test-session")
            assert result == {5678}

    def test_parses_p0_issue(self, tmp_path: Path) -> None:
        """Extracts P0 issue numbers from log."""
        log_dir = tmp_path / "execution"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "hook-execution-test-session.jsonl"
        log_file.write_text(
            json.dumps(
                {
                    "session_id": "test-session",
                    "hook": "issue-creation-tracker",
                    "decision": "approve",
                    "reason": "Recorded P0 issue #9999 - critical fix",
                }
            )
            + "\n"
        )

        with patch.object(session_issue_integrity_check, "_get_log_dir", return_value=log_dir):
            result = session_issue_integrity_check.get_issues_from_execution_log("test-session")
            assert result == {9999}

    def test_parses_p1_issue(self, tmp_path: Path) -> None:
        """Extracts P1 issue numbers from log."""
        log_dir = tmp_path / "execution"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "hook-execution-test-session.jsonl"
        log_file.write_text(
            json.dumps(
                {
                    "session_id": "test-session",
                    "hook": "issue-creation-tracker",
                    "decision": "approve",
                    "reason": "Recorded P1 issue #8888 - important bug",
                }
            )
            + "\n"
        )

        with patch.object(session_issue_integrity_check, "_get_log_dir", return_value=log_dir):
            result = session_issue_integrity_check.get_issues_from_execution_log("test-session")
            assert result == {8888}

    def test_parses_multiple_issues_same_session(self, tmp_path: Path) -> None:
        """Extracts multiple issue numbers from same session."""
        log_dir = tmp_path / "execution"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "hook-execution-test-session.jsonl"
        entries = [
            {
                "session_id": "test-session",
                "hook": "issue-creation-tracker",
                "decision": "approve",
                "reason": "Recorded P1 issue #1111 - first issue",
            },
            {
                "session_id": "test-session",
                "hook": "issue-creation-tracker",
                "decision": "approve",
                "reason": "Recorded P2 issue #2222 - second issue",
            },
            {
                "session_id": "test-session",
                "hook": "issue-creation-tracker",
                "decision": "approve",
                "reason": "Recorded issue #3333 - third issue",
            },
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        with patch.object(session_issue_integrity_check, "_get_log_dir", return_value=log_dir):
            result = session_issue_integrity_check.get_issues_from_execution_log("test-session")
            assert result == {1111, 2222, 3333}

    def test_handles_invalid_json(self, tmp_path: Path) -> None:
        """Skips invalid JSON lines gracefully."""
        log_dir = tmp_path / "execution"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "hook-execution-test-session.jsonl"
        log_file.write_text(
            "invalid json line\n"
            + json.dumps(
                {
                    "session_id": "test-session",
                    "hook": "issue-creation-tracker",
                    "decision": "approve",
                    "reason": "Recorded P2 issue #1234 - valid entry",
                }
            )
            + "\n"
        )

        with patch.object(session_issue_integrity_check, "_get_log_dir", return_value=log_dir):
            result = session_issue_integrity_check.get_issues_from_execution_log("test-session")
            assert result == {1234}


class TestGetIssuesFromSessionFile:
    """Tests for get_issues_from_session_file function."""

    def test_no_session_file(self, tmp_path: Path) -> None:
        """Returns empty set when session file doesn't exist."""
        with patch.object(session_issue_integrity_check, "_get_flow_dir", return_value=tmp_path):
            result = session_issue_integrity_check.get_issues_from_session_file("test-session")
            assert result == set()

    def test_parses_session_file(self, tmp_path: Path) -> None:
        """Parses issues from session file."""
        session_file = tmp_path / "session-created-issues-test-session.json"
        session_file.write_text(json.dumps({"issues": [1234, 5678]}))

        with patch.object(session_issue_integrity_check, "_get_flow_dir", return_value=tmp_path):
            result = session_issue_integrity_check.get_issues_from_session_file("test-session")
            assert result == {1234, 5678}

    def test_handles_invalid_json_in_session_file(self, tmp_path: Path) -> None:
        """Returns empty set when session file contains invalid JSON."""
        session_file = tmp_path / "session-created-issues-test-session.json"
        session_file.write_text("invalid json content")

        with patch.object(session_issue_integrity_check, "_get_flow_dir", return_value=tmp_path):
            result = session_issue_integrity_check.get_issues_from_session_file("test-session")
            assert result == set()

    def test_handles_empty_issues_array(self, tmp_path: Path) -> None:
        """Returns empty set when issues array is empty."""
        session_file = tmp_path / "session-created-issues-test-session.json"
        session_file.write_text(json.dumps({"issues": []}))

        with patch.object(session_issue_integrity_check, "_get_flow_dir", return_value=tmp_path):
            result = session_issue_integrity_check.get_issues_from_session_file("test-session")
            assert result == set()

    def test_handles_missing_issues_key(self, tmp_path: Path) -> None:
        """Returns empty set when issues key is missing."""
        session_file = tmp_path / "session-created-issues-test-session.json"
        session_file.write_text(json.dumps({"other_key": "value"}))

        with patch.object(session_issue_integrity_check, "_get_flow_dir", return_value=tmp_path):
            result = session_issue_integrity_check.get_issues_from_session_file("test-session")
            assert result == set()


class TestGetRecentSessionIds:
    """Tests for get_recent_session_ids function."""

    def test_no_flow_dir(self, tmp_path: Path) -> None:
        """Returns empty list when flow directory doesn't exist."""
        non_existent = tmp_path / "non_existent"
        with patch.object(
            session_issue_integrity_check, "_get_flow_dir", return_value=non_existent
        ):
            result = session_issue_integrity_check.get_recent_session_ids()
            assert result == []

    def test_returns_session_ids(self, tmp_path: Path) -> None:
        """Returns session IDs extracted from state files."""
        # Create state files
        (tmp_path / "state-session-abc.json").write_text("{}")
        (tmp_path / "state-session-xyz.json").write_text("{}")

        with patch.object(session_issue_integrity_check, "_get_flow_dir", return_value=tmp_path):
            result = session_issue_integrity_check.get_recent_session_ids()
            assert len(result) == 2
            assert "session-abc" in result
            assert "session-xyz" in result

    def test_respects_limit(self, tmp_path: Path) -> None:
        """Respects the limit parameter."""
        # Create multiple state files
        import time

        for i in range(5):
            state_file = tmp_path / f"state-session-{i}.json"
            state_file.write_text("{}")
            # Touch with different mtime to ensure ordering
            time.sleep(0.01)

        with patch.object(session_issue_integrity_check, "_get_flow_dir", return_value=tmp_path):
            result = session_issue_integrity_check.get_recent_session_ids(limit=2)
            assert len(result) == 2


class TestVerifyIntegrity:
    """Tests for verify_integrity function."""

    TEST_SESSION_ID = "current-session"

    def test_no_sessions(self, tmp_path: Path) -> None:
        """Returns no warnings when no recent sessions."""
        ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
        with patch.object(
            session_issue_integrity_check,
            "get_recent_session_ids",
            return_value=[],
        ):
            result = session_issue_integrity_check.verify_integrity(ctx)
            assert result == []

    def test_detects_missing_issues(self, tmp_path: Path) -> None:
        """Detects issues in log but missing from file."""
        ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
        with (
            patch.object(
                session_issue_integrity_check,
                "get_recent_session_ids",
                return_value=["old-session"],
            ),
            patch.object(
                session_issue_integrity_check,
                "get_issues_from_execution_log",
                return_value={1234, 5678},
            ),
            patch.object(
                session_issue_integrity_check,
                "get_issues_from_session_file",
                return_value={1234},  # Missing 5678
            ),
        ):
            result = session_issue_integrity_check.verify_integrity(ctx)
            assert len(result) == 1
            assert "5678" in result[0]

    def test_skips_current_session(self, tmp_path: Path) -> None:
        """Skips the current session when verifying integrity."""
        ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
        with (
            patch.object(
                session_issue_integrity_check,
                "get_recent_session_ids",
                return_value=["current-session", "old-session"],
            ),
            patch.object(
                session_issue_integrity_check,
                "get_issues_from_execution_log",
                return_value=set(),
            ),
            patch.object(
                session_issue_integrity_check,
                "get_issues_from_session_file",
                return_value=set(),
            ),
        ):
            result = session_issue_integrity_check.verify_integrity(ctx)
            assert result == []

    def test_no_warning_when_all_match(self, tmp_path: Path) -> None:
        """No warnings when log and file issues match."""
        ctx = create_hook_context({"session_id": self.TEST_SESSION_ID})
        with (
            patch.object(
                session_issue_integrity_check,
                "get_recent_session_ids",
                return_value=["old-session"],
            ),
            patch.object(
                session_issue_integrity_check,
                "get_issues_from_execution_log",
                return_value={1234, 5678},
            ),
            patch.object(
                session_issue_integrity_check,
                "get_issues_from_session_file",
                return_value={1234, 5678},
            ),
        ):
            result = session_issue_integrity_check.verify_integrity(ctx)
            assert result == []


class TestMain:
    """Tests for main function."""

    def test_skips_non_session_start_hook(self, capsys) -> None:
        """Returns continue=True without checking for non-SessionStart hooks."""
        with patch.object(
            session_issue_integrity_check,
            "parse_hook_input",
            return_value={"hook_type": "PreToolUse"},
        ):
            session_issue_integrity_check.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out.strip())
            assert result["continue"] is True
            assert "systemMessage" not in result

    def test_session_start_with_no_warnings(self, capsys) -> None:
        """Returns continue=True with no systemMessage when no integrity issues."""
        with (
            patch.object(
                session_issue_integrity_check,
                "parse_hook_input",
                return_value={"hook_type": "SessionStart"},
            ),
            patch.object(
                session_issue_integrity_check,
                "verify_integrity",
                return_value=[],
            ),
            patch.object(
                session_issue_integrity_check,
                "log_hook_execution",
            ),
        ):
            session_issue_integrity_check.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out.strip())
            assert result["continue"] is True
            assert "systemMessage" not in result

    def test_session_start_with_warnings(self, capsys) -> None:
        """Returns continue=True with systemMessage when integrity issues found."""
        warnings = ["Session abc...: Issues logged but missing from file: [1234]"]
        with (
            patch.object(
                session_issue_integrity_check,
                "parse_hook_input",
                return_value={"hook_type": "SessionStart"},
            ),
            patch.object(
                session_issue_integrity_check,
                "verify_integrity",
                return_value=warnings,
            ),
            patch.object(
                session_issue_integrity_check,
                "log_hook_execution",
            ),
        ):
            session_issue_integrity_check.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out.strip())
            assert result["continue"] is True
            assert "systemMessage" in result
            assert "整合性問題" in result["systemMessage"]
            assert "1234" in result["systemMessage"]

    def test_handles_exception_gracefully(self, capsys) -> None:
        """Returns continue=True even when exception occurs."""
        with patch.object(
            session_issue_integrity_check,
            "parse_hook_input",
            side_effect=Exception("Test error"),
        ):
            session_issue_integrity_check.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out.strip())
            assert result["continue"] is True
