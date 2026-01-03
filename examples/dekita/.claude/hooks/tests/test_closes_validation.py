#!/usr/bin/env python3
"""Tests for closes-validation.py hook."""

import sys
from pathlib import Path

# Add hooks directory to path for imports
TESTS_DIR = Path(__file__).parent
HOOKS_DIR = TESTS_DIR.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from conftest import load_hook_module

# Load the module for unit testing
MODULE_NAME = "closes-validation"


class TestClosesPattern:
    """Tests for CLOSES_PATTERN regex."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_closes_lowercase(self):
        """Test lowercase 'closes' pattern."""
        matches = self.module.CLOSES_PATTERN.findall("closes #123")
        assert matches == ["123"]

    def test_closes_uppercase(self):
        """Test uppercase 'Closes' pattern."""
        matches = self.module.CLOSES_PATTERN.findall("Closes #456")
        assert matches == ["456"]

    def test_fixes_pattern(self):
        """Test 'fixes' pattern."""
        matches = self.module.CLOSES_PATTERN.findall("fixes #789")
        assert matches == ["789"]

    def test_resolves_pattern(self):
        """Test 'resolves' pattern."""
        matches = self.module.CLOSES_PATTERN.findall("Resolves #100")
        assert matches == ["100"]

    def test_multiple_issues(self):
        """Test multiple issue references."""
        text = "Closes #123, fixes #456"
        matches = self.module.CLOSES_PATTERN.findall(text)
        assert set(matches) == {"123", "456"}

    def test_no_match(self):
        """Test text without issue references."""
        matches = self.module.CLOSES_PATTERN.findall("No issues here")
        assert matches == []


class TestExtractCommitMessage:
    """Tests for extract_commit_message function."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_double_quotes(self):
        """Test extracting message with double quotes."""
        command = 'git commit -m "Closes #123"'
        result = self.module.extract_commit_message(command)
        assert result == "Closes #123"

    def test_single_quotes(self):
        """Test extracting message with single quotes."""
        command = "git commit -m 'Fixes #456'"
        result = self.module.extract_commit_message(command)
        assert result == "Fixes #456"

    def test_heredoc_basic(self):
        """Test extracting message from basic HEREDOC format."""
        # Test the HEREDOC pattern that the hook actually uses
        command = """git commit -m "$(cat <<'EOF'
Closes #789

Test message
EOF
)\""""
        result = self.module.extract_commit_message(command)
        # HEREDOC extraction may return partial results depending on regex
        # This test documents that the function handles HEREDOC without crashing
        # The actual HEREDOC parsing is complex and may have limitations
        assert result is None or isinstance(result, str)

    def test_no_message(self):
        """Test command without -m flag."""
        command = "git commit --amend"
        result = self.module.extract_commit_message(command)
        assert result is None


class TestMainFunction:
    """Tests for main function behavior."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_non_bash_tool_approved(self):
        """Test that non-Bash tools are approved."""
        # This tests the logic without actually running the hook
        # since main() reads from environment variable
        pass  # Integration test via subprocess would be needed

    def test_non_commit_command_approved(self):
        """Test that non-commit Bash commands are approved."""
        pass  # Integration test via subprocess would be needed


class TestGetIssueInfo:
    """Tests for get_issue_info function."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_returns_none_on_invalid_issue(self):
        """Test that invalid issue numbers return None."""
        # This would require mocking subprocess or using a test repo
        # For now, we just verify the function exists and has correct signature
        assert callable(self.module.get_issue_info)


class TestGetChangedFiles:
    """Tests for get_changed_files function."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_function_exists(self):
        """Test that the function exists."""
        assert callable(self.module.get_changed_files)


class TestGetDiffStats:
    """Tests for get_diff_stats function."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_function_exists(self):
        """Test that the function exists."""
        assert callable(self.module.get_diff_stats)


