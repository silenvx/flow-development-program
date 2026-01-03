#!/usr/bin/env python3
"""Tests for merge-check.py - integration module."""

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


class TestBlockingReasonPattern:
    """Tests for BlockingReason details pattern consistency (Issue #879).

    Ensures all BlockingReason instances include instructions for
    re-attempting merge after addressing the issue.
    """

    def test_all_blocking_reasons_include_merge_instruction(self):
        """All BlockingReason details should include merge-related instruction.

        This test ensures that every BlockingReason created in merge_conditions.py
        includes guidance on how to proceed with merging after fixing the issue.

        The pattern can be:
        - "再度マージを実行" (re-attempt merge)
        - "待ってからマージ" (wait then merge)
        - "マージしてください" (please merge)
        """
        import re

        # Read the source file (Issue #1756: BlockingReason instances moved to merge_conditions.py)
        source_path = HOOK_PATH.parent / "merge_conditions.py"
        with open(source_path, encoding="utf-8") as f:
            source_code = f.read()

        # Find all BlockingReason instantiations with their details
        # Pattern matches BlockingReason( ... details=( ... ), ... )
        # Issue #893: Improved pattern to handle closing parentheses in details
        # The pattern requires `),' after details to distinguish from internal parentheses
        blocking_reason_pattern = re.compile(
            r"BlockingReason\s*\(\s*"
            r'check_name\s*=\s*"([^"]+)".*?'
            r"details\s*=\s*\(([\s\S]*?)\),\s*\)",
            re.DOTALL,
        )

        matches = blocking_reason_pattern.findall(source_code)
        assert len(matches) > 0, "No BlockingReason instances found in source"

        # Verify each BlockingReason has merge instruction
        merge_pattern = re.compile(r"マージ")
        missing_merge_instruction = []

        for check_name, details in matches:
            if not merge_pattern.search(details):
                missing_merge_instruction.append(check_name)

        assert not missing_merge_instruction, (
            f"BlockingReason instances missing merge instruction: {missing_merge_instruction}\n"
            "All BlockingReason details should include 'マージ' related instruction "
            "(e.g., '再度マージを実行', '待ってからマージ')"
        )


class TestDryRunMode:
    """Tests for --dry-run mode (Issue #892).

    The dry-run mode allows checking merge readiness without actually
    blocking the merge command.
    """

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_dry_run_check_returns_zero_when_no_issues(self, capsys):
        """dry_run_check should return 0 when no issues are found."""
        with patch.object(self.module, "run_all_pr_checks", return_value=([], [])):
            result = self.module.dry_run_check(123)

        assert result == 0
        captured = capsys.readouterr()
        assert "✅" in captured.out
        assert "123" in captured.out

    def test_dry_run_check_returns_one_when_issues_found(self, capsys):
        """dry_run_check should return 1 when issues are found."""
        blocking_reason = self.module.BlockingReason(
            check_name="test_check",
            title="Test Issue",
            details="This is a test blocking reason. 再度マージを実行してください。",
        )
        with patch.object(self.module, "run_all_pr_checks", return_value=([blocking_reason], [])):
            result = self.module.dry_run_check(456)

        assert result == 1
        captured = capsys.readouterr()
        assert "⚠️" in captured.out
        assert "1件の問題" in captured.out
        assert "Test Issue" in captured.out

    def test_dry_run_check_returns_two_on_error(self, capsys):
        """dry_run_check should return 2 when an error occurs."""
        with patch.object(self.module, "run_all_pr_checks", side_effect=Exception("Test error")):
            result = self.module.dry_run_check(789)

        assert result == 2
        captured = capsys.readouterr()
        assert "❌" in captured.err
        assert "エラー" in captured.err

    def test_dry_run_output_format_multiple_issues(self, capsys):
        """dry_run_check should format multiple issues correctly."""
        reasons = [
            self.module.BlockingReason(
                check_name="check1",
                title="Issue 1",
                details="Detail 1. 再度マージを実行してください。",
            ),
            self.module.BlockingReason(
                check_name="check2",
                title="Issue 2",
                details="Detail 2. 再度マージを実行してください。",
            ),
        ]
        with patch.object(self.module, "run_all_pr_checks", return_value=(reasons, [])):
            result = self.module.dry_run_check(100)

        assert result == 1
        captured = capsys.readouterr()
        assert "2件の問題" in captured.out
        assert "Issue 1" in captured.out
        assert "Issue 2" in captured.out


