#!/usr/bin/env python3
"""Tests for api-operation-logger.py hook."""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "api-operation-logger.py"


def load_module():
    """Load the hook module for testing."""
    parent_dir = str(Path(__file__).parent.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    spec = importlib.util.spec_from_file_location("api_operation_logger", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestApiOperationLogger:
    """Tests for API operation logger hook."""

    def test_continue_gh_pr_command(self):
        """Should continue after logging gh pr commands."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title 'test'"},
            "tool_result": {
                "exit_code": 0,
                "stdout": "https://github.com/owner/repo/pull/123",
                "stderr": "",
            },
        }
        result = run_hook(input_data)
        assert result.get("continue") is True

    def test_continue_git_push_command(self):
        """Should continue after logging git push commands."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "tool_result": {
                "exit_code": 0,
                "stdout": "Everything up-to-date",
                "stderr": "",
            },
        }
        result = run_hook(input_data)
        assert result.get("continue") is True

    def test_continue_npm_run_command(self):
        """Should continue after logging npm run commands."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Bash",
            "tool_input": {"command": "npm run build"},
            "tool_result": {
                "exit_code": 0,
                "stdout": "Build completed",
                "stderr": "",
            },
        }
        result = run_hook(input_data)
        assert result.get("continue") is True

    def test_continue_non_target_command(self):
        """Should continue for non-target commands without logging."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "tool_result": {
                "exit_code": 0,
                "stdout": "file1 file2",
                "stderr": "",
            },
        }
        result = run_hook(input_data)
        assert result.get("continue") is True

    def test_continue_non_bash_tool(self):
        """Should continue for non-Bash tools."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/path"},
            "tool_result": {"content": "file content"},
        }
        result = run_hook(input_data)
        assert result.get("continue") is True

    def test_continue_empty_input(self):
        """Should continue when input is empty."""
        result = run_hook({})
        assert result.get("continue") is True

    def test_continue_failed_command(self):
        """Should continue and log failed commands."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_result": {
                "exit_code": 1,
                "stdout": "",
                "stderr": "no pull request found",
            },
        }
        result = run_hook(input_data)
        assert result.get("continue") is True


class TestDurationCalculation:
    """Tests for duration calculation."""

    def setup_method(self):
        self.module = load_module()

    def test_calculate_duration_with_valid_start_time(self):
        """Should calculate duration correctly."""
        from datetime import UTC, datetime, timedelta

        start_time = datetime.now(UTC) - timedelta(seconds=5)
        duration = self.module.calculate_duration_ms(start_time)

        # Duration should be roughly 5000ms (allowing for some variance)
        assert duration is not None
        assert 4500 < duration < 6000

    def test_calculate_duration_with_none(self):
        """Should return None when start_time is None."""
        duration = self.module.calculate_duration_ms(None)
        assert duration is None


