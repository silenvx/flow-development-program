#!/usr/bin/env python3
"""Tests for common.py hook utilities."""

import json
import sys
from pathlib import Path

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from common import log_review_comment
from lib.constants import LOG_MAX_ROTATED_FILES, LOG_MAX_SIZE_BYTES, SESSION_GAP_THRESHOLD
from lib.execution import log_hook_execution, rotate_log_if_needed
from lib.input_context import extract_input_context, merge_details_with_context
from lib.review import _is_comment_already_logged
from lib.session import (
    handle_session_id_arg,
    parse_hook_input,
)
from lib.strings import (
    extract_inline_skip_env,
    is_skip_env_enabled,
    sanitize_branch_name,
    split_command_chain,
    strip_quoted_strings,
)
from lib.timestamp import parse_iso_timestamp


class TestStripQuotedStrings:
    """Tests for strip_quoted_strings function."""

    def test_single_quotes(self):
        """Should remove single-quoted strings."""
        assert strip_quoted_strings("echo 'hello world'") == "echo "

    def test_double_quotes(self):
        """Should remove double-quoted strings."""
        assert strip_quoted_strings('echo "hello world"') == "echo "

    def test_mixed_quotes(self):
        """Should remove both single and double quoted strings."""
        assert strip_quoted_strings("echo 'hello' and \"world\"") == "echo  and "

    def test_no_quotes(self):
        """Should return unchanged if no quotes."""
        assert strip_quoted_strings("gh pr create") == "gh pr create"

    def test_empty_string(self):
        """Should handle empty string."""
        assert strip_quoted_strings("") == ""

    def test_empty_quotes(self):
        """Should remove empty quoted strings."""
        assert strip_quoted_strings("echo '' and \"\"") == "echo  and "


class TestSplitCommandChain:
    """Tests for split_command_chain function (Issue #959)."""

    def test_single_command(self):
        """Should return single command as list."""
        assert split_command_chain("git commit") == ["git commit"]

    def test_and_chain(self):
        """Should split on && operator."""
        assert split_command_chain("git add && git commit") == ["git add", "git commit"]

    def test_or_chain(self):
        """Should split on || operator."""
        assert split_command_chain("cmd1 || cmd2") == ["cmd1", "cmd2"]

    def test_semicolon_chain(self):
        """Should split on ; operator."""
        assert split_command_chain("cmd1; cmd2") == ["cmd1", "cmd2"]

    def test_mixed_operators(self):
        """Should split on mixed operators."""
        result = split_command_chain("cmd1 && cmd2 || cmd3; cmd4")
        assert result == ["cmd1", "cmd2", "cmd3", "cmd4"]

    def test_empty_string(self):
        """Should return empty list for empty string."""
        assert split_command_chain("") == []

    def test_whitespace_only(self):
        """Should return empty list for whitespace only."""
        assert split_command_chain("   ") == []

    def test_strips_whitespace(self):
        """Should strip whitespace from subcommands."""
        result = split_command_chain("  git add  &&  git commit  ")
        assert result == ["git add", "git commit"]

    def test_triple_operator(self):
        """Should handle commands with three or more parts."""
        result = split_command_chain("git add . && git commit -m msg && git push")
        assert result == ["git add .", "git commit -m msg", "git push"]


class TestSanitizeBranchName:
    """Tests for sanitize_branch_name function."""

    def test_slash_replacement(self):
        """Should replace forward slashes with dashes."""
        assert sanitize_branch_name("feature/test") == "feature-test"
        assert sanitize_branch_name("feature/sub/test") == "feature-sub-test"

    def test_backslash_replacement(self):
        """Should replace backslashes with dashes."""
        assert sanitize_branch_name("feature\\test") == "feature-test"

    def test_colon_replacement(self):
        """Should replace colons with dashes."""
        assert sanitize_branch_name("feature:test") == "feature-test"

    def test_special_chars_replacement(self):
        """Should replace various special characters."""
        assert sanitize_branch_name("test<branch>") == "test-branch"
        assert sanitize_branch_name('test"branch"') == "test-branch"
        assert sanitize_branch_name("test|branch") == "test-branch"
        assert sanitize_branch_name("test?branch") == "test-branch"
        assert sanitize_branch_name("test*branch") == "test-branch"

    def test_space_replacement(self):
        """Should replace spaces with underscores."""
        assert sanitize_branch_name("feature test") == "feature_test"

    def test_consecutive_dashes(self):
        """Should remove consecutive dashes."""
        assert sanitize_branch_name("feature//test") == "feature-test"
        assert sanitize_branch_name("feature///test") == "feature-test"

    def test_leading_trailing_dashes(self):
        """Should remove leading and trailing dashes."""
        assert sanitize_branch_name("/feature/") == "feature"
        assert sanitize_branch_name("//feature//") == "feature"

    def test_no_changes_needed(self):
        """Should return unchanged if no special characters."""
        assert sanitize_branch_name("main") == "main"
        assert sanitize_branch_name("feature-test") == "feature-test"
        assert sanitize_branch_name("feature_test") == "feature_test"

    def test_complex_branch_name(self):
        """Should handle complex branch names with multiple issues."""
        assert sanitize_branch_name("feature/ABC-123/test branch") == "feature-ABC-123-test_branch"

    def test_empty_string(self):
        """Should handle empty string."""
        assert sanitize_branch_name("") == ""

    def test_only_special_chars(self):
        """Should handle string with only special characters."""
        assert sanitize_branch_name("///") == ""
        assert sanitize_branch_name("---") == ""


class TestLogHookExecution:
    """Tests for log_hook_execution function.

    Issue #1994: Updated to test session-specific file format.
    """

    def setup_method(self):
        """Set up test fixtures."""
        import shutil
        import tempfile

        self.temp_dir = Path(tempfile.mkdtemp())
        self.test_session_id = "test-session-12345"
        self._shutil = shutil

    def teardown_method(self):
        """Clean up test fixtures."""
        self._shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _get_session_log_file(self) -> Path:
        """Get the session-specific log file path."""
        return self.temp_dir / f"hook-execution-{self.test_session_id}.jsonl"

    def _read_log_entries(self) -> list:
        """Read all log entries from the session-specific file."""
        log_file = self._get_session_log_file()
        if not log_file.exists():
            return []
        with open(log_file) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_basic_approve_log(self):
        """Should log basic approve decision."""
        log_hook_execution(
            "test-hook",
            "approve",
            execution_log_dir=self.temp_dir,
            session_id=self.test_session_id,
        )

        entries = self._read_log_entries()
        assert len(entries) > 0

        entry = entries[-1]
        assert entry["hook"] == "test-hook"
        assert entry["decision"] == "approve"
        assert "timestamp" in entry

    def test_block_with_reason(self):
        """Should log block decision with reason."""
        log_hook_execution(
            "test-hook",
            "block",
            "Test reason",
            execution_log_dir=self.temp_dir,
            session_id=self.test_session_id,
        )

        entries = self._read_log_entries()
        entry = entries[-1]
        assert entry["hook"] == "test-hook"
        assert entry["decision"] == "block"
        assert entry["reason"] == "Test reason"

    def test_with_details(self):
        """Should log with additional details."""
        details = {"command": "git commit", "files": ["a.py", "b.py"]}
        log_hook_execution(
            "test-hook",
            "approve",
            None,
            details,
            execution_log_dir=self.temp_dir,
            session_id=self.test_session_id,
        )

        entries = self._read_log_entries()
        entry = entries[-1]
        assert entry["hook"] == "test-hook"
        assert entry["details"] == details

    def test_creates_log_directory(self):
        """Should create log directory if it doesn't exist."""
        new_dir = self.temp_dir / "new_log_dir"
        log_hook_execution(
            "test-hook",
            "approve",
            execution_log_dir=new_dir,
            session_id=self.test_session_id,
        )
        assert new_dir.exists()
        assert new_dir.is_dir()

    def test_appends_to_existing_log(self):
        """Should append to existing log file."""
        log_hook_execution(
            "hook1", "approve", execution_log_dir=self.temp_dir, session_id=self.test_session_id
        )
        log_hook_execution(
            "hook2",
            "block",
            "reason",
            execution_log_dir=self.temp_dir,
            session_id=self.test_session_id,
        )
        log_hook_execution(
            "hook3", "approve", execution_log_dir=self.temp_dir, session_id=self.test_session_id
        )

        entries = self._read_log_entries()

        # Check we have at least 3 entries
        assert len(entries) >= 3

        # Check last 3 entries
        assert entries[-3]["hook"] == "hook1"
        assert entries[-2]["hook"] == "hook2"
        assert entries[-1]["hook"] == "hook3"


