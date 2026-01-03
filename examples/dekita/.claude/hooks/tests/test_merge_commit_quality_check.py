#!/usr/bin/env python3
"""Unit tests for merge-commit-quality-check.py

This hook blocks --body option usage in gh pr merge commands.
The --body option overwrites the PR description, losing valuable context.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# merge-commit-quality-check.py has hyphens, so we need dynamic import
HOOK_PATH = Path(__file__).parent.parent / "merge-commit-quality-check.py"
_spec = importlib.util.spec_from_file_location("merge_commit_quality_check", HOOK_PATH)
merge_commit_quality_check = importlib.util.module_from_spec(_spec)
sys.modules["merge_commit_quality_check"] = merge_commit_quality_check
_spec.loader.exec_module(merge_commit_quality_check)

is_gh_pr_merge_command = merge_commit_quality_check.is_gh_pr_merge_command
has_body_option = merge_commit_quality_check.has_body_option
format_block_message = merge_commit_quality_check.format_block_message


class TestIsGhPrMergeCommand:
    """Tests for is_gh_pr_merge_command function."""

    def test_simple_merge_command(self):
        """Should detect simple gh pr merge command."""
        assert is_gh_pr_merge_command("gh pr merge 123")
        assert is_gh_pr_merge_command("gh pr merge")
        assert is_gh_pr_merge_command("gh pr merge #123")

    def test_merge_with_options(self):
        """Should detect merge command with options."""
        assert is_gh_pr_merge_command("gh pr merge 123 --squash")
        assert is_gh_pr_merge_command('gh pr merge 123 --squash --body "test"')
        assert is_gh_pr_merge_command("gh pr merge 123 --squash --delete-branch")

    def test_not_merge_command(self):
        """Should not match non-merge commands."""
        assert not is_gh_pr_merge_command("gh pr create")
        assert not is_gh_pr_merge_command("gh pr view 123")
        assert not is_gh_pr_merge_command("")
        assert not is_gh_pr_merge_command("gh issue merge")


class TestHasBodyOption:
    """Tests for has_body_option function."""

    def test_detects_body_option(self):
        """Should detect --body option."""
        assert has_body_option('gh pr merge 123 --body "test"')
        assert has_body_option('gh pr merge 123 -b "test"')
        assert has_body_option("gh pr merge 123 --body='test'")
        assert has_body_option('gh pr merge 123 --squash --body "message"')

    def test_detects_heredoc_body(self):
        """Should detect --body with HEREDOC."""
        cmd = '''gh pr merge 123 --body "$(cat <<'EOF'
message content
EOF
)"'''
        assert has_body_option(cmd)

    def test_no_body_option(self):
        """Should return False when no body option."""
        assert not has_body_option("gh pr merge 123 --squash")
        assert not has_body_option("gh pr merge 123")
        assert not has_body_option("gh pr merge 123 --squash --delete-branch")


class TestFormatBlockMessage:
    """Tests for format_block_message function."""

    def test_contains_reason(self):
        """Should explain why --body is blocked."""
        message = format_block_message()
        assert "--body" in message
        assert "禁止" in message or "blocked" in message.lower()

    def test_contains_correct_method(self):
        """Should show correct method using gh pr edit."""
        message = format_block_message()
        assert "gh pr edit" in message
        assert "gh pr merge" in message


def run_hook(command: str) -> dict:
    """Helper to run the hook with a command."""
    hook_input = {"tool_input": {"command": command}}

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.read.return_value = json.dumps(hook_input)

        # Capture stdout
        import io

        captured_output = io.StringIO()
        with patch("sys.stdout", captured_output):
            merge_commit_quality_check.main()

        return json.loads(captured_output.getvalue())


class TestMainHookBehavior:
    """Integration tests for the main hook behavior."""

    def test_blocks_body_option(self):
        """Should block when --body is used."""
        result = run_hook('gh pr merge 123 --squash --body "message"')
        assert result["decision"] == "block"
        assert "--body" in result.get("systemMessage", "")

    def test_blocks_short_b_option(self):
        """Should block when -b is used."""
        result = run_hook('gh pr merge 123 --squash -b "message"')
        assert result["decision"] == "block"

    def test_blocks_heredoc_body(self):
        """Should block HEREDOC style --body."""
        cmd = '''gh pr merge 123 --squash --body "$(cat <<'EOF'
## なぜ
背景説明
EOF
)"'''
        result = run_hook(cmd)
        assert result["decision"] == "block"

    def test_approves_without_body(self):
        """Should approve when --body is not used."""
        result = run_hook("gh pr merge 123 --squash --delete-branch")
        assert result["decision"] == "approve"
        assert "systemMessage" in result

    def test_approves_simple_merge(self):
        """Should approve simple merge without body."""
        result = run_hook("gh pr merge 123")
        assert result["decision"] == "approve"

    def test_ignores_non_merge_commands(self):
        """Should not affect non-merge commands."""
        result = run_hook("gh pr view 123")
        assert result["decision"] == "approve"

    def test_ignores_pr_edit_body(self):
        """Should not block gh pr edit --body."""
        result = run_hook('gh pr edit 123 --body "new description"')
        assert result["decision"] == "approve"

    def test_ignores_pr_create_body(self):
        """Should not block gh pr create --body."""
        result = run_hook('gh pr create --title "Test" --body "description"')
        assert result["decision"] == "approve"
