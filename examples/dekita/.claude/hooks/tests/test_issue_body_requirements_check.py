"""Tests for issue-body-requirements-check hook."""

import json
import subprocess
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "issue-body-requirements-check.py"


def run_hook(command: str, cwd: Path | None = None) -> dict:
    """Run the hook with a simulated gh issue create command.

    Args:
        command: The command to check
        cwd: Optional working directory for the subprocess
    """
    hook_input = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.stdout:
        return json.loads(result.stdout)
    return {"error": result.stderr, "returncode": result.returncode}


class TestIssueBodyRequirementsCheck:
    """Tests for issue body requirements check."""

    def test_non_gh_issue_create_command_passes(self):
        """Non-gh issue create commands should pass silently."""
        result = run_hook("git status")
        # No JSON output, just exit 0
        assert result.get("returncode") == 0 or result == {}

    def test_gh_issue_view_passes(self):
        """gh issue view should pass silently."""
        result = run_hook("gh issue view 123")
        # No JSON output, just exit 0
        assert result.get("returncode") == 0 or result == {}

    def test_missing_body_blocks(self):
        """Missing --body option should block."""
        result = run_hook('gh issue create --title "Test"')
        assert result.get("decision") == "block"
        assert "Issue本文（--body）が指定されていません" in result.get("systemMessage", "")

    def test_empty_body_blocks(self):
        """Empty body should block (missing required sections)."""
        result = run_hook('gh issue create --title "Test" --body ""')
        assert result.get("decision") == "block"

    def test_body_with_all_sections_passes(self):
        """Body with all required sections should pass."""
        body = """## なぜ
変更の理由

## 現状
現在の状態

## 期待動作
あるべき姿
"""
        result = run_hook(f'gh issue create --title "Test" --body "{body}"')
        assert result.get("decision") == "approve"

    def test_body_with_english_sections_passes(self):
        """Body with English section names should pass."""
        body = """## Why
Reason for change

## Current State
Current behavior

## Expected Behavior
What should happen
"""
        result = run_hook(f'gh issue create --title "Test" --body "{body}"')
        assert result.get("decision") == "approve"

    def test_body_with_proposed_solution_passes(self):
        """Body with '対応案' instead of '期待動作' should pass."""
        body = """## 背景
変更の理由

## 実際の動作
現在の状態

## 対応案
対応方法
"""
        result = run_hook(f'gh issue create --title "Test" --body "{body}"')
        assert result.get("decision") == "approve"

    def test_missing_why_section_blocks(self):
        """Missing 'なぜ' section should block."""
        body = """## 現状
現在の状態

## 期待動作
あるべき姿
"""
        result = run_hook(f'gh issue create --title "Test" --body "{body}"')
        assert result.get("decision") == "block"
        assert "なぜ/背景" in result.get("reason", "")

    def test_missing_current_state_section_blocks(self):
        """Missing '現状' section should block."""
        body = """## なぜ
変更の理由

## 期待動作
あるべき姿
"""
        result = run_hook(f'gh issue create --title "Test" --body "{body}"')
        assert result.get("decision") == "block"
        assert "現状/実際の動作" in result.get("reason", "")

    def test_missing_expected_section_blocks(self):
        """Missing '期待動作' section should block."""
        body = """## なぜ
変更の理由

## 現状
現在の状態
"""
        result = run_hook(f'gh issue create --title "Test" --body "{body}"')
        assert result.get("decision") == "block"
        assert "期待動作/対応案" in result.get("reason", "")

    def test_trivial_label_skips_check(self):
        """Trivial label should skip the check."""
        result = run_hook('gh issue create --title "Test" --label "trivial" --body "minimal"')
        assert result.get("decision") == "approve"

    def test_documentation_label_skips_check(self):
        """Documentation label should skip the check."""
        result = run_hook('gh issue create --title "Test" --label "documentation" --body "minimal"')
        assert result.get("decision") == "approve"

    def test_docs_label_skips_check(self):
        """Docs label should skip the check."""
        result = run_hook('gh issue create --title "Test" --label "docs" --body "minimal"')
        assert result.get("decision") == "approve"

    def test_body_with_skip_keyword_skips_check(self):
        """Body containing '調査不要' should skip the check."""
        result = run_hook('gh issue create --title "Test" --body "調査不要: 簡単な修正"')
        assert result.get("decision") == "approve"

    def test_case_insensitive_section_matching(self):
        """Section matching should be case-insensitive."""
        body = """## WHY
Reason

## CURRENT STATE
State

## EXPECTED
Expected
"""
        result = run_hook(f'gh issue create --title "Test" --body "{body}"')
        assert result.get("decision") == "approve"

    def test_h3_sections_are_recognized(self):
        """H3 sections (###) should also be recognized."""
        body = """### なぜ
変更の理由

### 現状
現在の状態

### 期待動作
あるべき姿
"""
        result = run_hook(f'gh issue create --title "Test" --body "{body}"')
        assert result.get("decision") == "approve"

    def test_comma_separated_labels(self):
        """Comma-separated labels should be correctly parsed."""
        result = run_hook('gh issue create --title "Test" --label "bug,trivial" --body "minimal"')
        assert result.get("decision") == "approve"

    def test_env_prefix_handled(self):
        """Environment variable prefix should be handled."""
        result = run_hook('GH_TOKEN=xxx gh issue create --title "Test"')
        assert result.get("decision") == "block"

    def test_full_path_gh_command(self):
        """Full path to gh command should be recognized."""
        result = run_hook('/usr/local/bin/gh issue create --title "Test"')
        assert result.get("decision") == "block"