class TestParseIsoTimestamp:
    """Tests for parse_iso_timestamp function."""

    def test_z_suffix_format(self):
        """Handles GitHub CLI 'Z' suffix format."""
        result = parse_iso_timestamp("2025-12-17T10:30:00Z")
        assert result is not None
        assert result.year == 2025
        assert result.month == 12
        assert result.day == 17
        assert result.hour == 10
        assert result.minute == 30

    def test_offset_format(self):
        """Handles +00:00 offset format."""
        result = parse_iso_timestamp("2025-12-17T10:30:00+00:00")
        assert result is not None
        assert result.hour == 10

    def test_microseconds_format(self):
        """Handles format with microseconds."""
        result = parse_iso_timestamp("2025-12-17T10:30:00.123456+00:00")
        assert result is not None
        assert result.microsecond == 123456

    def test_empty_string(self):
        """Returns None for empty string."""
        result = parse_iso_timestamp("")
        assert result is None

    def test_none_input(self):
        """Returns None for None input."""
        result = parse_iso_timestamp(None)
        assert result is None

    def test_invalid_format(self):
        """Returns None for invalid format."""
        result = parse_iso_timestamp("not-a-timestamp")
        assert result is None


class TestExtractInputContext:
    """Tests for extract_input_context function."""

    def test_bash_command(self):
        """Should extract Bash command preview."""
        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title test"},
        }
        result = extract_input_context(data)
        assert result["tool_name"] == "Bash"
        assert result["input_preview"] == "gh pr create --title test"
        assert result["hook_type"] == "PreToolUse"

    def test_bash_command_truncation(self):
        """Should truncate long commands."""
        long_cmd = "a" * 100
        data = {
            "tool_name": "Bash",
            "tool_input": {"command": long_cmd},
        }
        result = extract_input_context(data, max_preview_len=50)
        assert result["input_preview"] == "a" * 50 + "..."

    def test_edit_file_path(self):
        """Should extract file_path for Edit tool."""
        data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/path/to/file.py", "old_string": "x", "new_string": "y"},
        }
        result = extract_input_context(data)
        assert result["tool_name"] == "Edit"
        assert result["input_preview"] == "/path/to/file.py"

    def test_read_path(self):
        """Should extract path for Read tool."""
        data = {
            "tool_name": "Read",
            "tool_input": {"path": "/path/to/file.py"},
        }
        result = extract_input_context(data)
        assert result["tool_name"] == "Read"
        assert result["input_preview"] == "/path/to/file.py"

    def test_post_tool_use_detection(self):
        """Should detect PostToolUse hook type."""
        data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_output": "file1.txt\nfile2.txt",
        }
        result = extract_input_context(data)
        assert result["hook_type"] == "PostToolUse"

    def test_stop_hook_detection(self):
        """Should detect Stop hook type."""
        data = {"stop_hook_active": True}
        result = extract_input_context(data)
        assert result["hook_type"] == "Stop"

    def test_notification_hook_detection(self):
        """Should detect Notification hook type."""
        data = {"notification": "some message"}
        result = extract_input_context(data)
        assert result["hook_type"] == "Notification"

    def test_empty_input(self):
        """Should handle empty input gracefully.

        Issue #1312: Empty input (e.g., from JSON decode error) is marked as
        Unknown to distinguish from real SessionStart events which have at
        least session_id or other context.
        """
        result = extract_input_context({})
        assert result == {"hook_type": "Unknown"}

    def test_session_start_with_session_id(self):
        """Should infer SessionStart when only session_id is present.

        Real SessionStart events have at least session_id but no tool/stop/
        notification indicators.
        """
        result = extract_input_context({"session_id": "abc123"})
        assert result == {"hook_type": "SessionStart"}

    def test_generic_string_value(self):
        """Should extract first string value for unknown tools."""
        data = {
            "tool_name": "CustomTool",
            "tool_input": {"pattern": "*.py", "options": {}},
        }
        result = extract_input_context(data)
        assert result["input_preview"] == "*.py"


class TestMergeDetailsWithContext:
    """Tests for merge_details_with_context function."""

    def test_merge_both(self):
        """Should merge details and context."""
        details = {"issues": [1, 2, 3]}
        context = {"tool_name": "Bash", "input_preview": "gh pr create"}
        result = merge_details_with_context(details, context)
        assert result["issues"] == [1, 2, 3]
        assert result["tool_name"] == "Bash"
        assert result["input_preview"] == "gh pr create"

    def test_details_override_context(self):
        """Should prefer details over context for same key."""
        details = {"tool_name": "Custom"}
        context = {"tool_name": "Bash", "input_preview": "test"}
        result = merge_details_with_context(details, context)
        assert result["tool_name"] == "Custom"
        assert result["input_preview"] == "test"

    def test_none_details(self):
        """Should handle None details."""
        context = {"tool_name": "Bash"}
        result = merge_details_with_context(None, context)
        assert result == {"tool_name": "Bash"}

    def test_empty_details(self):
        """Should handle empty details."""
        context = {"tool_name": "Bash"}
        result = merge_details_with_context({}, context)
        assert result == {"tool_name": "Bash"}