class TestLogApiOperation:
    """Tests for log_api_operation function.

    Issue #1840: Updated to use session-specific log files.
    """

    def setup_method(self):
        self.module = load_module()
        # Use a temporary directory for logging
        self.temp_dir = tempfile.mkdtemp()
        self.test_session_id = "test-session"
        self.module.EXECUTION_LOG_DIR = Path(self.temp_dir)

    def teardown_method(self):
        # Cleanup
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _get_session_log_file(self) -> Path:
        """Get the session-specific log file path."""
        return Path(self.temp_dir) / f"api-operations-{self.test_session_id}.jsonl"

    def test_log_api_operation_writes_to_file(self):
        """Should write operation log to session-specific file."""
        self.module.log_api_operation(
            command_type="gh",
            operation="pr_create",
            command="gh pr create --title 'test'",
            duration_ms=1234,
            exit_code=0,
            success=True,
            parsed={"subcommand": "create"},
            result={"url": "https://github.com/owner/repo/pull/123"},
            session_id=self.test_session_id,
            branch="feat/test",
        )

        log_file = self._get_session_log_file()
        assert log_file.exists()

        with open(log_file) as f:
            lines = f.readlines()

        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["type"] == "gh"
        assert data["operation"] == "pr_create"
        assert data["duration_ms"] == 1234
        assert data["success"] is True
        assert data["session_id"] == self.test_session_id
        assert data["branch"] == "feat/test"

    def test_log_api_operation_without_duration(self):
        """Should handle missing duration."""
        self.module.log_api_operation(
            command_type="git",
            operation="push",
            command="git push",
            duration_ms=None,
            exit_code=0,
            success=True,
            parsed={},
            result={},
            session_id=self.test_session_id,
            branch=None,
        )

        with open(self._get_session_log_file()) as f:
            data = json.loads(f.read())

        assert "duration_ms" not in data

    def test_log_api_operation_with_error(self):
        """Should include stderr when operation fails (Issue #1269)."""
        self.module.log_api_operation(
            command_type="gh",
            operation="api",
            command="gh api graphql",
            duration_ms=500,
            exit_code=1,
            success=False,
            parsed={},
            result={},
            session_id=self.test_session_id,
            branch="main",
            stderr="API rate limit exceeded",
        )

        with open(self._get_session_log_file()) as f:
            data = json.loads(f.read())

        assert data["success"] is False
        assert data["exit_code"] == 1
        assert data["error"] == "API rate limit exceeded"

    def test_log_api_operation_with_rate_limit(self):
        """Should include rate_limit_detected flag (Issue #1269)."""
        self.module.log_api_operation(
            command_type="gh",
            operation="api",
            command="gh api graphql",
            duration_ms=500,
            exit_code=1,
            success=False,
            parsed={},
            result={},
            session_id=self.test_session_id,
            branch="main",
            stderr="API rate limit exceeded",
            rate_limit_detected=True,
        )

        with open(self._get_session_log_file()) as f:
            data = json.loads(f.read())

        assert data["rate_limit_detected"] is True

    def test_log_api_operation_truncates_long_stderr(self):
        """Should truncate stderr to 1000 characters (Issue #1269).

        Note: Python 3 string slicing is character-based, not byte-based.
        """
        long_stderr = "x" * 2000  # 2000 characters

        self.module.log_api_operation(
            command_type="gh",
            operation="api",
            command="gh api graphql",
            duration_ms=500,
            exit_code=1,
            success=False,
            parsed={},
            result={},
            session_id=self.test_session_id,
            branch="main",
            stderr=long_stderr,
        )

        with open(self._get_session_log_file()) as f:
            data = json.loads(f.read())

        assert len(data["error"]) == 1000

    def test_log_api_operation_no_error_on_success(self):
        """Should not include error field when operation succeeds."""
        self.module.log_api_operation(
            command_type="gh",
            operation="pr_create",
            command="gh pr create",
            duration_ms=500,
            exit_code=0,
            success=True,
            parsed={},
            result={},
            session_id=self.test_session_id,
            branch="main",
            stderr=None,  # No stderr for success
        )

        with open(self._get_session_log_file()) as f:
            data = json.loads(f.read())

        assert "error" not in data

    def test_log_api_operation_no_rate_limit_flag_when_false(self):
        """Should not include rate_limit_detected field when False (Issue #1269)."""
        self.module.log_api_operation(
            command_type="gh",
            operation="api",
            command="gh api graphql",
            duration_ms=500,
            exit_code=1,
            success=False,
            parsed={},
            result={},
            session_id=self.test_session_id,
            branch="main",
            stderr="permission denied",
            rate_limit_detected=False,  # Not a rate limit error
        )

        with open(self._get_session_log_file()) as f:
            data = json.loads(f.read())

        assert "rate_limit_detected" not in data
        assert data["error"] == "permission denied"


