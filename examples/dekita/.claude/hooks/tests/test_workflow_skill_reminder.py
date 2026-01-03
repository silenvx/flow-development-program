#!/usr/bin/env python3
"""Tests for workflow-skill-reminder.py hook."""

import json
import subprocess
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "workflow-skill-reminder.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result.

    Args:
        input_data: The JSON input data to pass to the hook.
    """
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestWorkflowSkillReminderIntegration:
    """Integration tests for workflow-skill-reminder hook."""

    def test_ignores_non_target_commands(self):
        """Should approve non-worktree/non-pr commands without reminder."""
        result = run_hook({"tool_input": {"command": "ls -la"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignores_git_status(self):
        """Should approve git status commands without reminder."""
        result = run_hook({"tool_input": {"command": "git status"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignores_git_push(self):
        """Should approve git push commands without reminder."""
        result = run_hook({"tool_input": {"command": "git push"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_handles_empty_command(self):
        """Should handle empty command gracefully."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_handles_missing_tool_input(self):
        """Should handle missing tool_input gracefully."""
        result = run_hook({})
        assert result["decision"] == "approve"


class TestWorktreeAddDetection:
    """Tests for git worktree add command detection."""

    def test_detects_worktree_add(self):
        """Should show reminder for git worktree add."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat/issue-123"}}
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "development-workflow" in result["systemMessage"]
        assert "worktree作成" in result["systemMessage"]

    def test_detects_worktree_add_with_lock(self):
        """Should show reminder for git worktree add --lock."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add --lock .worktrees/issue-123 -b feat/123"}}
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "development-workflow" in result["systemMessage"]

    def test_detects_worktree_add_with_env_prefix(self):
        """Should detect worktree add with environment variable prefix."""
        result = run_hook(
            {
                "tool_input": {
                    "command": "SKIP_PLAN=1 git worktree add .worktrees/issue-123 -b feat/123"
                }
            }
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "development-workflow" in result["systemMessage"]

    def test_detects_worktree_add_in_chain(self):
        """Should detect worktree add in command chain."""
        result = run_hook(
            {
                "tool_input": {
                    "command": "git fetch origin && git worktree add .worktrees/issue-123 -b feat/123"
                }
            }
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "development-workflow" in result["systemMessage"]

    def test_ignores_worktree_list(self):
        """Should NOT trigger on git worktree list."""
        result = run_hook({"tool_input": {"command": "git worktree list"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignores_worktree_remove(self):
        """Should NOT trigger on git worktree remove."""
        result = run_hook({"tool_input": {"command": "git worktree remove .worktrees/issue-123"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignores_quoted_worktree_add(self):
        """Should NOT trigger on worktree add in quoted strings."""
        result = run_hook({"tool_input": {"command": "echo 'git worktree add is a command'"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result


class TestPrCreateDetection:
    """Tests for gh pr create command detection."""

    def test_detects_pr_create(self):
        """Should show reminder for gh pr create."""
        result = run_hook(
            {"tool_input": {"command": "gh pr create --title 'My PR' --body 'Description'"}}
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "development-workflow" in result["systemMessage"]
        assert "PR作成" in result["systemMessage"]

    def test_detects_pr_create_simple(self):
        """Should show reminder for simple gh pr create."""
        result = run_hook({"tool_input": {"command": "gh pr create"}})
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "development-workflow" in result["systemMessage"]

    def test_detects_pr_create_in_chain(self):
        """Should detect gh pr create in command chain."""
        result = run_hook({"tool_input": {"command": "git push && gh pr create --title 'test'"}})
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "development-workflow" in result["systemMessage"]

    def test_ignores_pr_view(self):
        """Should NOT trigger on gh pr view."""
        result = run_hook({"tool_input": {"command": "gh pr view 123"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignores_pr_merge(self):
        """Should NOT trigger on gh pr merge (different workflow step)."""
        result = run_hook({"tool_input": {"command": "gh pr merge 123"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignores_quoted_pr_create(self):
        """Should NOT trigger on pr create in quoted strings."""
        result = run_hook({"tool_input": {"command": "echo 'Run gh pr create to create a PR'"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result


class TestCombinedDetection:
    """Tests for combined worktree and PR detection scenarios."""

    def test_shows_both_reminders(self):
        """Should show both reminders when both commands are in chain."""
        result = run_hook(
            {"tool_input": {"command": "git worktree add .worktrees/x -b feat/x && gh pr create"}}
        )
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "worktree作成" in result["systemMessage"]
        assert "PR作成" in result["systemMessage"]
        # Both should reference development-workflow
        assert "development-workflow" in result["systemMessage"]


class TestWorkflowSkillReminderUnit:
    """Unit tests for workflow-skill-reminder hook functions."""

    def setup_method(self):
        """Import module functions for testing."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("workflow_skill_reminder", str(HOOK_PATH))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_is_worktree_add_command_positive(self):
        """Should detect various worktree add patterns."""
        assert self.module.is_worktree_add_command("git worktree add .worktrees/x -b feat")
        assert self.module.is_worktree_add_command("git worktree add --lock .worktrees/x")
        assert self.module.is_worktree_add_command("SKIP=1 git worktree add .worktrees/x")

    def test_is_worktree_add_command_negative(self):
        """Should NOT detect non-add worktree commands."""
        assert not self.module.is_worktree_add_command("git worktree list")
        assert not self.module.is_worktree_add_command("git worktree remove .worktrees/x")
        assert not self.module.is_worktree_add_command("git worktree prune")
        assert not self.module.is_worktree_add_command("")

    def test_is_pr_create_command_positive(self):
        """Should detect various pr create patterns."""
        assert self.module.is_pr_create_command("gh pr create")
        assert self.module.is_pr_create_command("gh pr create --title 'test'")
        assert self.module.is_pr_create_command("git push && gh pr create")

    def test_is_pr_create_command_negative(self):
        """Should NOT detect non-create pr commands."""
        assert not self.module.is_pr_create_command("gh pr view 123")
        assert not self.module.is_pr_create_command("gh pr merge 123")
        assert not self.module.is_pr_create_command("gh pr list")
        assert not self.module.is_pr_create_command("")

    def test_build_worktree_skill_reminder(self):
        """Should build proper reminder message for worktree creation."""
        reminder = self.module.build_worktree_skill_reminder()

        # Check key elements
        assert "development-workflow" in reminder
        assert "worktree作成" in reminder
        assert "--lock" in reminder
        assert "ブランチ命名規則" in reminder
        assert "setup-worktree.sh" in reminder
        assert "/development-workflow" in reminder
        assert "単純な作業だからSkill不要" in reminder

    def test_build_pr_create_skill_reminder(self):
        """Should build proper reminder message for PR creation."""
        reminder = self.module.build_pr_create_skill_reminder()

        # Check key elements
        assert "development-workflow" in reminder
        assert "PR作成" in reminder
        assert "ローカルテスト" in reminder
        assert "Codexレビュー" in reminder
        assert "コミットメッセージ規約" in reminder
        assert "スクリーンショット" in reminder
        assert "/development-workflow" in reminder
        assert "単純な変更だからSkill不要" in reminder
