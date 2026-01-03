#!/usr/bin/env python3
"""Tests for merge-check.py - issue module."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "merge-check.py"

# Import submodules for correct mock targets after modularization (Issue #1756)
# These imports enable tests to mock functions at their actual definition locations
import issue_checker


def run_hook(input_data: dict) -> dict | None:
    """Run the hook with given input and return the result.

    Returns None if no output (silent approval per design principle).
    """
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return None  # Silent approval
    return json.loads(result.stdout)


class TestExtractIssueNumbersFromPrBody:
    """Tests for extract_issue_numbers_from_pr_body function."""

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_extract_closes(self):
        """Should extract issue number from Closes keyword."""
        body = "## Summary\n\nCloses #123"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert result == ["123"]

    def test_extract_fixes(self):
        """Should extract issue number from Fixes keyword."""
        body = "Fixes #456"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert result == ["456"]

    def test_extract_resolves(self):
        """Should extract issue number from Resolves keyword."""
        body = "Resolves #789"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert result == ["789"]

    def test_case_insensitive(self):
        """Should be case insensitive."""
        body = "CLOSES #123"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert result == ["123"]

    def test_with_colon(self):
        """Should handle colon format (Closes: #123)."""
        body = "Closes: #321"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert result == ["321"]

    def test_multiple_issues(self):
        """Should extract multiple issue numbers."""
        body = "Closes #123, Fixes #456"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert "123" in result
        assert "456" in result

    def test_empty_body(self):
        """Should return empty list for empty body."""
        result = self.module.extract_issue_numbers_from_pr_body("")
        assert result == []

    def test_no_closes_keyword(self):
        """Should return empty list when no Closes/Fixes keyword."""
        body = "Just a description #123"  # No keyword
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert result == []

    def test_comma_separated_issues(self):
        """Should extract comma-separated issue numbers with single keyword."""
        body = "Closes #123, #456"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert "123" in result
        assert "456" in result
        assert len(result) == 2

    def test_comma_separated_issues_multiple(self):
        """Should extract multiple comma-separated issue numbers."""
        body = "Fixes #100, #200, #300"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert "100" in result
        assert "200" in result
        assert "300" in result
        assert len(result) == 3

    def test_deduplication(self):
        """Should deduplicate when same issue number appears multiple times."""
        body = "Closes #123, Fixes #123"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert result == ["123"]

    def test_past_tense_closed(self):
        """Should extract issue number from Closed keyword (past tense)."""
        body = "Closed #123"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert result == ["123"]

    def test_past_tense_fixed(self):
        """Should extract issue number from Fixed keyword (past tense)."""
        body = "Fixed #456"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert result == ["456"]

    def test_past_tense_resolved(self):
        """Should extract issue number from Resolved keyword (past tense)."""
        body = "Resolved #789"
        result = self.module.extract_issue_numbers_from_pr_body(body)
        assert result == ["789"]


class TestGetPrBody:
    """Tests for get_pr_body function."""

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    @patch("subprocess.run")
    def test_success(self, mock_run):
        """Should return PR body on success."""
        mock_run.return_value = MagicMock(returncode=0, stdout="PR body content\n")
        result = self.module.get_pr_body("123")
        assert result == "PR body content"

    @patch("subprocess.run")
    def test_api_error(self, mock_run):
        """Should return None on API error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = self.module.get_pr_body("123")
        assert result is None

    @patch("subprocess.run")
    def test_empty_body(self, mock_run):
        """Should return empty string for PR with empty body."""
        mock_run.return_value = MagicMock(returncode=0, stdout="\n")
        result = self.module.get_pr_body("123")
        assert result == ""

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        """Should return None on timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)
        result = self.module.get_pr_body("123")
        assert result is None


