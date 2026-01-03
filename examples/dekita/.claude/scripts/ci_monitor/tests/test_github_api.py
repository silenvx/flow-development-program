"""Tests for ci_monitor.github_api module."""

from unittest.mock import MagicMock, patch

from ci_monitor.github_api import (
    _remove_urls_from_line,
    is_rate_limit_error,
    run_gh_command,
    run_gh_command_with_error,
)


class TestRunGhCommand:
    """Tests for run_gh_command function."""

    @patch("subprocess.run")
    def test_successful_command(self, mock_run):
        """Test successful command execution."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output\n",
            stderr="",
        )
        success, output = run_gh_command(["repo", "view"])
        assert success is True
        assert output == "output"

    @patch("subprocess.run")
    def test_failed_command(self, mock_run):
        """Test failed command execution."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error",
        )
        success, output = run_gh_command(["invalid"])
        assert success is False

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        """Test command timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        success, output = run_gh_command(["slow", "command"])
        assert success is False
        assert "timed out" in output.lower()


class TestRunGhCommandWithError:
    """Tests for run_gh_command_with_error function."""

    @patch("subprocess.run")
    def test_returns_stderr(self, mock_run):
        """Test that stderr is returned."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="out",
            stderr="error message",
        )
        success, stdout, stderr = run_gh_command_with_error(["cmd"])
        assert success is False
        assert stdout == "out"
        assert stderr == "error message"


class TestRemoveUrlsFromLine:
    """Tests for _remove_urls_from_line function."""

    def test_removes_https_url(self):
        """Test removing HTTPS URL."""
        line = "GET https://api.github.com/graphql: 403"
        result = _remove_urls_from_line(line)
        assert "https://" not in result
        assert "403" in result

    def test_removes_http_url(self):
        """Test removing HTTP URL."""
        line = "Error at http://example.com/api"
        result = _remove_urls_from_line(line)
        assert "http://" not in result

    def test_preserves_non_url_text(self):
        """Test that non-URL text is preserved."""
        line = "Rate limit exceeded for user"
        result = _remove_urls_from_line(line)
        assert result == line


class TestIsRateLimitError:
    """Tests for is_rate_limit_error function."""

    def test_detects_rate_limited(self):
        """Test detection of rate_limited error code."""
        output = '{"errors": [{"type": "rate_limited"}]}'
        assert is_rate_limit_error(output) is True

    def test_detects_rate_limit_exceeded(self):
        """Test detection of rate limit exceeded message."""
        output = "Error: rate limit exceeded"
        assert is_rate_limit_error(output) is True

    def test_detects_secondary_rate_limit(self):
        """Test detection of secondary rate limit."""
        stderr = "You have exceeded a secondary rate limit"
        assert is_rate_limit_error("", stderr) is True

    def test_no_rate_limit(self):
        """Test when there's no rate limit error."""
        output = "Permission denied"
        assert is_rate_limit_error(output) is False

    def test_url_does_not_cause_false_positive(self):
        """Test that URL containing 'rate' doesn't cause false positive."""
        output = "See https://docs.github.com/rate-limits for more info"
        # After URL removal, the remaining text shouldn't match patterns
        # Note: This tests the URL removal behavior
        assert is_rate_limit_error(output) is False


class TestGetRepoInfo:
    """Tests for get_repo_info function."""

    @patch("ci_monitor.github_api.run_gh_command")
    def test_successful_repo_info(self, mock_run):
        """Test successful repo info retrieval."""
        from ci_monitor.github_api import get_repo_info

        mock_run.return_value = (True, '{"owner": {"login": "testowner"}, "name": "testrepo"}')
        result = get_repo_info()
        assert result == ("testowner", "testrepo")

    @patch("ci_monitor.github_api.run_gh_command")
    def test_command_failure(self, mock_run):
        """Test when gh command fails."""
        from ci_monitor.github_api import get_repo_info

        mock_run.return_value = (False, "error")
        result = get_repo_info()
        assert result is None

    @patch("ci_monitor.github_api.run_gh_command")
    def test_invalid_json_response(self, mock_run):
        """Test handling of invalid JSON response."""
        from ci_monitor.github_api import get_repo_info

        mock_run.return_value = (True, "not valid json")
        result = get_repo_info()
        assert result is None

    @patch("ci_monitor.github_api.run_gh_command")
    def test_missing_owner(self, mock_run):
        """Test when owner field is missing."""
        from ci_monitor.github_api import get_repo_info

        mock_run.return_value = (True, '{"name": "testrepo"}')
        result = get_repo_info()
        assert result is None

    @patch("ci_monitor.github_api.run_gh_command")
    def test_missing_name(self, mock_run):
        """Test when name field is missing."""
        from ci_monitor.github_api import get_repo_info

        mock_run.return_value = (True, '{"owner": {"login": "testowner"}}')
        result = get_repo_info()
        assert result is None

    @patch("ci_monitor.github_api.run_gh_command")
    def test_empty_owner_login(self, mock_run):
        """Test when owner.login is empty."""
        from ci_monitor.github_api import get_repo_info

        mock_run.return_value = (True, '{"owner": {"login": ""}, "name": "testrepo"}')
        result = get_repo_info()
        assert result is None


class TestRunGraphqlWithFallback:
    """Tests for run_graphql_with_fallback function."""

    @patch("ci_monitor.github_api.run_gh_command_with_error")
    def test_successful_without_fallback(self, mock_run):
        """Test successful GraphQL command without using fallback."""
        from ci_monitor.github_api import run_graphql_with_fallback

        mock_run.return_value = (True, '{"data": {}}', "")
        success, output, used_fallback = run_graphql_with_fallback(["api", "graphql"])
        assert success is True
        assert used_fallback is False

    @patch("ci_monitor.github_api.run_gh_command_with_error")
    def test_fallback_success_sets_flag(self, mock_run):
        """Test that used_fallback is True when fallback succeeds."""
        from ci_monitor.github_api import run_graphql_with_fallback

        mock_run.return_value = (False, "rate limit exceeded", "")
        fallback = MagicMock(return_value=(True, "fallback result"))

        success, output, used_fallback = run_graphql_with_fallback(
            ["api", "graphql"], fallback_fn=fallback
        )
        assert success is True
        assert used_fallback is True
        assert output == "fallback result"

    @patch("ci_monitor.github_api.run_gh_command_with_error")
    def test_fallback_failure_still_sets_flag(self, mock_run):
        """Test that used_fallback is True even when fallback fails."""
        from ci_monitor.github_api import run_graphql_with_fallback

        mock_run.return_value = (False, "rate limit exceeded", "")
        fallback = MagicMock(return_value=(False, "fallback also failed"))

        success, output, used_fallback = run_graphql_with_fallback(
            ["api", "graphql"], fallback_fn=fallback
        )
        assert success is False
        # Important: used_fallback should be True even when fallback failed
        # This allows callers to know fallback was attempted for logging/metrics
        assert used_fallback is True
