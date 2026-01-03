#!/usr/bin/env python3
"""Tests for dependabot-skill-reminder.py hook."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load module from hyphenated filename
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))


def load_module(name: str, filepath: Path):
    """Load a Python module from a hyphenated filename."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to load spec for {filepath}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dependabot_skill_reminder = load_module(
    "dependabot_skill_reminder", HOOKS_DIR / "dependabot-skill-reminder.py"
)


class TestIsDependabotOperation:
    """Tests for is_dependabot_operation function."""

    def test_detects_merge_command(self):
        """Should detect gh pr merge command."""
        is_op, pr_num = dependabot_skill_reminder.is_dependabot_operation("gh pr merge 123")
        assert is_op
        assert pr_num == "123"
        is_op, pr_num = dependabot_skill_reminder.is_dependabot_operation(
            "gh pr merge 456 --squash"
        )
        assert is_op
        assert pr_num == "456"

    def test_detects_rebase_command(self):
        """Should detect gh pr rebase command."""
        is_op, pr_num = dependabot_skill_reminder.is_dependabot_operation("gh pr rebase 789")
        assert is_op
        assert pr_num == "789"

    def test_detects_checkout_command(self):
        """Should detect gh pr checkout command."""
        is_op, pr_num = dependabot_skill_reminder.is_dependabot_operation("gh pr checkout 123")
        assert is_op
        assert pr_num == "123"

    def test_detects_global_flags(self):
        """Should detect commands with global flags like -R."""
        is_op, pr_num = dependabot_skill_reminder.is_dependabot_operation(
            "gh -R owner/repo pr merge 123"
        )
        assert is_op
        assert pr_num == "123"
        is_op, pr_num = dependabot_skill_reminder.is_dependabot_operation(
            "gh --repo owner/repo pr rebase 456"
        )
        assert is_op
        assert pr_num == "456"

    def test_ignores_other_commands(self):
        """Should ignore non-Dependabot operation commands."""
        is_op, _ = dependabot_skill_reminder.is_dependabot_operation("gh pr view 123")
        assert not is_op
        is_op, _ = dependabot_skill_reminder.is_dependabot_operation("gh pr list")
        assert not is_op
        is_op, _ = dependabot_skill_reminder.is_dependabot_operation("git status")
        assert not is_op
        is_op, _ = dependabot_skill_reminder.is_dependabot_operation("ls -la")
        assert not is_op


class TestIsDependabotPr:
    """Tests for is_dependabot_pr function."""

    def test_detects_dependabot_author(self):
        """Should detect PR by dependabot[bot] author."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {"author": {"login": "dependabot[bot]"}, "headRefName": "some-branch"}
        )

        with patch("subprocess.run", return_value=mock_result):
            assert dependabot_skill_reminder.is_dependabot_pr("123")

    def test_detects_dependabot_branch(self):
        """Should detect PR with dependabot/ branch prefix."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {"author": {"login": "some-user"}, "headRefName": "dependabot/npm_and_yarn/axios-1.7.0"}
        )

        with patch("subprocess.run", return_value=mock_result):
            assert dependabot_skill_reminder.is_dependabot_pr("456")

    def test_returns_false_for_regular_pr(self):
        """Should return False for non-Dependabot PR."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {"author": {"login": "regular-user"}, "headRefName": "feat/some-feature"}
        )

        with patch("subprocess.run", return_value=mock_result):
            assert not dependabot_skill_reminder.is_dependabot_pr("789")

    def test_returns_false_on_gh_error(self):
        """Should return False when gh command fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            assert not dependabot_skill_reminder.is_dependabot_pr("999")

    def test_returns_false_on_timeout(self):
        """Should return False when gh command times out."""
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 10)):
            assert not dependabot_skill_reminder.is_dependabot_pr("123")

    def test_returns_false_on_json_error(self):
        """Should return False when JSON parsing fails."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "invalid json"

        with patch("subprocess.run", return_value=mock_result):
            assert not dependabot_skill_reminder.is_dependabot_pr("123")


class TestMain:
    """Tests for main function."""

    def test_approves_non_dependabot_operation(self, capsys):
        """Should approve non-Dependabot operation commands."""
        mock_input = {"tool_input": {"command": "git status"}}

        with (
            patch.object(dependabot_skill_reminder, "parse_hook_input", return_value=mock_input),
            patch.object(dependabot_skill_reminder, "log_hook_execution"),
        ):
            dependabot_skill_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["decision"] == "approve"

    def test_approves_regular_pr_merge(self, capsys):
        """Should approve merge of non-Dependabot PR."""
        mock_input = {"tool_input": {"command": "gh pr merge 123 --squash"}}
        mock_gh_result = MagicMock()
        mock_gh_result.returncode = 0
        mock_gh_result.stdout = json.dumps(
            {"author": {"login": "regular-user"}, "headRefName": "feat/some-feature"}
        )

        with (
            patch.object(dependabot_skill_reminder, "parse_hook_input", return_value=mock_input),
            patch.object(dependabot_skill_reminder, "log_hook_execution"),
            patch("subprocess.run", return_value=mock_gh_result),
        ):
            dependabot_skill_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["decision"] == "approve"
        # No warning in stderr for regular PR
        assert "dependabot-skill-reminder" not in captured.err

    def test_warns_on_dependabot_pr_merge(self, capsys):
        """Should show warning but approve when merging Dependabot PR."""
        mock_input = {"tool_input": {"command": "gh pr merge 456 --squash"}}
        mock_gh_result = MagicMock()
        mock_gh_result.returncode = 0
        mock_gh_result.stdout = json.dumps(
            {
                "author": {"login": "dependabot[bot]"},
                "headRefName": "dependabot/npm_and_yarn/axios-1.7.0",
            }
        )

        with (
            patch.object(dependabot_skill_reminder, "parse_hook_input", return_value=mock_input),
            patch.object(dependabot_skill_reminder, "log_hook_execution"),
            patch("subprocess.run", return_value=mock_gh_result),
        ):
            dependabot_skill_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        # Should still approve
        assert result["decision"] == "approve"
        # Should show warning in stderr
        assert "dependabot-skill-reminder" in captured.err
        assert "development-workflow" in captured.err
        assert "456" in captured.err

    def test_approves_on_exception(self, capsys):
        """Should approve when exception occurs."""
        with (
            patch.object(
                dependabot_skill_reminder, "parse_hook_input", side_effect=Exception("Test error")
            ),
            patch.object(dependabot_skill_reminder, "log_hook_execution"),
        ):
            dependabot_skill_reminder.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["decision"] == "approve"
