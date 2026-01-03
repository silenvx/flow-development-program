"""Tests for multi-issue-guard.py."""

import importlib.util
import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Load the module with hyphen in name
HOOKS_DIR = Path(__file__).parent.parent
HOOK_PATH = HOOKS_DIR / "multi-issue-guard.py"

# Add hooks directory to path for lib imports
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

spec = importlib.util.spec_from_file_location("multi_issue_guard", HOOK_PATH)
multi_issue_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(multi_issue_guard)


class TestExtractAllIssueNumbers:
    """Test cases for extract_all_issue_numbers function."""

    def test_extracts_single_issue(self):
        """Single Issue number is correctly extracted."""
        assert multi_issue_guard.extract_all_issue_numbers("feat/issue-123-feature") == [123]

    def test_extracts_multiple_issues(self):
        """Multiple Issue numbers are correctly extracted."""
        # Multiple patterns in one string
        assert set(multi_issue_guard.extract_all_issue_numbers("issue-123-456")) == {123, 456}
        assert set(multi_issue_guard.extract_all_issue_numbers("feat/123-and-456-fix")) == {
            123,
            456,
        }

    def test_extracts_hash_patterns(self):
        """Hash patterns (#123) are correctly extracted."""
        assert multi_issue_guard.extract_all_issue_numbers("fix-#123") == [123]
        assert set(multi_issue_guard.extract_all_issue_numbers("#123-#456")) == {123, 456}

    def test_returns_empty_for_no_issues(self):
        """Returns empty list when no Issue numbers found."""
        assert multi_issue_guard.extract_all_issue_numbers("feat/add-feature") == []
        assert multi_issue_guard.extract_all_issue_numbers("main") == []

    def test_deduplicates_same_issue(self):
        """Same Issue number appearing multiple times is deduplicated."""
        # issue-123 and -123- match the same number
        result = multi_issue_guard.extract_all_issue_numbers("issue-123-feature-123")
        assert result == [123]


class TestExtractClosingIssueNumbers:
    """Test cases for extract_closing_issue_numbers function."""

    def test_extracts_single_close(self):
        """Extracts single Closes #xxx pattern."""
        assert multi_issue_guard.extract_closing_issue_numbers("Closes #123") == [123]

    def test_extracts_multiple_closes(self):
        """Extracts multiple closing keywords."""
        result = multi_issue_guard.extract_closing_issue_numbers("Closes #123\nFixes #456")
        assert set(result) == {123, 456}

    def test_case_insensitive(self):
        """Closing keywords are case-insensitive."""
        assert multi_issue_guard.extract_closing_issue_numbers("closes #123") == [123]
        assert multi_issue_guard.extract_closing_issue_numbers("CLOSES #123") == [123]
        assert multi_issue_guard.extract_closing_issue_numbers("ClOsEs #123") == [123]

    def test_returns_empty_for_no_keywords(self):
        """Returns empty list when no closing keywords found."""
        assert multi_issue_guard.extract_closing_issue_numbers("This is a PR description") == []
        assert multi_issue_guard.extract_closing_issue_numbers("Related to #123") == []


class TestCheckWorktreeCommand:
    """Test cases for check_worktree_command function."""

    def test_warns_multiple_issues_in_branch(self):
        """Warns when branch name contains multiple Issue numbers."""
        command = "git worktree add .worktrees/issue-123-456 -b feat/issue-123-456"
        result = multi_issue_guard.check_worktree_command(command)
        assert result["warn"] is True
        assert "123" in result["message"]
        assert "456" in result["message"]

    def test_no_warn_single_issue(self):
        """No warning when branch name contains single Issue number."""
        command = "git worktree add .worktrees/issue-123 -b feat/issue-123-feature"
        result = multi_issue_guard.check_worktree_command(command)
        assert result["warn"] is False

    def test_no_warn_no_worktree_command(self):
        """No warning for non-worktree commands."""
        result = multi_issue_guard.check_worktree_command("git status")
        assert result["warn"] is False

    def test_checks_path_for_issues(self):
        """Also checks worktree path for Issue numbers."""
        # Path has multiple issues but no -b flag
        command = "git worktree add .worktrees/issue-123-456"
        result = multi_issue_guard.check_worktree_command(command)
        assert result["warn"] is True