class TestRunAllPrChecks:
    """Tests for run_all_pr_checks function (Issue #890).

    Ensures that already-merged PRs are properly skipped.
    """

    def setup_method(self):
        """Load the module."""
        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_skip_checks_for_merged_pr(self):
        """run_all_pr_checks should return empty tuple for merged PRs."""
        with patch.object(self.module, "is_pr_merged", return_value=True):
            result = self.module.run_all_pr_checks("123", dry_run=True)

        assert result == ([], [])

    def test_run_checks_for_open_pr(self):
        """run_all_pr_checks should run checks for open (unmerged) PRs."""
        with (
            patch.object(self.module, "is_pr_merged", return_value=False),
            patch.object(self.module, "check_ai_reviewing", return_value=[]),
            patch.object(self.module, "check_ai_review_error", return_value=None),
            patch.object(self.module, "check_dismissal_without_issue", return_value=[]),
            patch.object(self.module, "check_resolved_without_response", return_value=[]),
            patch.object(self.module, "check_resolved_without_verification", return_value=[]),
            patch.object(self.module, "check_unresolved_ai_threads", return_value=[]),
            patch.object(self.module, "check_numeric_claims_verified", return_value=[]),
            patch.object(self.module, "check_incomplete_acceptance_criteria", return_value=[]),
            patch.object(self.module, "check_bug_issue_from_review", return_value=[]),
        ):
            result = self.module.run_all_pr_checks("456", dry_run=True)

        # All checks pass, so result should be empty tuple
        assert result == ([], [])

    def test_merged_pr_skips_all_checks(self):
        """Merged PR should skip all checks without calling them."""
        mock_ai_reviewing = MagicMock()
        with (
            patch.object(self.module, "is_pr_merged", return_value=True),
            patch.object(self.module, "check_ai_reviewing", mock_ai_reviewing),
        ):
            result = self.module.run_all_pr_checks("789", dry_run=True)

        # is_pr_merged returned True, so check_ai_reviewing should not be called
        mock_ai_reviewing.assert_not_called()
        assert result == ([], [])


