"""Tests for issue-priority-label-check.py hook."""

import importlib.util
import sys
from pathlib import Path

# Load the hook module directly
HOOK_PATH = Path(__file__).parent.parent / "issue-priority-label-check.py"
spec = importlib.util.spec_from_file_location("issue_priority_label_check", HOOK_PATH)
hook_module = importlib.util.module_from_spec(spec)
sys.modules["issue_priority_label_check"] = hook_module
spec.loader.exec_module(hook_module)

is_gh_issue_create_command = hook_module.is_gh_issue_create_command
extract_labels_from_command = hook_module.extract_labels_from_command
has_priority_label = hook_module.has_priority_label


class TestIsGhIssueCreateCommand:
    """Tests for is_gh_issue_create_command function."""

    def test_simple_gh_issue_create(self):
        """Basic gh issue create command."""
        assert is_gh_issue_create_command("gh issue create --title 'test'") is True

    def test_gh_issue_create_with_options(self):
        """gh issue create with multiple options."""
        cmd = 'gh issue create --title "test" --body "body" --label "bug,P1"'
        assert is_gh_issue_create_command(cmd) is True

    def test_full_path_gh(self):
        """gh command with full path."""
        assert is_gh_issue_create_command("/usr/local/bin/gh issue create") is True

    def test_env_prefix(self):
        """gh command with environment variable prefix."""
        assert is_gh_issue_create_command("GH_TOKEN=xxx gh issue create") is True

    def test_not_gh_command(self):
        """Non-gh commands should return False."""
        assert is_gh_issue_create_command("git commit -m 'test'") is False

    def test_gh_other_command(self):
        """Other gh commands should return False."""
        assert is_gh_issue_create_command("gh pr create") is False

    def test_gh_issue_list(self):
        """gh issue list should return False."""
        assert is_gh_issue_create_command("gh issue list") is False

    def test_in_commit_message(self):
        """gh issue create in commit message should return False."""
        cmd = 'git commit -m "fix: related to gh issue create"'
        assert is_gh_issue_create_command(cmd) is False

    def test_unbalanced_quotes_fallback(self):
        """gh issue create with unbalanced quotes should still be detected."""
        # shlex.split will fail, but fallback to command.split() should work
        cmd = 'gh issue create --title "unclosed quote'
        assert is_gh_issue_create_command(cmd) is True


class TestExtractLabelsFromCommand:
    """Tests for extract_labels_from_command function."""

    def test_single_label_long_form(self):
        """Single label with --label."""
        cmd = 'gh issue create --label "bug"'
        assert extract_labels_from_command(cmd) == ["bug"]

    def test_single_label_short_form(self):
        """Single label with -l."""
        cmd = 'gh issue create -l "enhancement"'
        assert extract_labels_from_command(cmd) == ["enhancement"]

    def test_label_equals_form(self):
        """Label with --label=value form."""
        cmd = 'gh issue create --label="bug"'
        assert extract_labels_from_command(cmd) == ["bug"]

    def test_multiple_labels(self):
        """Multiple -l options."""
        cmd = 'gh issue create -l "bug" -l "P1"'
        assert extract_labels_from_command(cmd) == ["bug", "P1"]

    def test_comma_separated_labels(self):
        """Comma-separated labels in single option."""
        cmd = 'gh issue create --label "bug,P2"'
        assert extract_labels_from_command(cmd) == ["bug,P2"]

    def test_no_labels(self):
        """Command without labels."""
        cmd = 'gh issue create --title "test"'
        assert extract_labels_from_command(cmd) == []

    def test_short_label_equals_form(self):
        """-l=value form."""
        cmd = 'gh issue create -l="bug,P2"'
        assert extract_labels_from_command(cmd) == ["bug,P2"]


class TestHasPriorityLabel:
    """Tests for has_priority_label function."""

    def test_p0_label(self):
        """P0 label should be detected."""
        assert has_priority_label(["P0"]) is True

    def test_p1_label(self):
        """P1 label should be detected."""
        assert has_priority_label(["P1"]) is True

    def test_p2_label(self):
        """P2 label should be detected."""
        assert has_priority_label(["P2"]) is True

    def test_p3_label(self):
        """P3 label should be detected."""
        assert has_priority_label(["P3"]) is True

    def test_comma_separated_with_priority(self):
        """Priority in comma-separated labels."""
        assert has_priority_label(["bug,P2"]) is True
        assert has_priority_label(["enhancement,P1"]) is True

    def test_multiple_labels_with_priority(self):
        """Priority in multiple label options."""
        assert has_priority_label(["bug", "P2"]) is True

    def test_no_priority_label(self):
        """No priority label should return False."""
        assert has_priority_label(["bug"]) is False
        assert has_priority_label(["enhancement"]) is False

    def test_empty_labels(self):
        """Empty label list should return False."""
        assert has_priority_label([]) is False

    def test_lowercase_is_matched(self):
        """Lowercase priority should be matched (case-insensitive).

        Issue #1957: Changed to case-insensitive to be more user-friendly.
        """
        assert has_priority_label(["p2"]) is True
        assert has_priority_label(["p0"]) is True

    def test_priority_with_spaces(self):
        """Priority with surrounding spaces should be trimmed."""
        assert has_priority_label([" P2 "]) is True
        assert has_priority_label(["bug, P2"]) is True


class TestIntegrationUnit:
    """Integration tests for the full flow."""

    def test_valid_issue_create_with_priority(self):
        """Full command with priority label should pass."""
        cmd = 'gh issue create --title "test" --body "body" --label "bug,P2"'
        assert is_gh_issue_create_command(cmd) is True
        labels = extract_labels_from_command(cmd)
        assert has_priority_label(labels) is True

    def test_issue_create_without_priority(self):
        """Full command without priority label should be caught."""
        cmd = 'gh issue create --title "test" --body "body" --label "bug"'
        assert is_gh_issue_create_command(cmd) is True
        labels = extract_labels_from_command(cmd)
        assert has_priority_label(labels) is False

    def test_issue_create_no_labels(self):
        """Full command without any labels should be caught."""
        cmd = 'gh issue create --title "test" --body "body"'
        assert is_gh_issue_create_command(cmd) is True
        labels = extract_labels_from_command(cmd)
        assert has_priority_label(labels) is False


class TestHookExecution:
    """Integration tests using subprocess to run the actual hook."""

    def _run_hook(self, command: str) -> tuple[int, str]:
        """Run the hook with the given command and return exit code and output."""
        import json
        import subprocess

        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout

    def test_hook_blocks_without_priority(self):
        """Hook should block when priority label is missing."""
        import json

        exit_code, output = self._run_hook('gh issue create --title "test" --label "bug"')
        assert exit_code == 0
        result = json.loads(output)
        assert result["decision"] == "block"
        assert "P0-P3" in result["reason"]

    def test_hook_approves_with_priority(self):
        """Hook should approve when priority label is present."""
        exit_code, output = self._run_hook('gh issue create --title "test" --label "bug,P2"')
        # When approved, hook exits without output
        assert exit_code == 0
        assert output == ""

    def test_hook_ignores_non_issue_create(self):
        """Hook should not output anything for non-issue-create commands."""
        exit_code, output = self._run_hook("git commit -m 'test'")
        assert exit_code == 0
        assert output == ""