class TestCheckPrCommand:
    """Test cases for check_pr_command function."""

    def test_warns_multiple_closes(self):
        """Warns when PR body contains multiple Closes keywords."""
        command = 'gh pr create --title "Fix bugs" --body "Closes #123\nCloses #456"'
        result = multi_issue_guard.check_pr_command(command)
        assert result["warn"] is True
        assert "123" in result["message"]
        assert "456" in result["message"]

    def test_no_warn_single_closes(self):
        """No warning when PR body contains single Closes keyword."""
        command = 'gh pr create --title "Fix bug" --body "Closes #123"'
        result = multi_issue_guard.check_pr_command(command)
        assert result["warn"] is False

    def test_no_warn_no_pr_command(self):
        """No warning for non-pr commands."""
        result = multi_issue_guard.check_pr_command("git status")
        assert result["warn"] is False

    def test_handles_heredoc_body(self):
        """Handles HEREDOC-style body argument."""
        # Simplified heredoc simulation - actual body is not in command
        command = 'gh pr create --title "Fix" --body "$(cat <<\'EOF\'\nCloses #123\nEOF\n)"'
        result = multi_issue_guard.check_pr_command(command)
        # This might not detect the issue in heredoc - that's acceptable
        # The key is to not crash
        assert "warn" in result


class TestMain:
    """Test cases for main hook function."""

    def test_approves_non_target_command(self):
        """Non-target commands are approved without warning."""
        hook_input = {"tool_name": "Bash", "tool_input": {"command": "git status"}}

        with patch("sys.stdin", StringIO(json.dumps(hook_input))):
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                multi_issue_guard.main()

        output = mock_stdout.getvalue()
        # Should output nothing for non-target command
        assert output == ""

    def test_warns_multiple_issues_worktree(self):
        """Warns when worktree command has multiple Issues."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "git worktree add .worktrees/issue-123-456 -b feat/issue-123-456"
            },
        }

        with patch("sys.stdin", StringIO(json.dumps(hook_input))):
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                multi_issue_guard.main()

        output = mock_stdout.getvalue()
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "123" in result["systemMessage"]
        assert "456" in result["systemMessage"]

    def test_warns_multiple_closes_pr(self):
        """Warns when PR command has multiple Closes keywords."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {
                "command": 'gh pr create --title "Fix" --body "Closes #123\nCloses #456"'
            },
        }

        with patch("sys.stdin", StringIO(json.dumps(hook_input))):
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                multi_issue_guard.main()

        output = mock_stdout.getvalue()
        result = json.loads(output)
        assert result["decision"] == "approve"
        assert "systemMessage" in result

    def test_handles_empty_input(self):
        """Handles empty input gracefully."""
        with patch("sys.stdin", StringIO("{}")):
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                multi_issue_guard.main()

        # Should not crash, may output nothing
        output = mock_stdout.getvalue()
        assert output == "" or "decision" in output

    def test_skip_env_variable(self):
        """SKIP_MULTI_ISSUE_GUARD=1 skips the check."""
        import os

        hook_input = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "git worktree add .worktrees/issue-123-456 -b feat/issue-123-456"
            },
        }

        with patch.dict(os.environ, {"SKIP_MULTI_ISSUE_GUARD": "1"}):
            with patch("sys.stdin", StringIO(json.dumps(hook_input))):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    multi_issue_guard.main()

        output = mock_stdout.getvalue()
        result = json.loads(output)
        assert result["decision"] == "approve"
        # Should not have warning message when skipped
        assert "systemMessage" not in result or "複数Issue" not in result.get("systemMessage", "")