class TestRateLimitDetection:
    """Tests for rate limit detection (Issue #1269)."""

    def setup_method(self):
        self.module = load_module()

    def test_detect_rate_limit_in_stderr(self):
        """Should detect rate limit in stderr."""
        assert self.module.detect_rate_limit("", "API rate limit exceeded") is True

    def test_detect_rate_limit_in_stdout(self):
        """Should detect rate limit in stdout."""
        assert self.module.detect_rate_limit("rate limit exceeded", "") is True

    def test_detect_secondary_rate_limit(self):
        """Should detect secondary rate limit."""
        assert self.module.detect_rate_limit("", "You have exceeded a secondary rate limit") is True

    def test_detect_too_many_requests(self):
        """Should detect too many requests error."""
        assert self.module.detect_rate_limit("", "too many requests") is True

    def test_detect_abuse_detection(self):
        """Should detect abuse detection mechanism."""
        assert self.module.detect_rate_limit("", "abuse detection mechanism triggered") is True

    def test_no_rate_limit_normal_error(self):
        """Should not detect rate limit for normal errors."""
        assert self.module.detect_rate_limit("", "no pull request found") is False

    def test_no_rate_limit_empty(self):
        """Should not detect rate limit for empty output."""
        assert self.module.detect_rate_limit("", "") is False

    def test_case_insensitive(self):
        """Should detect rate limit case-insensitively."""
        assert self.module.detect_rate_limit("", "RATE LIMIT EXCEEDED") is True
        assert self.module.detect_rate_limit("", "Rate Limit Exceeded") is True
        assert self.module.detect_rate_limit("", "RaTe LiMiT ExCeEdEd") is True  # Mixed case


class TestRateLimitDetectionIssue1564:
    """Tests for improved rate limit detection (Issue #1564, #1581).

    These tests verify that false positives from URLs/documentation are avoided
    while still detecting errors on the same line as URLs.
    """

    def setup_method(self):
        self.module = load_module()

    def test_url_only_no_error(self):
        """Should not detect rate limit when only URL contains rate limit text.

        Issue #1581: URLs are removed before pattern matching, so 'rate-limit'
        in URL paths should not trigger false positives.
        """
        # URL containing "rate limit" in path - should NOT match
        url_line = "See https://docs.github.com/rate-limiting for more info"
        assert self.module.detect_rate_limit(url_line, "") is False

        # HTTPS URL with rate limit in path
        url_stderr = "Documentation: https://example.com/rate-limit-guide"
        assert self.module.detect_rate_limit("", url_stderr) is False

        # HTTP URL
        http_url = "http://example.com/rate_limit_info"
        assert self.module.detect_rate_limit(http_url, "") is False

    def test_same_line_url_and_error(self):
        """Should detect rate limit when URL and error are on the same line.

        Issue #1581: This is the key improvement - URL is removed from the line
        before pattern matching, allowing detection of errors on the same line.
        """
        # API error with URL prefix - should detect "rate limit exceeded"
        api_error = "GET https://api.github.com/graphql: 403 rate limit exceeded"
        assert self.module.detect_rate_limit(api_error, "") is True

        # gh CLI error format
        gh_error = "gh: https://api.github.com/: secondary rate limit"
        assert self.module.detect_rate_limit("", gh_error) is True

        # Error with trailing URL
        trailing_url = "rate limit exceeded - see https://docs.github.com/rate-limiting"
        assert self.module.detect_rate_limit(trailing_url, "") is True

    def test_detect_rate_limit_in_non_url_line(self):
        """Should detect rate limit in lines without URLs."""
        # Error message without URL
        assert self.module.detect_rate_limit("", "Error: rate limit exceeded") is True

    def test_multiline_with_url_and_error(self):
        """Should detect rate limit when URL and error are on different lines."""
        # URL on first line, error on second line
        output = "See https://docs.github.com/rate-limiting\nError: rate limit exceeded"
        assert self.module.detect_rate_limit(output, "") is True

        # Error on first line, URL on second line
        output2 = "Error: rate limit exceeded\nhttps://docs.github.com/rate-limiting"
        assert self.module.detect_rate_limit(output2, "") is True

    def test_no_partial_match_rate_limit_only(self):
        """Should not detect with just 'rate limit' without action verb.

        Issue #1564: Patterns now require context like 'exceeded' to avoid
        matching explanatory text.
        """
        # Just "rate limit" without action verb - should not match
        assert self.module.detect_rate_limit("", "The rate limit is 5000") is False
        assert self.module.detect_rate_limit("", "レート制限について説明します") is False

    def test_detect_graphql_error_code(self):
        """Should detect RATE_LIMITED GraphQL error code."""
        graphql_error = '{"errors": [{"type": "RATE_LIMITED"}]}'
        assert self.module.detect_rate_limit(graphql_error, "") is True

        # Underscore version
        assert self.module.detect_rate_limit("", "error: rate_limited") is True