class TestParseGhPrCommand:
    """Tests for parse_gh_pr_command function."""

    def test_simple_merge_command(self):
        """Should parse simple gh pr merge command."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("gh pr merge 123")
        assert subcommand == "merge"
        assert pr_number == "123"

    def test_merge_with_flags(self):
        """Should handle flags in merge command."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("gh pr merge --squash 456")
        assert subcommand == "merge"
        assert pr_number == "456"

    def test_merge_with_hash_prefix(self):
        """Should handle #123 format."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("gh pr merge #789")
        assert subcommand == "merge"
        assert pr_number == "789"

    def test_global_repo_flag(self):
        """Should handle global --repo flag before pr."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("gh --repo owner/repo pr merge 123")
        assert subcommand == "merge"
        assert pr_number == "123"

    def test_short_repo_flag(self):
        """Should handle -R flag before pr."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("gh -R owner/repo pr close 456")
        assert subcommand == "close"
        assert pr_number == "456"

    def test_view_command(self):
        """Should parse view command."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("gh pr view 789")
        assert subcommand == "view"
        assert pr_number == "789"

    def test_no_pr_number(self):
        """Should return None for PR number if not present."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("gh pr merge")
        assert subcommand == "merge"
        assert pr_number is None

    def test_non_pr_command(self):
        """Should return None for non-pr commands."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("gh issue create")
        assert subcommand is None
        assert pr_number is None

    def test_non_gh_command(self):
        """Should return None for non-gh commands."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("git push origin main")
        assert subcommand is None
        assert pr_number is None

    def test_quoted_body_not_detected(self):
        """Should not extract PR number from quoted body."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command('gh pr create --body "fixes PR #123"')
        # This is a create command, not operating on PR 123
        assert subcommand == "create"
        assert pr_number is None

    def test_flag_with_numeric_value(self):
        """Should skip numeric flag values like --limit 100."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("gh pr list --limit 100")
        assert subcommand == "list"
        assert pr_number is None

    def test_chained_command(self):
        """Should only parse up to pipe/semicolon."""
        from lib.github import parse_gh_pr_command

        subcommand, pr_number = parse_gh_pr_command("gh pr merge 123 && echo done")
        assert subcommand == "merge"
        assert pr_number == "123"


class TestExtractPrNumber:
    """Tests for extract_pr_number function."""

    def test_extracts_pr_from_merge(self):
        """Should extract PR number from merge command."""
        from lib.github import extract_pr_number

        result = extract_pr_number("gh pr merge 123")
        assert result == "123"

    def test_extracts_pr_from_view(self):
        """Should extract PR number from view command."""
        from lib.github import extract_pr_number

        result = extract_pr_number("gh pr view 456")
        assert result == "456"

    def test_returns_none_for_no_number(self):
        """Should return None when no PR number present."""
        from lib.github import extract_pr_number

        result = extract_pr_number("gh pr merge")
        assert result is None

    def test_returns_none_for_non_gh_command(self):
        """Should return None for non-gh commands."""
        from lib.github import extract_pr_number

        result = extract_pr_number("git status")
        assert result is None


class TestCheckAndUpdateSessionMarker:
    """Tests for check_and_update_session_marker function."""

    def setup_method(self):
        """Set up test fixtures."""
        import shutil
        import tempfile

        # Import common module to access and modify SESSION_DIR
        import common as common_module

        self.temp_dir = tempfile.mkdtemp()
        self.original_session_dir = common_module.SESSION_DIR
        common_module.SESSION_DIR = Path(self.temp_dir)
        self._shutil = shutil
        self._common_module = common_module

    def teardown_method(self):
        """Clean up test fixtures."""
        self._common_module.SESSION_DIR = self.original_session_dir
        self._shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_first_call_returns_true(self):
        """First call in a new session should return True."""
        from common import check_and_update_session_marker

        result = check_and_update_session_marker("test-marker")
        assert result

    def test_second_call_returns_false(self):
        """Second call within session gap should return False."""
        from common import check_and_update_session_marker

        # First call should return True
        result1 = check_and_update_session_marker("test-marker-2")
        assert result1

        # Second call within session gap should return False
        result2 = check_and_update_session_marker("test-marker-2")
        assert not result2

    def test_different_markers_independent(self):
        """Different marker names should be independent."""
        from common import check_and_update_session_marker

        # First marker
        result1 = check_and_update_session_marker("marker-a")
        assert result1

        # Different marker should also return True
        result2 = check_and_update_session_marker("marker-b")
        assert result2

    def test_creates_marker_file(self):
        """Should create marker file after call."""
        from common import check_and_update_session_marker

        check_and_update_session_marker("test-marker-files")

        marker_file = Path(self.temp_dir) / "test-marker-files.marker"
        assert marker_file.exists()

    def test_creates_lock_file(self):
        """Should create lock file during operation."""
        from common import check_and_update_session_marker

        check_and_update_session_marker("test-marker-lock")

        lock_file = Path(self.temp_dir) / "test-marker-lock.lock"
        assert lock_file.exists()

    def test_marker_contains_timestamp(self):
        """Marker file should contain a valid timestamp."""
        import time

        from common import check_and_update_session_marker

        before = time.time()
        check_and_update_session_marker("test-marker-timestamp")
        after = time.time()

        marker_file = Path(self.temp_dir) / "test-marker-timestamp.marker"
        timestamp = float(marker_file.read_text().strip())

        assert timestamp >= before
        assert timestamp <= after

    def test_expired_marker_returns_true(self):
        """Should return True when marker has expired (SESSION_GAP_THRESHOLD passed)."""
        import time

        from common import check_and_update_session_marker

        # Create an old marker file manually
        marker_file = Path(self.temp_dir) / "test-expired.marker"
        old_time = time.time() - SESSION_GAP_THRESHOLD - 1
        marker_file.write_text(str(old_time))

        # Should return True because marker has expired
        result = check_and_update_session_marker("test-expired")
        assert result

    def test_invalid_marker_content_treated_as_new_session(self):
        """Should treat invalid marker file content as new session."""
        from common import check_and_update_session_marker

        # Create marker file with invalid content
        marker_file = Path(self.temp_dir) / "test-invalid.marker"
        marker_file.write_text("not-a-number")

        # Should return True (treat as new session)
        result = check_and_update_session_marker("test-invalid")
        assert result

    def test_empty_marker_file_treated_as_new_session(self):
        """Should treat empty marker file as new session."""
        from common import check_and_update_session_marker

        # Create empty marker file
        marker_file = Path(self.temp_dir) / "test-empty.marker"
        marker_file.write_text("")

        # Should return True (treat as new session)
        result = check_and_update_session_marker("test-empty")
        assert result


class TestSessionGapThreshold:
    """Tests for SESSION_GAP_THRESHOLD constant."""

    def test_session_gap_threshold_value(self):
        """SESSION_GAP_THRESHOLD should be 3600 seconds (1 hour)."""

        assert SESSION_GAP_THRESHOLD == 3600

    def test_session_gap_threshold_is_one_hour(self):
        """SESSION_GAP_THRESHOLD should equal 60 * 60 (one hour in seconds)."""

        assert SESSION_GAP_THRESHOLD == 60 * 60


class TestHandleSessionIdArg:
    """Tests for handle_session_id_arg function (Issue #2326, #2496).

    Issue #2496: Updated to test return value instead of global state.
    The function now returns the validated session_id instead of setting global state.
    """

    def test_returns_valid_session_id(self):
        """Should return valid UUID session ID."""
        valid_uuid = "8ea2a2a0-ad70-4eb8-92d0-20912e119f94"
        result = handle_session_id_arg(valid_uuid)
        assert result == valid_uuid

    def test_returns_none_for_none_input(self):
        """Should return None when session_id is None."""
        result = handle_session_id_arg(None)
        assert result is None

    def test_warns_on_invalid_session_id(self, capsys):
        """Should print warning for invalid session ID format."""
        result = handle_session_id_arg("not-a-valid-uuid")
        captured = capsys.readouterr()
        assert "Warning: Invalid session ID format" in captured.err
        # Should return None for invalid session ID
        assert result is None

    def test_returns_none_for_invalid_format(self):
        """Should return None when format is invalid."""
        result = handle_session_id_arg("ppid-12345")
        assert result is None

    def test_returns_none_for_empty_string(self, capsys):
        """Should treat empty string same as None (return None, no warning)."""
        result = handle_session_id_arg("")
        # Empty string is falsy, so return None (same as None)
        assert result is None
        # No warning should be printed for empty string
        captured = capsys.readouterr()
        assert captured.err == ""


class TestParseHookInput:
    """Tests for parse_hook_input function (Issue #759, #2496).

    Issue #2496: Updated - parse_hook_input no longer sets global state.
    It only parses JSON from stdin and returns the result.
    """

    def setup_method(self):
        """Set up test fixtures."""
        import io

        # Save original stdin
        self.original_stdin = sys.stdin
        self._io = io

    def teardown_method(self):
        """Clean up test fixtures."""
        # Restore stdin
        sys.stdin = self.original_stdin

    def test_parses_valid_json(self):
        """Should parse valid JSON input."""
        sys.stdin = self._io.StringIO('{"tool_name": "Bash", "command": "ls"}')
        result = parse_hook_input()
        assert result["tool_name"] == "Bash"
        assert result["command"] == "ls"

    def test_returns_session_id_from_input(self):
        """Should return session_id from hook input in the result dict."""
        sys.stdin = self._io.StringIO('{"session_id": "test-session-123", "tool_name": "Edit"}')
        result = parse_hook_input()
        assert result["session_id"] == "test-session-123"
        assert result["tool_name"] == "Edit"

    def test_returns_empty_dict_on_invalid_json(self):
        """Should return empty dict on JSON parse error."""
        sys.stdin = self._io.StringIO("not valid json")
        result = parse_hook_input()
        assert result == {}

    def test_returns_empty_dict_on_empty_input(self):
        """Should return empty dict on empty input."""
        sys.stdin = self._io.StringIO("")
        result = parse_hook_input()
        assert result == {}

    def test_returns_result_without_session_id_if_missing(self):
        """Should return result without session_id if not in input."""
        sys.stdin = self._io.StringIO('{"tool_name": "Read"}')
        result = parse_hook_input()
        assert result["tool_name"] == "Read"
        assert "session_id" not in result


# =============================================================================
# CWD Detection Tests (Issue #671, #682)
# =============================================================================


class TestExtractCdTargetFromCommand:
    """Tests for extract_cd_target_from_command function."""

    def test_simple_cd_with_double_ampersand(self):
        """Should extract path from 'cd path &&' pattern."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command("cd /main/repo && git worktree remove .")
        assert result == "/main/repo"

    def test_cd_with_quoted_path(self):
        """Should extract quoted path."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command('cd "/path/with spaces" && git status')
        assert result == "/path/with spaces"

    def test_cd_with_single_quoted_path(self):
        """Should extract single quoted path."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command("cd '/path/with spaces' && git status")
        assert result == "/path/with spaces"

    def test_cd_with_relative_path(self):
        """Should extract relative path."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command("cd .. && git worktree remove foo")
        assert result == ".."

    def test_cd_with_semicolon(self):
        """Should extract path with semicolon separator."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command("cd /repo; git status")
        assert result == "/repo"

    def test_no_cd_pattern(self):
        """Should return None when no cd pattern."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command("git worktree remove foo")
        assert result is None

    def test_cd_without_separator(self):
        """Should return None when cd has no && or ; separator."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command("cd /path")
        assert result is None

    def test_empty_command(self):
        """Should handle empty command."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command("")
        assert result is None

    def test_escaped_double_quote_in_path(self):
        """Should handle escaped double quotes in path (Issue #680)."""
        from lib.cwd import extract_cd_target_from_command

        # Note: shlex.split() handles \" inside double quotes
        result = extract_cd_target_from_command(
            r'cd "/path/with\"escaped/quotes" && git worktree remove foo'
        )
        assert result == '/path/with"escaped/quotes'

    def test_escaped_single_quote_in_path(self):
        """Should handle escaped single quotes in path (Issue #680)."""
        from lib.cwd import extract_cd_target_from_command

        # In shell, single quotes are escaped as: 'path'\''escaped'
        # But shlex doesn't handle this pattern well, so we test simpler cases
        result = extract_cd_target_from_command("cd '/simple/path' && git worktree remove foo")
        assert result == "/simple/path"

    def test_non_leading_cd(self):
        """Should detect cd not at command start (Issue #679)."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command(
            "export VAR=value && cd /path && git worktree remove foo"
        )
        assert result == "/path"

    def test_multiple_cd_returns_last(self):
        """Should return the last cd target when multiple cd commands exist."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command(
            "cd /first && echo test && cd /second && git status"
        )
        assert result == "/second"

    def test_cd_with_tilde(self):
        """Should handle tilde in path."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command("cd ~/projects && git status")
        assert result == "~/projects"

    def test_cd_as_argument_not_detected(self):
        """Should not detect cd when it appears as argument (Codex review fix)."""
        from lib.cwd import extract_cd_target_from_command

        # cd appears as echo argument, not a real cd command
        result = extract_cd_target_from_command("echo cd /tmp && git status")
        assert result is None

    def test_cd_in_quoted_string_not_detected(self):
        """Should not detect cd inside quoted strings."""
        from lib.cwd import extract_cd_target_from_command

        result = extract_cd_target_from_command("python -c \"print('cd /tmp')\" && git status")
        assert result is None

    def test_cd_after_pipe_not_detected(self):
        """Should not detect cd after pipe (runs in subshell)."""
        from lib.cwd import extract_cd_target_from_command

        # cd after pipe runs in subshell, doesn't affect subsequent commands
        result = extract_cd_target_from_command("echo test | cd /path && git status")
        assert result is None

    def test_cd_after_or_not_detected(self):
        """Should not detect cd after || (conditional execution)."""
        from lib.cwd import extract_cd_target_from_command

        # cd after || only runs if previous command fails
        result = extract_cd_target_from_command("true || cd /path && git status")
        assert result is None


class TestGetEffectiveCwd:
    """Tests for get_effective_cwd function."""

    def test_with_cd_pattern(self):
        """Should use cd target when command has cd pattern."""
        from unittest.mock import patch

        from lib.cwd import get_effective_cwd

        with patch.dict("os.environ", {}, clear=True):
            with patch("pathlib.Path.cwd", return_value=Path("/cwd")):
                # Mock Path.exists() and Path.resolve()
                with patch.object(Path, "exists", return_value=True):
                    with patch.object(Path, "resolve", lambda self: self):
                        result = get_effective_cwd("cd /main/repo && git worktree remove .")
                        assert result == Path("/main/repo")

    def test_without_cd_uses_env_var(self):
        """Should use CLAUDE_WORKING_DIRECTORY when no cd pattern."""
        from unittest.mock import patch

        from lib.cwd import get_effective_cwd

        with patch.dict("os.environ", {"CLAUDE_WORKING_DIRECTORY": "/env/cwd"}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    result = get_effective_cwd("git status")
                    assert result == Path("/env/cwd")

    def test_fallback_to_pwd(self):
        """Should fallback to PWD when CLAUDE_WORKING_DIRECTORY not set."""
        from unittest.mock import patch

        from lib.cwd import get_effective_cwd

        with patch.dict("os.environ", {"PWD": "/pwd/path"}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    result = get_effective_cwd("git status")
                    assert result == Path("/pwd/path")

    def test_fallback_to_path_cwd(self):
        """Should fallback to Path.cwd() when no env vars set."""
        from unittest.mock import patch

        from lib.cwd import get_effective_cwd

        with patch.dict("os.environ", {}, clear=True):
            with patch("pathlib.Path.cwd", return_value=Path("/fallback/cwd")):
                with patch.object(Path, "resolve", lambda self: self):
                    result = get_effective_cwd("git status")
                    assert result == Path("/fallback/cwd")

    def test_none_command(self):
        """Should handle None command."""
        from unittest.mock import patch

        from lib.cwd import get_effective_cwd

        with patch.dict("os.environ", {"CLAUDE_WORKING_DIRECTORY": "/env/cwd"}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    result = get_effective_cwd(None)
                    assert result == Path("/env/cwd")

    def test_relative_cd_with_base_cwd(self):
        """Issue #1035: Should resolve relative cd path against base_cwd."""
        from unittest.mock import patch

        from lib.cwd import get_effective_cwd

        with patch.dict("os.environ", {"CLAUDE_WORKING_DIRECTORY": "/env/cwd"}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    # Relative cd should be resolved against base_cwd, not env cwd
                    result = get_effective_cwd("cd src && git status", base_cwd="/worktree/foo")
                    assert result == Path("/worktree/foo/src")

    def test_absolute_cd_ignores_base_cwd(self):
        """Issue #1035: Absolute cd path should not use base_cwd."""
        from unittest.mock import patch

        from lib.cwd import get_effective_cwd

        with patch.dict("os.environ", {}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    # Absolute cd should not be affected by base_cwd
                    result = get_effective_cwd(
                        "cd /main/repo && git status", base_cwd="/worktree/foo"
                    )
                    assert result == Path("/main/repo")

    def test_no_cd_pattern_with_base_cwd(self):
        """Issue #1035: Should return base_cwd when no cd pattern and base_cwd provided."""
        from unittest.mock import patch

        from lib.cwd import get_effective_cwd

        with patch.dict("os.environ", {"CLAUDE_WORKING_DIRECTORY": "/env/cwd"}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    # No cd pattern, should use base_cwd
                    result = get_effective_cwd("git status", base_cwd="/worktree/foo")
                    assert result == Path("/worktree/foo")

    def test_none_command_with_base_cwd(self):
        """Issue #1035: Should return base_cwd when command is None."""
        from unittest.mock import patch

        from lib.cwd import get_effective_cwd

        with patch.dict("os.environ", {"CLAUDE_WORKING_DIRECTORY": "/env/cwd"}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    result = get_effective_cwd(None, base_cwd="/worktree/foo")
                    assert result == Path("/worktree/foo")


class TestCheckCwdInsidePath:
    """Tests for check_cwd_inside_path function."""

    def test_cwd_is_target_path(self):
        """Should return True when cwd equals target path."""
        from unittest.mock import patch

        from lib.cwd import check_cwd_inside_path

        with patch.dict("os.environ", {"CLAUDE_WORKING_DIRECTORY": "/worktree/foo"}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    result = check_cwd_inside_path(Path("/worktree/foo"))
                    assert result

    def test_cwd_inside_target_path(self):
        """Should return True when cwd is inside target path."""
        from unittest.mock import patch

        from lib.cwd import check_cwd_inside_path

        with patch.dict(
            "os.environ", {"CLAUDE_WORKING_DIRECTORY": "/worktree/foo/src"}, clear=True
        ):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    result = check_cwd_inside_path(Path("/worktree/foo"))
                    assert result

    def test_cwd_outside_target_path(self):
        """Should return False when cwd is outside target path."""
        from unittest.mock import patch

        from lib.cwd import check_cwd_inside_path

        with patch.dict("os.environ", {"CLAUDE_WORKING_DIRECTORY": "/main/repo"}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    result = check_cwd_inside_path(Path("/worktree/foo"))
                    assert not result

    def test_cd_moves_outside_worktree(self):
        """Should return False when cd moves cwd outside target (Issue #682)."""
        from unittest.mock import patch

        from lib.cwd import check_cwd_inside_path

        # Current cwd is inside worktree, but cd moves outside
        with patch.dict("os.environ", {"CLAUDE_WORKING_DIRECTORY": "/worktree/foo"}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    result = check_cwd_inside_path(
                        Path("/worktree/foo"),
                        command="cd /main/repo && git worktree remove .",
                    )
                    assert not result

    def test_cd_stays_inside_worktree(self):
        """Should return True when cd stays inside target."""
        from unittest.mock import patch

        from lib.cwd import check_cwd_inside_path

        with patch.dict("os.environ", {"CLAUDE_WORKING_DIRECTORY": "/worktree/foo"}, clear=True):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "resolve", lambda self: self):
                    result = check_cwd_inside_path(
                        Path("/worktree/foo"),
                        command="cd /worktree/foo/src && git status",
                    )
                    assert result


# =============================================================================
# Log Rotation Tests (Issue #710)
# =============================================================================


class TestRotateLogIfNeeded:
    """Tests for rotate_log_if_needed function."""

    def setup_method(self):
        """Set up test fixtures."""
        import shutil
        import tempfile

        self.temp_dir = Path(tempfile.mkdtemp())
        self.log_file = self.temp_dir / "test.log"
        self._shutil = shutil

    def teardown_method(self):
        """Clean up test fixtures."""
        self._shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_no_rotation_when_file_not_exists(self):
        """Should return False when log file doesn't exist."""
        result = rotate_log_if_needed(self.log_file, 1000, 3)
        assert not result

    def test_no_rotation_when_under_max_size(self):
        """Should return False when file is under max size."""
        # Create a small file
        self.log_file.write_text("small content")
        result = rotate_log_if_needed(self.log_file, 1000, 3)
        assert not result
        # Original file should still exist with same content
        assert self.log_file.read_text() == "small content"

    def test_rotation_when_exceeds_max_size(self):
        """Should rotate when file exceeds max size."""
        # Create a file that exceeds the limit
        content = "x" * 100
        self.log_file.write_text(content)

        result = rotate_log_if_needed(self.log_file, 50, 3)

        assert result
        # Original file should not exist (renamed to .log.1)
        assert not self.log_file.exists()
        # Rotated file should exist
        rotated = self.log_file.with_suffix(".log.1")
        assert rotated.exists()
        assert rotated.read_text() == content

    def test_cascading_rotation(self):
        """Should cascade rotation (log.1 -> log.2, log -> log.1)."""
        # Create existing rotated file
        rotated_1 = self.log_file.with_suffix(".log.1")
        rotated_1.write_text("old content")

        # Create current log that exceeds limit
        self.log_file.write_text("new content that exceeds limit")

        result = rotate_log_if_needed(self.log_file, 10, 3)

        assert result
        # Old log.1 should now be log.2
        rotated_2 = self.log_file.with_suffix(".log.2")
        assert rotated_2.exists()
        assert rotated_2.read_text() == "old content"
        # Current log should now be log.1
        assert rotated_1.exists()
        assert rotated_1.read_text() == "new content that exceeds limit"

    def test_oldest_file_deleted_on_rotation(self):
        """Should delete oldest file when max_files reached."""
        # Create existing rotated files
        for i in range(1, 4):
            rotated = self.log_file.with_suffix(f".log.{i}")
            rotated.write_text(f"content {i}")

        # Create current log that exceeds limit
        self.log_file.write_text("x" * 100)

        result = rotate_log_if_needed(self.log_file, 50, 3)

        assert result
        # Original log.3 ("content 3") is deleted; log.2 shifts to log.3
        rotated_3 = self.log_file.with_suffix(".log.3")
        assert rotated_3.exists()
        assert rotated_3.read_text() == "content 2"  # Was log.2
        # log.4 should not exist (max_files=3 limits to log.1, log.2, log.3)
        rotated_4 = self.log_file.with_suffix(".log.4")
        assert not rotated_4.exists()

    def test_handles_missing_intermediate_files(self):
        """Should handle missing intermediate rotated files gracefully."""
        # Create only log.3 (gap in sequence)
        rotated_3 = self.log_file.with_suffix(".log.3")
        rotated_3.write_text("old content")

        # Create current log that exceeds limit
        self.log_file.write_text("x" * 100)

        result = rotate_log_if_needed(self.log_file, 50, 3)

        assert result
        # log.1 should exist (rotated from current)
        rotated_1 = self.log_file.with_suffix(".log.1")
        assert rotated_1.exists()
        # log.3 should be deleted (it was the oldest)
        assert not rotated_3.exists()

    def test_log_max_size_constant(self):
        """LOG_MAX_SIZE_BYTES should be 10MB."""
        assert LOG_MAX_SIZE_BYTES == 10 * 1024 * 1024

    def test_log_max_rotated_files_constant(self):
        """LOG_MAX_ROTATED_FILES should be 5."""
        assert LOG_MAX_ROTATED_FILES == 5


class TestIsPrMerged:
    """Test cases for is_pr_merged function (Issue #890)."""

    def test_merged_pr_returns_true(self):
        """is_pr_merged should return True for merged PRs."""
        from unittest.mock import MagicMock, patch

        from lib.github import is_pr_merged

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "true\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = is_pr_merged("123")

            assert result is True
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "repos/:owner/:repo/pulls/123" in " ".join(call_args)

    def test_open_pr_returns_false(self):
        """is_pr_merged should return False for open (unmerged) PRs."""
        from unittest.mock import MagicMock, patch

        from lib.github import is_pr_merged

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "false\n"

        with patch("subprocess.run", return_value=mock_result):
            result = is_pr_merged("456")

            assert result is False

    def test_api_error_returns_false(self):
        """is_pr_merged should return False on API error (fail open)."""
        from unittest.mock import MagicMock, patch

        from lib.github import is_pr_merged

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = is_pr_merged("789")

            assert result is False

    def test_exception_returns_false(self):
        """is_pr_merged should return False on exception (fail open)."""
        from unittest.mock import patch

        from lib.github import is_pr_merged

        with patch("subprocess.run", side_effect=Exception("Network error")):
            result = is_pr_merged("999")

            assert result is False

    def test_empty_response_returns_false(self):
        """is_pr_merged should return False for empty response."""
        from unittest.mock import MagicMock, patch

        from lib.github import is_pr_merged

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = is_pr_merged("111")

            assert result is False


class TestGetPrMergeStatus:
    """Test cases for get_pr_merge_status function (Issue #2377)."""

    def test_successful_status_fetch(self):
        """get_pr_merge_status should return correct status for a valid PR."""
        import json
        from unittest.mock import MagicMock, patch

        from lib.github import get_pr_merge_status

        mock_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [
                {"conclusion": "SUCCESS", "status": None},
                {"conclusion": "SKIPPED", "status": None},
            ],
            "reviews": [
                {"state": "APPROVED", "author": {"login": "reviewer1"}},
            ],
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_data)

        with patch("subprocess.run", return_value=mock_result):
            result = get_pr_merge_status("123")

            assert result["mergeable"] is True
            assert result["merge_state_status"] == "CLEAN"
            assert result["review_decision"] == "APPROVED"
            assert result["status_check_status"] == "SUCCESS"
            assert result["current_approvals"] == 1
            assert result["blocking_reasons"] == []

    def test_ci_failure_status(self):
        """get_pr_merge_status should detect CI failure."""
        import json
        from unittest.mock import MagicMock, patch

        from lib.github import get_pr_merge_status

        mock_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "BLOCKED",
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [
                {"conclusion": "FAILURE", "status": None},
            ],
            "reviews": [],
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_data)

        with patch("subprocess.run", return_value=mock_result):
            result = get_pr_merge_status("123")

            assert result["status_check_status"] == "FAILURE"
            assert "CIチェックが失敗しています" in result["blocking_reasons"]
            assert any("gh pr checks 123" in a for a in result["suggested_actions"])

    def test_pending_ci_status(self):
        """get_pr_merge_status should detect pending CI."""
        import json
        from unittest.mock import MagicMock, patch

        from lib.github import get_pr_merge_status

        mock_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "BLOCKED",
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [
                {"conclusion": None, "status": "IN_PROGRESS"},
            ],
            "reviews": [],
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_data)

        with patch("subprocess.run", return_value=mock_result):
            result = get_pr_merge_status("123")

            assert result["status_check_status"] == "PENDING"
            assert "CIチェックが実行中です" in result["blocking_reasons"]

    def test_behind_state(self):
        """get_pr_merge_status should detect BEHIND state."""
        import json
        from unittest.mock import MagicMock, patch

        from lib.github import get_pr_merge_status

        mock_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "BEHIND",
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [],
            "reviews": [],
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_data)

        with patch("subprocess.run", return_value=mock_result):
            result = get_pr_merge_status("123")

            assert result["merge_state_status"] == "BEHIND"
            assert "mainブランチより遅れています（BEHIND）" in result["blocking_reasons"]
            assert any("rebase" in a for a in result["suggested_actions"])

    def test_review_required(self):
        """get_pr_merge_status should detect review required."""
        import json
        from unittest.mock import MagicMock, patch

        from lib.github import get_pr_merge_status

        mock_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "BLOCKED",
            "reviewDecision": "REVIEW_REQUIRED",
            "statusCheckRollup": [{"conclusion": "SUCCESS", "status": None}],
            "reviews": [],
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_data)

        with patch("subprocess.run", return_value=mock_result):
            result = get_pr_merge_status("123")

            assert result["review_decision"] == "REVIEW_REQUIRED"
            assert "レビュー承認が必要ですが、承認されていません" in result["blocking_reasons"]

    def test_api_error_returns_defaults(self):
        """get_pr_merge_status should return default values on API error."""
        from unittest.mock import MagicMock, patch

        from lib.github import get_pr_merge_status

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = get_pr_merge_status("999")

            assert result["mergeable"] is None
            assert result["merge_state_status"] == "UNKNOWN"
            assert result["status_check_status"] == "UNKNOWN"
            assert result["blocking_reasons"] == []

    def test_exception_returns_defaults(self):
        """get_pr_merge_status should return default values on exception."""
        from unittest.mock import patch

        from lib.github import get_pr_merge_status

        with patch("subprocess.run", side_effect=Exception("Network error")):
            result = get_pr_merge_status("999")

            assert result["mergeable"] is None
            assert result["merge_state_status"] == "UNKNOWN"

    def test_no_checks_returns_none_status(self):
        """get_pr_merge_status should return NONE for status_check_status when no checks."""
        import json
        from unittest.mock import MagicMock, patch

        from lib.github import get_pr_merge_status

        mock_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [],
            "reviews": [],
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_data)

        with patch("subprocess.run", return_value=mock_result):
            result = get_pr_merge_status("123")

            assert result["status_check_status"] == "NONE"

    def test_unique_reviewer_counting(self):
        """get_pr_merge_status should count unique reviewers only."""
        import json
        from unittest.mock import MagicMock, patch

        from lib.github import get_pr_merge_status

        mock_data = {
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [],
            "reviews": [
                {"state": "CHANGES_REQUESTED", "author": {"login": "reviewer1"}},
                {"state": "APPROVED", "author": {"login": "reviewer1"}},
                {"state": "APPROVED", "author": {"login": "reviewer2"}},
            ],
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_data)

        with patch("subprocess.run", return_value=mock_result):
            result = get_pr_merge_status("123")

            # Should count 2 unique approvers (reviewer1 and reviewer2)
            assert result["current_approvals"] == 2


class TestIsSkipEnvEnabled:
    """Tests for is_skip_env_enabled function (Issue #956)."""

    def test_returns_true_for_1(self):
        """Should return True for '1'."""
        assert is_skip_env_enabled("1") is True

    def test_returns_true_for_true_lowercase(self):
        """Should return True for 'true'."""
        assert is_skip_env_enabled("true") is True

    def test_returns_true_for_true_titlecase(self):
        """Should return True for 'True'."""
        assert is_skip_env_enabled("True") is True

    def test_returns_false_for_0(self):
        """Should return False for '0'."""
        assert is_skip_env_enabled("0") is False

    def test_returns_false_for_false_lowercase(self):
        """Should return False for 'false'."""
        assert is_skip_env_enabled("false") is False

    def test_returns_false_for_false_titlecase(self):
        """Should return False for 'False'."""
        assert is_skip_env_enabled("False") is False

    def test_returns_false_for_empty_string(self):
        """Should return False for empty string."""
        assert is_skip_env_enabled("") is False

    def test_returns_false_for_none(self):
        """Should return False for None."""
        assert is_skip_env_enabled(None) is False

    def test_returns_false_for_random_string(self):
        """Should return False for random string."""
        assert is_skip_env_enabled("yes") is False
        assert is_skip_env_enabled("on") is False
        assert is_skip_env_enabled("enabled") is False

    def test_returns_false_for_true_uppercase(self):
        """Should return False for 'TRUE' (not in accepted list)."""
        assert is_skip_env_enabled("TRUE") is False


class TestExtractInlineSkipEnv:
    """Tests for extract_inline_skip_env function (Issue #956)."""

    def test_extracts_unquoted_value(self):
        """Should extract unquoted value."""
        assert extract_inline_skip_env("SKIP_PLAN=1 git worktree add", "SKIP_PLAN") == "1"
        assert extract_inline_skip_env("SKIP_PLAN=true git worktree", "SKIP_PLAN") == "true"

    def test_extracts_double_quoted_value(self):
        """Should extract and unquote double-quoted value."""
        assert extract_inline_skip_env('SKIP_PLAN="1" git worktree add', "SKIP_PLAN") == "1"
        assert extract_inline_skip_env('SKIP_PLAN="true" git worktree', "SKIP_PLAN") == "true"

    def test_extracts_single_quoted_value(self):
        """Should extract and unquote single-quoted value."""
        assert extract_inline_skip_env("SKIP_PLAN='1' git worktree add", "SKIP_PLAN") == "1"
        assert extract_inline_skip_env("SKIP_PLAN='true' git worktree", "SKIP_PLAN") == "true"

    def test_returns_none_when_inside_quotes(self):
        """Should return None when env var is inside quoted strings."""
        assert extract_inline_skip_env("echo 'SKIP_PLAN=1' && git worktree", "SKIP_PLAN") is None
        assert extract_inline_skip_env('echo "SKIP_PLAN=1" && git worktree', "SKIP_PLAN") is None

    def test_returns_none_when_not_present(self):
        """Should return None when env var is not present."""
        assert extract_inline_skip_env("git worktree add", "SKIP_PLAN") is None

    def test_returns_none_for_different_env_var(self):
        """Should return None when searching for different env var."""
        assert extract_inline_skip_env("SKIP_PLAN=1 git worktree", "SKIP_OTHER") is None

    def test_handles_env_var_at_command_start(self):
        """Should handle env var at command start."""
        assert extract_inline_skip_env("SKIP_CODEX_REVIEW=1 git push", "SKIP_CODEX_REVIEW") == "1"

    def test_handles_env_var_in_middle(self):
        """Should handle env var in middle of command chain."""
        # Note: This is unusual but should still work
        result = extract_inline_skip_env("echo test && SKIP_PLAN=1 git worktree", "SKIP_PLAN")
        assert result == "1"


class TestIsCommentAlreadyLogged:
    """Tests for _is_comment_already_logged function (Issue #1263).

    Issue #1840: Updated to use session-specific log files.
    Now takes metrics_log_dir (directory) and searches for review-quality-*.jsonl files.
    """

    def test_returns_false_when_log_does_not_exist(self, tmp_path):
        """Should return False when log directory doesn't have any session files."""
        # Pass directory, not file - function will search for review-quality-*.jsonl
        assert _is_comment_already_logged("123", 456, tmp_path) is False

    def test_returns_false_for_empty_comment_id(self, tmp_path):
        """Should return False for empty comment_id."""
        # Pass directory, not file
        assert _is_comment_already_logged("", 456, tmp_path) is False

    def test_returns_true_when_comment_exists(self, tmp_path):
        """Should return True when comment_id exists in session log."""
        # Create session-specific log file
        log_file = tmp_path / "review-quality-test-session.jsonl"
        log_file.write_text(json.dumps({"comment_id": "123", "pr_number": 456}) + "\n")
        # Pass directory - function will find review-quality-*.jsonl files
        assert _is_comment_already_logged("123", 456, tmp_path) is True

    def test_returns_false_for_different_pr(self, tmp_path):
        """Should return False when PR number differs."""
        # Create session-specific log file
        log_file = tmp_path / "review-quality-test-session.jsonl"
        log_file.write_text(json.dumps({"comment_id": "123", "pr_number": 456}) + "\n")
        # Different PR number
        assert _is_comment_already_logged("123", 789, tmp_path) is False

    def test_handles_string_int_pr_number_comparison(self, tmp_path):
        """Should handle int vs str PR number comparison."""
        # Create session-specific log file
        log_file = tmp_path / "review-quality-test-session.jsonl"
        # Stored as int
        log_file.write_text(json.dumps({"comment_id": "123", "pr_number": 456}) + "\n")
        # Query as string - should still match
        assert _is_comment_already_logged("123", "456", tmp_path) is True

    def test_returns_false_when_pr_number_is_none(self, tmp_path):
        """Should return False when pr_number is None to avoid false positives."""
        # Create session-specific log file
        log_file = tmp_path / "review-quality-test-session.jsonl"
        # Stored with None as pr_number
        log_file.write_text(json.dumps({"comment_id": "123", "pr_number": None}) + "\n")
        # Query with None - should NOT match (to avoid false positives)
        assert _is_comment_already_logged("123", None, tmp_path) is False


class TestLogReviewCommentDuplicatePrevention:
    """Tests for log_review_comment duplicate prevention (Issue #1263).

    Issue #1840: Updated to use session-specific log files.
    Issue #2496: Updated to use session_id parameter instead of global state.
    Security: Use UUID format for session_id validation.
    """

    # Use UUID format for session_id (Issue #2496: security validation)
    TEST_SESSION_ID = "12345678-0000-0000-0000-000000000001"

    def _get_session_log_file(self, tmp_path):
        """Get the session-specific log file path."""
        return tmp_path / f"review-quality-{self.TEST_SESSION_ID}.jsonl"

    def test_skips_duplicate_initial_recording(self, tmp_path):
        """Should skip logging when comment already exists (initial recording)."""
        # Create session-specific log file with existing entry
        log_file = self._get_session_log_file(tmp_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(json.dumps({"comment_id": "123", "pr_number": 456}) + "\n")

        # Try to log same comment (no resolution = initial recording)
        # Issue #2496: Pass session_id explicitly instead of mocking global state
        log_review_comment(
            pr_number=456,
            comment_id="123",
            reviewer="copilot",
            metrics_log_dir=tmp_path,
            session_id=self.TEST_SESSION_ID,
        )

        # Should still have only 1 line
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_allows_resolution_update(self, tmp_path):
        """Should allow resolution update for existing comment."""
        # Create session-specific log file with existing entry
        log_file = self._get_session_log_file(tmp_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(json.dumps({"comment_id": "123", "pr_number": 456}) + "\n")

        # Log with resolution (should NOT be skipped)
        # Issue #2496: Pass session_id explicitly instead of mocking global state
        log_review_comment(
            pr_number=456,
            comment_id="123",
            reviewer="copilot",
            resolution="accepted",
            metrics_log_dir=tmp_path,
            session_id=self.TEST_SESSION_ID,
        )

        # Should have 2 lines now
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        # Second line should have resolution
        second = json.loads(lines[1])
        assert second["resolution"] == "accepted"


class TestNormalizeCommentText:
    """Tests for normalize_comment_text function (Issue #1389)."""

    def test_empty_text(self):
        """Empty text should return empty string."""
        from lib.review import normalize_comment_text

        assert normalize_comment_text("") == ""
        assert normalize_comment_text(None) == ""

    def test_lowercase(self):
        """Text should be lowercased."""
        from lib.review import normalize_comment_text

        assert normalize_comment_text("HELLO WORLD") == "hello world"

    def test_whitespace_normalization(self):
        """Multiple whitespaces should be collapsed."""
        from lib.review import normalize_comment_text

        result = normalize_comment_text("hello   world\n\ntest")
        assert result == "hello world test"

    def test_markdown_removal(self):
        """Markdown formatting should be removed."""
        from lib.review import normalize_comment_text

        result = normalize_comment_text("**bold** and `code`")
        assert result == "bold and code"

    def test_punctuation_removal(self):
        """Common punctuation should be removed."""
        from lib.review import normalize_comment_text

        result = normalize_comment_text("Hello, world! How are you?")
        assert result == "hello world how are you"


class TestCalculateCommentSimilarity:
    """Tests for calculate_comment_similarity function (Issue #1389)."""

    def test_identical_text(self):
        """Identical texts should have similarity 1.0."""
        from lib.review import calculate_comment_similarity

        assert calculate_comment_similarity("hello world", "hello world") == 1.0

    def test_empty_text(self):
        """Empty texts should have similarity 0.0."""
        from lib.review import calculate_comment_similarity

        assert calculate_comment_similarity("", "hello") == 0.0
        assert calculate_comment_similarity("hello", "") == 0.0
        assert calculate_comment_similarity("", "") == 0.0

    def test_similar_text(self):
        """Similar texts should have high similarity."""
        from lib.review import calculate_comment_similarity

        # Same meaning with minor differences
        score = calculate_comment_similarity(
            "Please add docstring to this function",
            "please add docstring to this function",
        )
        assert score > 0.9

    def test_different_text(self):
        """Different texts should have low similarity."""
        from lib.review import calculate_comment_similarity

        score = calculate_comment_similarity(
            "Add error handling",
            "Remove unused imports",
        )
        assert score < 0.5

    def test_whitespace_and_punctuation_normalization(self):
        """Texts differing only in whitespace/punctuation should be similar."""
        from lib.review import calculate_comment_similarity

        score = calculate_comment_similarity(
            "Add docstring to function!",
            "add   docstring  to function",
        )
        assert score > 0.9

    def test_punctuation_only_text_returns_zero_similarity(self):
        """Punctuation-only text normalizes to empty, should return 0.0."""
        from lib.review import calculate_comment_similarity

        # Punctuation-only strings normalize to empty
        assert calculate_comment_similarity("!!!", "???") == 0.0
        assert calculate_comment_similarity("!!!", "Add docstring") == 0.0
        assert calculate_comment_similarity("Add docstring", "...") == 0.0


class TestFindSimilarComments:
    """Tests for find_similar_comments function (Issue #1389)."""

    def test_empty_input(self):
        """Empty new comment should return empty list."""
        from lib.review import find_similar_comments

        result = find_similar_comments({"body": ""}, [{"body": "test"}])
        assert result == []

    def test_no_similar_comments(self):
        """Should return empty list when no similar comments found."""
        from lib.review import find_similar_comments

        new = {"body": "Add error handling", "reviewer": "copilot"}
        previous = [
            {"body": "Remove unused imports", "reviewer": "copilot"},
            {"body": "Fix typo in variable name", "reviewer": "copilot"},
        ]
        result = find_similar_comments(new, previous)
        assert result == []

    def test_find_similar_comment(self):
        """Should find similar comment above threshold."""
        from lib.review import find_similar_comments

        new = {"body": "Add docstring to function", "reviewer": "copilot"}
        previous = [
            {"body": "add docstring to this function!", "reviewer": "copilot"},
            {"body": "Remove unused imports", "reviewer": "copilot"},
        ]
        result = find_similar_comments(new, previous, threshold=0.8)
        assert len(result) == 1
        assert "similarity_score" in result[0]
        assert result[0]["similarity_score"] >= 0.8

    def test_filter_by_reviewer(self):
        """Should only match comments from same reviewer."""
        from lib.review import find_similar_comments

        new = {"body": "Add docstring", "reviewer": "copilot"}
        previous = [
            {"body": "Add docstring", "reviewer": "codex"},  # Different reviewer
        ]
        result = find_similar_comments(new, previous)
        assert result == []

    def test_filter_by_path(self):
        """Should match comments with same or similar file path."""
        from lib.review import find_similar_comments

        new = {"body": "Add docstring", "reviewer": "copilot", "path": "src/utils.py"}
        previous = [
            {
                "body": "Add docstring",
                "reviewer": "copilot",
                "path": "src/utils.py",
            },  # Same path
            {
                "body": "Add docstring",
                "reviewer": "copilot",
                "path": "tests/test_other.py",
            },  # Different path
        ]
        result = find_similar_comments(new, previous)
        assert len(result) == 1
        assert result[0]["path"] == "src/utils.py"

    def test_sorted_by_similarity(self):
        """Results should be sorted by similarity score descending."""
        from lib.review import find_similar_comments

        new = {"body": "Add docstring to this function", "reviewer": "copilot"}
        previous = [
            {"body": "add docstring function", "reviewer": "copilot"},  # Less similar
            {"body": "Add docstring to this function!", "reviewer": "copilot"},  # Very similar
            {"body": "add docstring to function", "reviewer": "copilot"},  # Medium similar
        ]
        result = find_similar_comments(new, previous, threshold=0.7)
        assert len(result) >= 1
        # First result should have highest similarity
        for i in range(len(result) - 1):
            assert result[i]["similarity_score"] >= result[i + 1]["similarity_score"]

    def test_none_reviewer_does_not_crash(self):
        """Should handle None reviewer without crashing (but not match)."""
        from lib.review import find_similar_comments

        # Test with None reviewer in new comment - should NOT match (requires same reviewer)
        new = {"body": "Add docstring", "reviewer": None}
        previous = [{"body": "Add docstring", "reviewer": "copilot"}]
        result = find_similar_comments(new, previous)
        assert len(result) == 0  # Missing reviewer = non-match

        # Test with None reviewer in previous comment - should NOT match
        new = {"body": "Add docstring", "reviewer": "copilot"}
        previous = [{"body": "Add docstring", "reviewer": None}]
        result = find_similar_comments(new, previous)
        assert len(result) == 0  # Missing reviewer = non-match

        # Test with both None - should NOT match (can't verify same reviewer)
        new = {"body": "Add docstring", "reviewer": None}
        previous = [{"body": "Add docstring", "reviewer": None}]
        result = find_similar_comments(new, previous)
        assert len(result) == 0  # Missing reviewer = non-match


class TestGetMainRepoFromWorktree:
    """Tests for _get_main_repo_from_worktree function (Issue #2505).

    This function resolves worktree paths to their main repository path,
    ensuring session logs are stored in the main repository's .claude/logs/
    directory and not lost when worktrees are deleted.
    """

    def setup_method(self):
        """Set up test fixtures."""
        import shutil
        import tempfile

        self.temp_dir = Path(tempfile.mkdtemp())
        self._shutil = shutil

    def teardown_method(self):
        """Clean up test fixtures."""
        self._shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_none_for_main_repo(self):
        """Should return None when .git is a directory (main repository)."""
        from common import _get_main_repo_from_worktree

        # Create a main repo structure (git directory)
        git_dir = self.temp_dir / ".git"
        git_dir.mkdir()

        result = _get_main_repo_from_worktree(self.temp_dir)
        assert result is None

    def test_returns_none_for_no_git(self):
        """Should return None when no .git exists."""
        from common import _get_main_repo_from_worktree

        result = _get_main_repo_from_worktree(self.temp_dir)
        assert result is None

    def test_returns_main_repo_for_worktree(self):
        """Should return main repo path for a worktree."""
        from common import _get_main_repo_from_worktree

        # Create main repo structure
        main_repo = self.temp_dir / "main_repo"
        main_git = main_repo / ".git" / "worktrees" / "issue-123"
        main_git.mkdir(parents=True)

        # Create worktree structure
        worktree = self.temp_dir / "worktree"
        worktree.mkdir()
        git_file = worktree / ".git"
        git_file.write_text(f"gitdir: {main_git}")

        result = _get_main_repo_from_worktree(worktree)
        assert result == main_repo

    def test_returns_none_for_invalid_gitdir_format(self):
        """Should return None when .git file has invalid format."""
        from common import _get_main_repo_from_worktree

        # Create worktree with invalid .git file
        worktree = self.temp_dir / "worktree"
        worktree.mkdir()
        git_file = worktree / ".git"
        git_file.write_text("invalid content without gitdir prefix")

        result = _get_main_repo_from_worktree(worktree)
        assert result is None

    def test_returns_none_for_non_worktree_gitdir(self):
        """Should return None when gitdir doesn't point to .git/worktrees/xxx."""
        from common import _get_main_repo_from_worktree

        # Create worktree pointing to non-worktree path
        worktree = self.temp_dir / "worktree"
        worktree.mkdir()
        git_file = worktree / ".git"
        git_file.write_text("gitdir: /some/random/path")

        result = _get_main_repo_from_worktree(worktree)
        assert result is None

    def test_handles_whitespace_in_gitdir(self):
        """Should handle whitespace around gitdir path."""
        from common import _get_main_repo_from_worktree

        # Create main repo structure
        main_repo = self.temp_dir / "main_repo"
        main_git = main_repo / ".git" / "worktrees" / "issue-456"
        main_git.mkdir(parents=True)

        # Create worktree with whitespace in .git file
        worktree = self.temp_dir / "worktree"
        worktree.mkdir()
        git_file = worktree / ".git"
        git_file.write_text(f"gitdir:  {main_git}  \n")

        result = _get_main_repo_from_worktree(worktree)
        assert result == main_repo

    def test_handles_relative_gitdir(self):
        """Should resolve relative gitdir paths (default for git worktree add)."""
        from common import _get_main_repo_from_worktree

        # Create structure mimicking default git worktree layout:
        # main_repo/
        #   .git/
        #     worktrees/
        #       issue-789/
        #   .worktrees/
        #     issue-789/
        #       .git (file: "gitdir: ../../.git/worktrees/issue-789")
        main_repo = self.temp_dir / "main_repo"
        main_git = main_repo / ".git" / "worktrees" / "issue-789"
        main_git.mkdir(parents=True)

        worktree = main_repo / ".worktrees" / "issue-789"
        worktree.mkdir(parents=True)
        git_file = worktree / ".git"
        # This is the default format: relative path from worktree to main .git
        git_file.write_text("gitdir: ../../.git/worktrees/issue-789")

        result = _get_main_repo_from_worktree(worktree)
        assert result is not None
        assert result.resolve() == main_repo.resolve()

    def test_returns_none_when_gitdir_path_missing(self):
        """Should return None when gitdir_path doesn't actually exist (Issue #2509).

        This tests the gitdir_path.exists() check added for path traversal prevention.
        Even if the path structure matches .git/worktrees/xxx, we should verify
        the path actually exists on disk.
        """
        from common import _get_main_repo_from_worktree

        # Create a worktree with .git file pointing to non-existent path
        # The path structure matches .git/worktrees/xxx but doesn't actually exist
        main_repo = self.temp_dir / "main_repo"
        main_repo.mkdir()
        fake_gitdir = main_repo / ".git" / "worktrees" / "issue-fake"
        # Note: We intentionally do NOT create this path

        worktree = self.temp_dir / "worktree"
        worktree.mkdir()
        git_file = worktree / ".git"
        git_file.write_text(f"gitdir: {fake_gitdir}")

        result = _get_main_repo_from_worktree(worktree)
        # Should return None because gitdir_path doesn't exist
        assert result is None


class TestGetProjectDirWorktreeResolution:
    """Tests for _get_project_dir worktree resolution (Issue #2505).

    Verifies that _get_project_dir returns the main repository path
    when running inside a worktree, ensuring session logs are stored
    in a persistent location.
    """

    def setup_method(self):
        """Set up test fixtures."""
        import os as os_module
        import shutil
        import tempfile

        self.temp_dir = Path(tempfile.mkdtemp())
        self._shutil = shutil
        self._os = os_module
        self._original_env = os_module.environ.copy()

    def teardown_method(self):
        """Clean up test fixtures."""
        self._os.environ.clear()
        self._os.environ.update(self._original_env)
        self._shutil.rmtree(self.temp_dir, ignore_errors=True)
        # Reload common module to restore SESSION_DIR and other module-level constants
        # that may have been affected by test mocking
        import importlib

        import common

        importlib.reload(common)

    def test_resolves_worktree_env_to_main_repo(self):
        """Should resolve worktree CLAUDE_PROJECT_DIR to main repo."""
        from unittest.mock import patch

        # Create main repo structure
        main_repo = self.temp_dir / "main_repo"
        main_git = main_repo / ".git" / "worktrees" / "issue-789"
        main_git.mkdir(parents=True)

        # Create worktree structure
        worktree = self.temp_dir / "worktree"
        worktree.mkdir()
        git_file = worktree / ".git"
        git_file.write_text(f"gitdir: {main_git}")

        # Set CLAUDE_PROJECT_DIR to worktree
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(worktree)}):
            # Import fresh to get new _PROJECT_DIR value
            import importlib

            import common

            importlib.reload(common)

            # _get_project_dir should resolve to main repo
            result = common._get_project_dir()
            assert result == main_repo

    def test_returns_main_repo_when_cwd_is_worktree(self):
        """Should resolve cwd worktree to main repo when no env var."""
        from unittest.mock import patch

        # Create main repo structure
        main_repo = self.temp_dir / "main_repo"
        main_git = main_repo / ".git" / "worktrees" / "issue-999"
        main_git.mkdir(parents=True)

        # Create worktree structure
        worktree = self.temp_dir / "worktree"
        worktree.mkdir()
        git_file = worktree / ".git"
        git_file.write_text(f"gitdir: {main_git}")

        # No CLAUDE_PROJECT_DIR, cwd is worktree
        with patch.dict("os.environ", {}, clear=True):
            with patch("pathlib.Path.cwd", return_value=worktree):
                import importlib

                import common

                importlib.reload(common)

                result = common._get_project_dir()
                assert result == main_repo