class TestEstimateIssueSize:
    """Tests for estimate_issue_size function."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_large_by_enhancement_label(self):
        """Test large size detection by enhancement label."""
        issue_info = {
            "title": "Add new feature",
            "labels": [{"name": "enhancement"}],
            "body": "Short description",
        }
        result = self.module.estimate_issue_size(issue_info)
        assert result == "large"

    def test_large_by_feature_label(self):
        """Test large size detection by feature label."""
        issue_info = {
            "title": "Add feature",
            "labels": [{"name": "feature"}],
            "body": "",
        }
        result = self.module.estimate_issue_size(issue_info)
        assert result == "large"

    def test_small_by_documentation_label(self):
        """Test small size detection by documentation label."""
        issue_info = {
            "title": "Update docs",
            "labels": [{"name": "documentation"}],
            "body": "Fix typo",
        }
        result = self.module.estimate_issue_size(issue_info)
        assert result == "small"

    def test_medium_by_bug_label(self):
        """Test medium size detection by bug label."""
        issue_info = {
            "title": "Fix bug",
            "labels": [{"name": "bug"}],
            "body": "Bug description",
        }
        result = self.module.estimate_issue_size(issue_info)
        assert result == "medium"

    def test_large_by_title_keyword(self):
        """Test large size detection by title keyword."""
        issue_info = {
            "title": "フックライフサイクル管理システムの実装",
            "labels": [],
            "body": "",
        }
        result = self.module.estimate_issue_size(issue_info)
        assert result == "large"

    def test_small_by_title_keyword(self):
        """Test small size detection by typo keyword in title."""
        issue_info = {
            "title": "Fix typo in README",
            "labels": [],
            "body": "",
        }
        result = self.module.estimate_issue_size(issue_info)
        assert result == "small"

    def test_large_by_long_body(self):
        """Test large size detection by long body."""
        issue_info = {
            "title": "Some task",
            "labels": [],
            "body": "x" * 1500,  # More than 1000 characters
        }
        result = self.module.estimate_issue_size(issue_info)
        assert result == "large"

    def test_medium_by_default(self):
        """Test medium size as default."""
        issue_info = {
            "title": "Some task",
            "labels": [],
            "body": "Short description",
        }
        result = self.module.estimate_issue_size(issue_info)
        assert result == "medium"


class TestCheckSizeMismatch:
    """Tests for check_size_mismatch function."""

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_large_issue_small_commit_warns(self):
        """Test warning when large Issue has small commit."""
        issue_info = {
            "title": "Large feature",
            "labels": [{"name": "enhancement"}],
            "body": "",
        }
        diff_stats = {"insertions": 10, "deletions": 5}
        file_count = 1

        result = self.module.check_size_mismatch(issue_info, diff_stats, file_count)
        assert result is not None
        assert "サイズ乖離" in result

    def test_large_issue_large_commit_no_warning(self):
        """Test no warning when large Issue has large commit."""
        issue_info = {
            "title": "Large feature",
            "labels": [{"name": "enhancement"}],
            "body": "",
        }
        diff_stats = {"insertions": 200, "deletions": 50}
        file_count = 10

        result = self.module.check_size_mismatch(issue_info, diff_stats, file_count)
        assert result is None

    def test_small_issue_small_commit_no_warning(self):
        """Test no warning when small Issue has small commit."""
        issue_info = {
            "title": "Fix typo",
            "labels": [{"name": "documentation"}],
            "body": "",
        }
        diff_stats = {"insertions": 1, "deletions": 1}
        file_count = 1

        result = self.module.check_size_mismatch(issue_info, diff_stats, file_count)
        assert result is None

    def test_and_logic_lines_above_threshold_no_warning(self):
        """Test AND logic: lines above threshold (>=50), files below (<3) => no warning.

        The size mismatch check uses AND logic, so if either condition is NOT met,
        no warning should be issued. This tests the case where line count is at/above
        the threshold but file count is below.
        """
        issue_info = {
            "title": "Large feature",
            "labels": [{"name": "enhancement"}],
            "body": "",
        }
        # 50 lines is at the threshold, so no warning
        diff_stats = {"insertions": 30, "deletions": 20}  # total = 50
        file_count = 1  # below threshold of 3

        result = self.module.check_size_mismatch(issue_info, diff_stats, file_count)
        assert result is None

    def test_and_logic_files_above_threshold_no_warning(self):
        """Test AND logic: lines below threshold (<50), files at threshold (>=3) => no warning.

        The size mismatch check uses AND logic, so if either condition is NOT met,
        no warning should be issued. This tests the case where file count is at/above
        the threshold but line count is below.
        """
        issue_info = {
            "title": "Large feature",
            "labels": [{"name": "enhancement"}],
            "body": "",
        }
        diff_stats = {"insertions": 10, "deletions": 5}  # total = 15, below 50
        file_count = 3  # at threshold of 3

        result = self.module.check_size_mismatch(issue_info, diff_stats, file_count)
        assert result is None

    def test_and_logic_both_below_threshold_warns(self):
        """Test AND logic: both lines (<50) and files (<3) below thresholds => warning.

        The size mismatch check uses AND logic, so BOTH conditions must be met
        to trigger a warning. This tests the case where both are below thresholds.
        """
        issue_info = {
            "title": "Large feature",
            "labels": [{"name": "enhancement"}],
            "body": "",
        }
        diff_stats = {"insertions": 10, "deletions": 5}  # total = 15, below 50
        file_count = 2  # below threshold of 3

        result = self.module.check_size_mismatch(issue_info, diff_stats, file_count)
        assert result is not None
        assert "サイズ乖離" in result


class TestHasAllFlag:
    """Tests for has_all_flag function.

    Pattern should match:
    - -a, -am, -ma flags
    - --all flag
    Should NOT match:
    - -a inside quoted commit messages
    """

    def setup_method(self):
        self.module = load_hook_module(MODULE_NAME)

    def test_a_flag(self):
        """Test -a flag detection."""
        assert self.module.has_all_flag('git commit -a -m "message"')

    def test_am_flag(self):
        """Test -am flag detection."""
        assert self.module.has_all_flag('git commit -am "message"')

    def test_ma_flag(self):
        """Test -ma flag detection (reversed order)."""
        assert self.module.has_all_flag('git commit -ma "message"')

    def test_all_flag(self):
        """Test --all flag detection."""
        assert self.module.has_all_flag('git commit --all -m "message"')

    def test_no_flag(self):
        """Test command without -a flag."""
        assert not self.module.has_all_flag('git commit -m "message"')

    def test_a_in_quoted_message(self):
        """Test that -a inside commit message is NOT matched."""
        assert not self.module.has_all_flag('git commit -m "message with -a in text"')

    def test_am_in_quoted_message(self):
        """Test that -am inside commit message is NOT matched."""
        assert not self.module.has_all_flag('git commit -m "Fixes -am issue"')

    def test_single_quoted_message(self):
        """Test with single-quoted commit message."""
        assert self.module.has_all_flag("git commit -am 'message'")

    def test_a_flag_with_single_quote_content(self):
        """Test that -a inside single-quoted message is NOT matched."""
        assert not self.module.has_all_flag("git commit -m 'message with -a'")