class TestBodyFileSupport:
    """Tests for --body-file support.

    Note: These tests use the cwd parameter to set the subprocess working directory,
    which is needed for path traversal protection validation.
    """

    def test_body_file_option(self, tmp_path):
        """--body-file option should read file contents."""
        body_file = tmp_path / "body.md"
        body_file.write_text(
            "## なぜ\n変更理由\n\n## 現状\n現在の状態\n\n## 期待動作\nあるべき姿\n"
        )
        result = run_hook(f'gh issue create --title "Test" --body-file "{body_file}"', cwd=tmp_path)
        assert result.get("decision") == "approve"

    def test_body_file_equals_option(self, tmp_path):
        """--body-file= option should read file contents."""
        body_file = tmp_path / "body.md"
        body_file.write_text(
            "## なぜ\n変更理由\n\n## 現状\n現在の状態\n\n## 期待動作\nあるべき姿\n"
        )
        result = run_hook(f'gh issue create --title "Test" --body-file="{body_file}"', cwd=tmp_path)
        assert result.get("decision") == "approve"

    def test_short_form_body_file_option(self, tmp_path):
        """-F option (short form) should read file contents."""
        body_file = tmp_path / "body.md"
        body_file.write_text(
            "## なぜ\n変更理由\n\n## 現状\n現在の状態\n\n## 期待動作\nあるべき姿\n"
        )
        result = run_hook(f'gh issue create --title "Test" -F "{body_file}"', cwd=tmp_path)
        assert result.get("decision") == "approve"

    def test_body_file_missing_sections_blocks(self, tmp_path):
        """--body-file with missing sections should block."""
        body_file = tmp_path / "body.md"
        body_file.write_text("## なぜ\n変更理由のみ\n")
        result = run_hook(f'gh issue create --title "Test" --body-file "{body_file}"', cwd=tmp_path)
        assert result.get("decision") == "block"

    def test_body_file_not_found_blocks(self, tmp_path):
        """Non-existent body file should block."""
        result = run_hook(
            f'gh issue create --title "Test" --body-file "{tmp_path}/nonexistent.md"', cwd=tmp_path
        )
        assert result.get("decision") == "block"

    def test_body_takes_precedence_over_body_file(self, tmp_path):
        """--body should take precedence over --body-file."""
        body_file = tmp_path / "body.md"
        body_file.write_text("File content with no sections")
        # --body comes first and is complete
        result = run_hook(
            f'gh issue create --title "Test" --body "## なぜ\ntest\n## 現状\ntest\n## 期待動作\ntest" --body-file "{body_file}"',
            cwd=tmp_path,
        )
        assert result.get("decision") == "approve"

    def test_path_traversal_blocked(self, tmp_path):
        """Path traversal attempts should be blocked."""
        # Create a file outside of what will be the cwd
        parent_file = tmp_path / "parent.md"
        parent_file.write_text(
            "## なぜ\n変更理由\n\n## 現状\n現在の状態\n\n## 期待動作\nあるべき姿\n"
        )

        # Create a subdirectory and set it as cwd
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        # Try to access file in parent directory using path traversal
        result = run_hook('gh issue create --title "Test" --body-file "../parent.md"', cwd=subdir)
        assert result.get("decision") == "block"