class TestRestApiMergeBlock:
    """Tests for REST API merge blocking (Issue #1379)."""

    def test_block_rest_api_merge_with_repos_prefix(self):
        """Should block gh api repos/owner/repo/pulls/123/merge."""
        result = run_hook(
            {"tool_input": {"command": "gh api repos/owner/repo/pulls/123/merge -X PUT"}}
        )
        assert result["decision"] == "block"
        assert "REST API" in result["reason"]
        assert "Issue #1379" in result["reason"]

    def test_block_rest_api_merge_without_repos_prefix(self):
        """Should block gh api pulls/123/merge."""
        result = run_hook({"tool_input": {"command": "gh api pulls/123/merge -X PUT"}})
        assert result["decision"] == "block"
        assert "REST API" in result["reason"]

    def test_block_rest_api_merge_chained_command(self):
        """Should block chained commands like cd repo && gh api pulls/123/merge."""
        result = run_hook(
            {"tool_input": {"command": "cd repo && gh api repos/o/r/pulls/456/merge -X PUT"}}
        )
        assert result["decision"] == "block"
        assert "REST API" in result["reason"]

    def test_approve_comment_with_merge_path_in_body(self):
        """Should NOT block gh pr comment --body containing merge path."""
        result = run_hook(
            {"tool_input": {"command": 'gh pr comment --body "Don\'t use gh api pulls/123/merge"'}}
        )
        # Should silently approve (no output)
        assert result is None

    def test_approve_regular_gh_api_call(self):
        """Should NOT block regular gh api calls."""
        result = run_hook({"tool_input": {"command": "gh api repos/owner/repo/pulls/123"}})
        # Should silently approve (no output) - not a merge command
        assert result is None

    def test_block_message_contains_alternative(self):
        """Block message should suggest gh pr merge alternative."""
        result = run_hook({"tool_input": {"command": "gh api repos/o/r/pulls/123/merge"}})
        assert result["decision"] == "block"
        assert "gh pr merge" in result["reason"]

    def test_block_rest_api_merge_with_trailing_slash(self):
        """Should block gh api pulls/123/merge/ (trailing slash)."""
        result = run_hook(
            {"tool_input": {"command": "gh api repos/owner/repo/pulls/123/merge/ -X PUT"}}
        )
        assert result["decision"] == "block"
        assert "REST API" in result["reason"]

    def test_block_rest_api_merge_with_leading_slash(self):
        """Should block gh api /repos/owner/repo/pulls/123/merge (leading slash)."""
        result = run_hook(
            {"tool_input": {"command": "gh api /repos/owner/repo/pulls/123/merge -X PUT"}}
        )
        assert result["decision"] == "block"
        assert "REST API" in result["reason"]

    def test_block_rest_api_merge_with_or_operator(self):
        """Should block chained commands using || operator."""
        result = run_hook({"tool_input": {"command": "false || gh api pulls/123/merge -X PUT"}})
        assert result["decision"] == "block"
        assert "REST API" in result["reason"]

    def test_block_rest_api_merge_with_semicolon(self):
        """Should block chained commands using ; operator."""
        result = run_hook({"tool_input": {"command": "echo test; gh api pulls/456/merge -X PUT"}})
        assert result["decision"] == "block"
        assert "REST API" in result["reason"]

    def test_approve_quoted_operator_in_body_issue_1392(self):
        """Should NOT block when shell operators are inside quoted strings (Issue #1392)."""
        # --body contains semicolon and gh api reference - should not trigger
        result = run_hook(
            {
                "tool_input": {
                    "command": 'gh pr comment --body "note; gh api pulls/123/merge example"'
                }
            }
        )
        assert result is None  # Should silently approve

    def test_approve_quoted_and_operator_in_body_issue_1392(self):
        """Should NOT block when && is inside quoted strings (Issue #1392)."""
        result = run_hook(
            {
                "tool_input": {
                    "command": 'gh pr comment --body "Use && to chain commands; gh pr merge"'
                }
            }
        )
        assert result is None  # Should silently approve

    def test_approve_quoted_pipe_operator_in_body_issue_1392(self):
        """Should NOT block when || is inside quoted strings (Issue #1392)."""
        result = run_hook(
            {"tool_input": {"command": "gh pr comment --body 'Try gh pr merge || gh api fallback'"}}
        )
        assert result is None  # Should silently approve

    def test_block_quoted_merge_path_codex_review(self):
        """Should block gh api with quoted merge path (Codex review fix)."""
        result = run_hook(
            {"tool_input": {"command": 'gh api "repos/owner/repo/pulls/123/merge" -X PUT'}}
        )
        assert result["decision"] == "block"
        assert "REST API" in result["reason"]

    def test_block_single_quoted_merge_path_codex_review(self):
        """Should block gh api with single-quoted merge path (Codex review fix)."""
        result = run_hook({"tool_input": {"command": "gh api 'pulls/456/merge' -X PUT"}})
        assert result["decision"] == "block"
        assert "REST API" in result["reason"]

    def test_approve_quoted_merge_in_other_command_body(self):
        """Should NOT block when merge path is in another command's body."""
        result = run_hook(
            {
                "tool_input": {
                    "command": 'gh api /repos/o/r/issues && gh pr comment --body "try pulls/123/merge"'
                }
            }
        )
        assert result is None  # Should silently approve


class TestMergeBackgroundReminder:
    """Tests for merge background reminder feature (Issue #2347)."""

    def test_reminder_shown_on_merge_with_pr_number(self):
        """Should show reminder when merge command has PR number."""
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=json.dumps({"tool_input": {"command": "gh pr merge 123 --squash"}}),
            capture_output=True,
            text=True,
        )
        # Check stderr for reminder
        assert "[REMINDER]" in result.stderr
        assert "背景（Why）" in result.stderr
        assert "コミットメッセージ規約" in result.stderr

    def test_no_reminder_on_non_merge_command(self):
        """Should NOT show reminder on non-merge commands."""
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=json.dumps({"tool_input": {"command": "gh pr view 123"}}),
            capture_output=True,
            text=True,
        )
        assert "[REMINDER]" not in result.stderr

    def test_no_reminder_on_merge_without_pr_number(self):
        """Should NOT show reminder when merge command lacks PR number."""
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=json.dumps({"tool_input": {"command": "gh pr merge"}}),
            capture_output=True,
            text=True,
        )
        # No PR number = no reminder (command will fail anyway)
        assert "[REMINDER]" not in result.stderr