class TestFetchIssueAcceptanceCriteria:
    """Tests for fetch_issue_acceptance_criteria function."""

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_extract_unchecked_criteria(self):
        """Should extract unchecked checkbox items."""
        mock_response = json.dumps(
            {
                "title": "Test Issue",
                "body": "## Criteria\n- [ ] Task 1\n- [ ] Task 2",
                "state": "OPEN",
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_response)
            success, title, criteria = self.module.fetch_issue_acceptance_criteria("123")

        assert success
        assert title == "Test Issue"
        assert len(criteria) == 2
        # (is_completed, is_strikethrough, text)
        assert criteria[0] == (False, False, "Task 1")
        assert criteria[1] == (False, False, "Task 2")

    def test_extract_checked_criteria(self):
        """Should extract checked checkbox items."""
        mock_response = json.dumps(
            {"title": "Test Issue", "body": "- [x] Done task\n- [X] Also done", "state": "OPEN"}
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_response)
            success, title, criteria = self.module.fetch_issue_acceptance_criteria("123")

        assert success
        assert len(criteria) == 2
        # (is_completed, is_strikethrough, text) - checked boxes are not strikethrough
        assert criteria[0] == (True, False, "Done task")
        assert criteria[1] == (True, False, "Also done")

    def test_skip_closed_issues(self):
        """Should skip closed issues."""
        mock_response = json.dumps(
            {"title": "Closed Issue", "body": "- [ ] Unchecked", "state": "CLOSED"}
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_response)
            success, title, criteria = self.module.fetch_issue_acceptance_criteria("123")

        assert not success

    def test_fail_open_on_api_error(self):
        """Should return failure on API errors."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            success, title, criteria = self.module.fetch_issue_acceptance_criteria("123")

        assert not success
        assert criteria == []

    def test_strikethrough_checkbox_treated_as_completed(self):
        """Issue #823: Strikethrough checkboxes should be treated as completed."""
        mock_response = json.dumps(
            {
                "title": "Test Issue",
                "body": (
                    "## Criteria\n"
                    "- [ ] ~~No longer needed~~\n"
                    "- [x] Actually done\n"
                    "- [ ] Still pending"
                ),
                "state": "OPEN",
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_response)
            success, title, criteria = self.module.fetch_issue_acceptance_criteria("123")

        assert success
        assert len(criteria) == 3
        # (is_completed, is_strikethrough, text)
        # Strikethrough checkbox is treated as completed, is_strikethrough=True
        assert criteria[0] == (True, True, "~~No longer needed~~")
        # Checked checkbox is completed, not strikethrough
        assert criteria[1] == (True, False, "Actually done")
        # Unchecked without strikethrough is not completed
        assert criteria[2] == (False, False, "Still pending")

    def test_strikethrough_with_explanation_text(self):
        """Issue #823: Strikethrough with explanation should also be treated as completed."""
        mock_response = json.dumps(
            {
                "title": "Test Issue",
                "body": (
                    "- [ ] ~~Old requirement~~（上記で対応済み）\n"
                    "- [ ] ~~Removed feature~~ (not needed)\n"
                ),
                "state": "OPEN",
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_response)
            success, title, criteria = self.module.fetch_issue_acceptance_criteria("123")

        assert success
        # Items starting with strikethrough are treated as completed
        # even if there's explanation text after
        assert len(criteria) == 2
        # (is_completed, is_strikethrough, text)
        # "~~Old requirement~~（上記で対応済み）" - starts with strikethrough
        assert criteria[0] == (True, True, "~~Old requirement~~（上記で対応済み）")
        # "~~Removed feature~~ (not needed)" - starts with strikethrough
        assert criteria[1] == (True, True, "~~Removed feature~~ (not needed)")

    def test_strikethrough_edge_cases(self):
        """Issue #823: Edge cases for strikethrough detection."""
        mock_response = json.dumps(
            {
                "title": "Test Issue",
                "body": (
                    "- [ ] ~~incomplete strikethrough without closing\n"
                    "- [ ] ~~~~\n"
                    "- [ ] ~single tilde~\n"
                    "- [ ] Normal text ~~with strikethrough~~ in middle\n"
                ),
                "state": "OPEN",
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_response)
            success, title, criteria = self.module.fetch_issue_acceptance_criteria("123")

        assert success
        assert len(criteria) == 4
        # (is_completed, is_strikethrough, text)
        # Incomplete strikethrough (no closing ~~) should NOT be treated as completed
        assert criteria[0] == (False, False, "~~incomplete strikethrough without closing")
        # Empty strikethrough (~~~~) has no content between the markers
        # The regex requires at least one character (.+?), so this is NOT completed
        assert criteria[1] == (False, False, "~~~~")
        # Single tilde is not strikethrough
        assert criteria[2] == (False, False, "~single tilde~")
        # Strikethrough in middle (not at start) should NOT be treated as completed
        assert criteria[3] == (False, False, "Normal text ~~with strikethrough~~ in middle")

    def test_checked_checkbox_with_strikethrough(self):
        """Issue #823: Checked checkbox with strikethrough should be treated as completed."""
        mock_response = json.dumps(
            {
                "title": "Test Issue",
                "body": (
                    "- [x] ~~Checked with strikethrough~~\n"
                    "- [X] ~~Also checked with strikethrough~~\n"
                ),
                "state": "OPEN",
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_response)
            success, title, criteria = self.module.fetch_issue_acceptance_criteria("123")

        assert success
        assert len(criteria) == 2
        # (is_completed, is_strikethrough, text)
        # Checked checkbox with strikethrough: both conditions are true (checkbox AND strikethrough)
        # Should still be treated as completed, is_strikethrough=True
        assert criteria[0] == (True, True, "~~Checked with strikethrough~~")
        assert criteria[1] == (True, True, "~~Also checked with strikethrough~~")

    def test_ignore_checkboxes_in_code_blocks(self):
        """Should ignore checkboxes inside fenced code blocks (Issue #830).

        This prevents false positives when Issue body contains checkbox examples
        in code blocks like documentation or examples.
        """
        mock_response = json.dumps(
            {
                "title": "Test Issue",
                "body": (
                    "## Acceptance Criteria\n"
                    "- [ ] Real task 1\n"
                    "- [x] Real task 2\n"
                    "\n"
                    "## Example code block\n"
                    "```markdown\n"
                    "- [ ] Example checkbox in code\n"
                    "- [x] Another example\n"
                    "```\n"
                    "\n"
                    "- [ ] Real task 3\n"
                ),
                "state": "OPEN",
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_response)
            success, title, criteria = self.module.fetch_issue_acceptance_criteria("123")

        assert success
        # Should only find 3 real tasks, not the 2 in the code block
        assert len(criteria) == 3
        # (is_completed, is_strikethrough, text)
        assert criteria[0] == (False, False, "Real task 1")
        assert criteria[1] == (True, False, "Real task 2")
        assert criteria[2] == (False, False, "Real task 3")

    def test_ignore_checkboxes_in_multiple_code_blocks(self):
        """Should ignore checkboxes in multiple code blocks."""
        mock_response = json.dumps(
            {
                "title": "Test Issue",
                "body": (
                    "- [ ] Task before code\n"
                    "```\n"
                    "- [ ] In first code block\n"
                    "```\n"
                    "- [x] Task between blocks\n"
                    "```bash\n"
                    "- [ ] In second code block\n"
                    "```\n"
                    "- [ ] Task after code\n"
                ),
                "state": "OPEN",
            }
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_response)
            success, title, criteria = self.module.fetch_issue_acceptance_criteria("123")

        assert success
        assert len(criteria) == 3
        # (is_completed, is_strikethrough, text)
        assert criteria[0] == (False, False, "Task before code")
        assert criteria[1] == (True, False, "Task between blocks")
        assert criteria[2] == (False, False, "Task after code")


class TestCheckIncompleteAcceptanceCriteria:
    """Tests for check_incomplete_acceptance_criteria function."""

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_detect_incomplete_criteria(self):
        """Should detect issues with incomplete acceptance criteria."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {
                "title": "Test Issue",
                "body": "- [ ] Incomplete task\n- [x] Complete task",
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_incomplete_acceptance_criteria("456")

        assert len(result) == 1
        assert result[0]["issue_number"] == "123"
        assert result[0]["incomplete_count"] == 1
        # Issue #2463: Check completion ratio fields
        assert result[0]["total_count"] == 2
        assert result[0]["completed_count"] == 1

    def test_no_incomplete_when_all_checked(self):
        """Should return empty list when all criteria are complete."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {"title": "Test Issue", "body": "- [x] Task 1\n- [x] Task 2", "state": "OPEN"}
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_incomplete_acceptance_criteria("456")

        assert len(result) == 0

    def test_no_incomplete_when_no_criteria(self):
        """Should return empty list when issue has no acceptance criteria."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {
                "title": "Test Issue",
                "body": "Just a description without checkboxes",
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_incomplete_acceptance_criteria("456")

        assert len(result) == 0

    def test_skip_closed_issues(self):
        """Should skip closed issues (already handled)."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {"title": "Closed Issue", "body": "- [ ] Incomplete", "state": "CLOSED"}
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_incomplete_acceptance_criteria("456")

        assert len(result) == 0

    def test_no_pr_body(self):
        """Should return empty list when PR body is unavailable."""
        with patch.object(issue_checker, "get_pr_body", return_value=None):
            result = self.module.check_incomplete_acceptance_criteria("456")

        assert len(result) == 0

    def test_no_closes_in_body(self):
        """Should return empty list when no Closes keyword in PR body."""
        pr_body = "Just a PR description"

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            result = self.module.check_incomplete_acceptance_criteria("456")

        assert len(result) == 0

    def test_skip_issues_in_commits(self):
        """Should skip issues that are referenced in commit messages (Issue #1638)."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {
                "title": "Test Issue",
                "body": "- [ ] Not completed",  # Incomplete criteria
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            # Mock extract_issue_numbers_from_commits to return the same issue
            with patch.object(
                issue_checker, "extract_issue_numbers_from_commits", return_value=["123"]
            ):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                    result = self.module.check_incomplete_acceptance_criteria("456")

        # Should skip issue #123 because it's in commits
        assert len(result) == 0

    def test_completion_ratio_multiple_items(self):
        """Should correctly count completed and total items (Issue #2463)."""
        pr_body = "Closes #123"
        # 5 items total: 2 completed, 3 incomplete
        issue_response = json.dumps(
            {
                "title": "Test Issue with Multiple Items",
                "body": (
                    "- [x] Completed task 1\n"
                    "- [x] Completed task 2\n"
                    "- [ ] Incomplete task 1\n"
                    "- [ ] Incomplete task 2\n"
                    "- [ ] Incomplete task 3"
                ),
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_incomplete_acceptance_criteria("456")

        assert len(result) == 1
        assert result[0]["issue_number"] == "123"
        assert result[0]["incomplete_count"] == 3
        assert result[0]["total_count"] == 5
        assert result[0]["completed_count"] == 2

    def test_completion_ratio_with_deferred_items(self):
        """Should count deferred items (with Issue refs) as handled (Issue #2463)."""
        pr_body = "Closes #123"
        # 4 items total: 1 completed, 2 incomplete, 1 deferred (with Issue ref)
        issue_response = json.dumps(
            {
                "title": "Test Issue with Deferred Items",
                "body": (
                    "- [x] Completed task\n"
                    "- [ ] Incomplete task 1\n"
                    "- [ ] Incomplete task 2\n"
                    "- [ ] Deferred task → #456"  # Should count as "handled"
                ),
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_incomplete_acceptance_criteria("456")

        assert len(result) == 1
        assert result[0]["issue_number"] == "123"
        # Only 2 incomplete (the deferred one with Issue ref is not counted as incomplete)
        assert result[0]["incomplete_count"] == 2
        assert result[0]["total_count"] == 4
        # Deferred items (with Issue ref) count as "handled" (1 completed + 1 deferred with Issue ref = 2 handled)
        assert result[0]["completed_count"] == 2

    def test_completion_ratio_all_deferred(self):
        """Should handle all items being deferred (Issue #2463 edge case)."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {
                "title": "All Deferred Issue",
                "body": ("- [ ] Task 1 → #456\n- [ ] Task 2 → #789\n- [ ] Task 3 → #101"),
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_incomplete_acceptance_criteria("456")

        # All items are deferred with Issue refs, so none are "incomplete"
        assert len(result) == 0  # No blocking issues

    def test_completion_ratio_zero_completed_some_incomplete(self):
        """Should handle zero completed with some incomplete items (Issue #2463 edge case)."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {
                "title": "Zero Completed Issue",
                "body": (
                    "- [ ] Incomplete task 1\n- [ ] Incomplete task 2\n- [ ] Incomplete task 3"
                ),
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_incomplete_acceptance_criteria("456")

        assert len(result) == 1
        assert result[0]["issue_number"] == "123"
        assert result[0]["incomplete_count"] == 3
        assert result[0]["total_count"] == 3
        # Zero completed, zero deferred = 0 handled
        assert result[0]["completed_count"] == 0


class TestExtractIssueNumbersFromCommits:
    """Tests for extract_issue_numbers_from_commits function (Issue #1638)."""

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_extract_fixes_from_commits(self):
        """Should extract issue number from Fixes keyword in commits."""
        commit_output = "feat: add feature Fixes #123"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=commit_output)
            result = self.module.extract_issue_numbers_from_commits("456")

        assert "123" in result

    def test_extract_closes_from_commits(self):
        """Should extract issue number from Closes keyword in commits."""
        commit_output = "fix: bug fix Closes #789"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=commit_output)
            result = self.module.extract_issue_numbers_from_commits("456")

        assert "789" in result

    def test_empty_on_failure(self):
        """Should return empty list on API failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = self.module.extract_issue_numbers_from_commits("456")

        assert result == []

    def test_no_fixes_keyword(self):
        """Should return empty list when no Fixes/Closes keyword in commits."""
        commit_output = "feat: add feature #123"  # No keyword

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=commit_output)
            result = self.module.extract_issue_numbers_from_commits("456")

        assert result == []


class TestCheckExcludedCriteriaWithoutFollowup:
    """Tests for check_excluded_criteria_without_followup function (Issue #1458)."""

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_strikethrough_without_issue_ref_fails(self):
        """Strikethrough criteria without Issue reference should be flagged."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {
                "title": "Test Issue",
                "body": (
                    "- [x] Implemented feature\n- [ ] ~~Not needed anymore~~\n"  # No Issue ref
                ),
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_excluded_criteria_without_followup("456")

        assert len(result) == 1
        assert result[0]["issue_number"] == "123"
        assert "~~Not needed anymore~~" in result[0]["excluded_items"]

    def test_strikethrough_with_issue_ref_passes(self):
        """Strikethrough criteria with Issue reference should pass."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {
                "title": "Test Issue",
                "body": (
                    "- [x] Implemented feature\n"
                    "- [ ] ~~Not needed anymore~~ → #456 で対応\n"  # Has Issue ref
                ),
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_excluded_criteria_without_followup("789")

        assert len(result) == 0

    def test_checked_criteria_not_affected(self):
        """Normally completed [x] criteria should not require Issue ref."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {
                "title": "Test Issue",
                "body": ("- [x] Implemented feature\n- [x] Another feature\n"),
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_excluded_criteria_without_followup("456")

        # Checked items (not strikethrough) should not be flagged
        assert len(result) == 0

    def test_multiple_excluded_criteria(self):
        """Multiple strikethrough criteria should all be checked."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {
                "title": "Test Issue",
                "body": (
                    "- [x] Done\n"
                    "- [ ] ~~Excluded 1~~\n"  # No Issue ref
                    "- [ ] ~~Excluded 2~~ → #456\n"  # Has Issue ref
                    "- [ ] ~~Excluded 3~~\n"  # No Issue ref
                ),
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_excluded_criteria_without_followup("789")

        assert len(result) == 1
        assert result[0]["issue_number"] == "123"
        # Only items without Issue ref should be in excluded_items
        assert len(result[0]["excluded_items"]) == 2
        assert "~~Excluded 1~~" in result[0]["excluded_items"]
        assert "~~Excluded 3~~" in result[0]["excluded_items"]
        # Item with Issue ref should NOT be included
        assert "~~Excluded 2~~ → #456" not in result[0]["excluded_items"]

    def test_japanese_issue_creation_phrase(self):
        """Japanese 'Issue作成' phrase should also count as Issue reference."""
        pr_body = "Closes #123"
        issue_response = json.dumps(
            {
                "title": "Test Issue",
                "body": (
                    "- [ ] ~~対象外機能~~ Issue作成予定\n"  # Japanese phrase
                ),
                "state": "OPEN",
            }
        )

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=issue_response)
                result = self.module.check_excluded_criteria_without_followup("456")

        # Japanese phrase should be recognized as Issue reference
        assert len(result) == 0

    def test_no_pr_body(self):
        """Should return empty list when PR body is unavailable."""
        with patch.object(issue_checker, "get_pr_body", return_value=None):
            result = self.module.check_excluded_criteria_without_followup("456")

        assert len(result) == 0

    def test_no_closes_in_body(self):
        """Should return empty list when no Closes keyword in PR body."""
        pr_body = "Just a PR description"

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            result = self.module.check_excluded_criteria_without_followup("456")

        assert len(result) == 0

    def test_skip_issues_in_commits(self):
        """Should skip issues that are referenced in commits with Fixes/Closes.

        Issue #1638: Issues referenced with Fixes/Closes in commit messages
        will be auto-closed by GitHub on merge, so they should be skipped.
        """
        pr_body = "Closes #123"
        # Issue #123 has strikethrough without Issue ref, which normally would fail
        issue_response = json.dumps(
            {
                "title": "Test Issue",
                "body": ("- [x] Done\n- [ ] ~~Excluded without ref~~\n"),
                "state": "OPEN",
            }
        )
        # But #123 is also referenced in commits with Fixes keyword
        commit_output = "feat: add feature Fixes #123"

        with patch.object(issue_checker, "get_pr_body", return_value=pr_body):
            with patch("subprocess.run") as mock_run:
                # First call: extract_issue_numbers_from_commits (gh pr view --json commits)
                # Second call: issue info (gh issue view)
                def side_effect(*args, **kwargs):
                    cmd = args[0]
                    if "--json" in cmd and "commits" in cmd:
                        return MagicMock(returncode=0, stdout=commit_output)
                    return MagicMock(returncode=0, stdout=issue_response)

                mock_run.side_effect = side_effect
                result = self.module.check_excluded_criteria_without_followup("456")

        # Issue #123 should be skipped because it's in commits
        assert len(result) == 0