class TestRemoveUrlsFromLine:
    """Tests for URL removal helper function (Issue #1581)."""

    def setup_method(self):
        self.module = load_module()

    def test_remove_https_url(self):
        """Should remove HTTPS URLs from line."""
        # Note: trailing colon is part of URL match since \S+ matches non-whitespace
        line = "GET https://api.github.com/graphql: 403 error"
        result = self.module._remove_urls_from_line(line)
        assert result == "GET  403 error"

    def test_remove_http_url(self):
        """Should remove HTTP URLs from line."""
        line = "See http://example.com/docs for info"
        result = self.module._remove_urls_from_line(line)
        assert result == "See  for info"

    def test_remove_multiple_urls(self):
        """Should remove multiple URLs from line."""
        line = "Check https://a.com and https://b.com for details"
        result = self.module._remove_urls_from_line(line)
        assert result == "Check  and  for details"

    def test_no_url_unchanged(self):
        """Should leave line unchanged if no URL present."""
        line = "Error: rate limit exceeded"
        result = self.module._remove_urls_from_line(line)
        assert result == line

    def test_url_with_path_and_query(self):
        """Should remove URLs with paths and query strings."""
        line = "Visit https://api.github.com/rate_limit?token=xxx for status"
        result = self.module._remove_urls_from_line(line)
        assert result == "Visit  for status"

    def test_uppercase_url_scheme(self):
        """Should remove URLs with uppercase schemes (HTTPS://, HTTP://)."""
        line = "GET HTTPS://API.GITHUB.COM/graphql: 403 error"
        result = self.module._remove_urls_from_line(line)
        assert result == "GET  403 error"

        # Mixed case should also work
        line2 = "See HTTP://Example.Com/docs for info"
        result2 = self.module._remove_urls_from_line(line2)
        assert result2 == "See  for info"


class TestTruncateStderrBytes:
    """Tests for byte-based stderr truncation (Issue #1564)."""

    def setup_method(self):
        self.module = load_module()

    def test_short_string_unchanged(self):
        """Should not truncate strings under limit."""
        short = "Error message"
        result = self.module.truncate_stderr_bytes(short)
        assert result == short

    def test_ascii_string_truncated(self):
        """Should truncate ASCII strings at byte limit."""
        long_ascii = "x" * 2000
        result = self.module.truncate_stderr_bytes(long_ascii, max_bytes=1000)
        assert len(result) == 1000
        assert len(result.encode("utf-8")) == 1000

    def test_multibyte_string_truncated(self):
        """Should truncate multibyte strings correctly.

        Japanese characters are 3 bytes each in UTF-8.
        """
        # 500 Japanese characters = 1500 bytes
        japanese = "あ" * 500
        result = self.module.truncate_stderr_bytes(japanese, max_bytes=1000)

        # Should be 333 characters (999 bytes) - truncated at byte boundary
        assert len(result.encode("utf-8")) <= 1000
        assert len(result) == 333  # 333 * 3 = 999 bytes

    def test_mixed_content_truncated(self):
        """Should handle mixed ASCII and multibyte content."""
        # 100 chars ASCII (100 bytes) + 400 Japanese (1200 bytes) = 1300 bytes
        mixed = "x" * 100 + "あ" * 400
        result = self.module.truncate_stderr_bytes(mixed, max_bytes=1000)

        assert len(result.encode("utf-8")) <= 1000
        # 100 ASCII + 300 Japanese = 400 chars, 100 + 900 = 1000 bytes
        assert len(result) == 400

    def test_emoji_truncation(self):
        """Should handle emoji (4-byte characters) correctly."""
        # Emoji are 4 bytes each
        emoji = "🎉" * 300  # 1200 bytes
        result = self.module.truncate_stderr_bytes(emoji, max_bytes=1000)

        assert len(result.encode("utf-8")) <= 1000
        # 250 emoji * 4 = 1000 bytes
        assert len(result) == 250

    def test_exact_boundary(self):
        """Should handle strings exactly at the limit."""
        exact = "x" * 1000
        result = self.module.truncate_stderr_bytes(exact)
        assert result == exact
        assert len(result) == 1000
