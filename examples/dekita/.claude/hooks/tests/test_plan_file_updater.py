"""Tests for plan-file-updater.py hook.

Issue #1336: Plan file checkboxes auto-update after PR merge.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Import functions from plan-file-updater.py (hyphenated filename)
hook_path = Path(__file__).parent.parent / "plan-file-updater.py"
spec = importlib.util.spec_from_file_location("plan_file_updater", hook_path)
assert spec is not None and spec.loader is not None
plan_file_updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan_file_updater)

extract_issue_number_from_branch = plan_file_updater.extract_issue_number_from_branch
extract_pr_number_from_command = plan_file_updater.extract_pr_number_from_command
find_plan_file = plan_file_updater.find_plan_file
update_plan_checkboxes = plan_file_updater.update_plan_checkboxes
# _check_merge_success is a private wrapper; use common.is_merge_success for testing
from lib.repo import is_merge_success


def run_hook(stdin_data: dict, env_override: dict | None = None) -> tuple[int, str, str]:
    """Run the hook and return results."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def run_hook_with_mocked_branch(
    stdin_data: dict,
    project_dir: Path,
    mock_branch: str,
) -> subprocess.CompletedProcess[str]:
    """Run the hook with get_pr_branch mocked to return a specific branch.

    This runs the hook in a subprocess with dynamic patching of get_pr_branch.
    The patching is done via exec_module interception to inject the mock
    after module load but before main() execution.

    Args:
        stdin_data: Hook input data (tool_name, tool_input, tool_result)
        project_dir: Path to use as CLAUDE_PROJECT_DIR
        mock_branch: Branch name to return from mocked get_pr_branch

    Returns:
        CompletedProcess with returncode, stdout, stderr
    """
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)

    # Use inline Python to patch get_pr_branch before calling main()
    # This approach works because subprocess creates a fresh Python interpreter
    inline_code = f'''
import sys
import json
sys.path.insert(0, "{hook_path.parent}")

import importlib.util
spec = importlib.util.spec_from_file_location("plan_file_updater", "{hook_path}")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

# Patch get_pr_branch after module is loaded
module.get_pr_branch = lambda pr, root: "{mock_branch}"

module.main()
'''

    return subprocess.run(
        [sys.executable, "-c", inline_code],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class TestExtractPrNumberFromCommand:
    """Tests for extract_pr_number_from_command function."""

    def test_extracts_number_from_simple_command(self) -> None:
        """Should extract PR number from 'gh pr merge 123'."""
        assert extract_pr_number_from_command("gh pr merge 123") == 123

    def test_extracts_number_with_hash(self) -> None:
        """Should extract PR number from 'gh pr merge #123'."""
        assert extract_pr_number_from_command("gh pr merge #123") == 123

    def test_extracts_number_with_options(self) -> None:
        """Should extract PR number from command with options."""
        assert extract_pr_number_from_command("gh pr merge 456 --squash") == 456

    def test_returns_none_for_non_merge_command(self) -> None:
        """Should return None for non-merge commands."""
        assert extract_pr_number_from_command("gh pr view 123") is None

    def test_returns_none_for_empty_command(self) -> None:
        """Should return None for empty command."""
        assert extract_pr_number_from_command("") is None


class TestExtractIssueNumberFromBranch:
    """Tests for extract_issue_number_from_branch function."""

    def test_extracts_from_feat_branch(self) -> None:
        """Should extract Issue number from feat/issue-1336-xxx."""
        assert extract_issue_number_from_branch("feat/issue-1336-xxx") == "1336"

    def test_extracts_from_fix_branch(self) -> None:
        """Should extract Issue number from fix/issue-123-cleanup."""
        assert extract_issue_number_from_branch("fix/issue-123-cleanup") == "123"

    def test_extracts_from_simple_branch(self) -> None:
        """Should extract Issue number from issue-999."""
        assert extract_issue_number_from_branch("issue-999") == "999"

    def test_case_insensitive(self) -> None:
        """Should be case-insensitive."""
        assert extract_issue_number_from_branch("feat/Issue-456-test") == "456"

    def test_returns_none_for_no_issue(self) -> None:
        """Should return None when no issue pattern found."""
        assert extract_issue_number_from_branch("feature/new-feature") is None

    def test_returns_none_for_empty(self) -> None:
        """Should return None for empty string."""
        assert extract_issue_number_from_branch("") is None


class TestFindPlanFile:
    """Tests for find_plan_file function."""

    def test_finds_exact_match(self, tmp_path: Path) -> None:
        """Should find exact match issue-{number}.md."""
        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan_file = plans_dir / "issue-1336.md"
        plan_file.write_text("# Plan\n- [ ] Step 1")

        result = find_plan_file("1336", tmp_path)
        assert result == plan_file

    def test_finds_pattern_match(self, tmp_path: Path) -> None:
        """Should find pattern match when exact match not found."""
        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan_file = plans_dir / "some-issue-1336-plan.md"
        plan_file.write_text("# Plan\n- [ ] Step 1")

        result = find_plan_file("1336", tmp_path)
        assert result == plan_file

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        """Should return None when no matching file found."""
        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)

        result = find_plan_file("9999", tmp_path)
        assert result is None

    def test_returns_none_when_plans_dir_missing(self, tmp_path: Path) -> None:
        """Should return None when .claude/plans/ doesn't exist."""
        result = find_plan_file("1336", tmp_path)
        assert result is None

    def test_returns_newest_file_when_multiple_match(self, tmp_path: Path) -> None:
        """Should return newest file when multiple files match."""
        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)

        # Create older file with explicit mtime
        old_file = plans_dir / "old-issue-1336-plan.md"
        old_file.write_text("# Old Plan\n- [ ] Step 1")
        os.utime(old_file, (1000000, 1000000))  # Set older mtime

        # Create newer file with later mtime
        new_file = plans_dir / "new-issue-1336-plan.md"
        new_file.write_text("# New Plan\n- [ ] Step 1")
        os.utime(new_file, (2000000, 2000000))  # Set newer mtime

        result = find_plan_file("1336", tmp_path)
        assert result == new_file

    def test_exact_match_takes_priority_over_pattern(self, tmp_path: Path) -> None:
        """Should prioritize exact match over pattern match."""
        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)

        # Create pattern-matched file with newer mtime
        pattern_file = plans_dir / "some-issue-1336-plan.md"
        pattern_file.write_text("# Pattern Plan")
        os.utime(pattern_file, (2000000, 2000000))  # Newer mtime

        # Create exact match file (older mtime, but should still win)
        exact_file = plans_dir / "issue-1336.md"
        exact_file.write_text("# Exact Plan")
        os.utime(exact_file, (1000000, 1000000))  # Older mtime

        result = find_plan_file("1336", tmp_path)
        assert result == exact_file

    def test_home_plans_returns_newest_matching_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return newest file when multiple files match in ~/.claude/plans/."""
        # Create fake home directory
        fake_home = tmp_path / "fake_home"
        home_plans_dir = fake_home / ".claude" / "plans"
        home_plans_dir.mkdir(parents=True)

        # Mock Path.home() to return fake home
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        # Create older file with Issue reference
        old_file = home_plans_dir / "plan-abc123.md"
        old_file.write_text("# Plan for Issue #1336\n- [ ] Step 1")
        os.utime(old_file, (1000000, 1000000))

        # Create newer file with Issue reference
        new_file = home_plans_dir / "plan-def456.md"
        new_file.write_text("# Plan for Issue #1336\n- [ ] Step 2")
        os.utime(new_file, (2000000, 2000000))

        # Create unrelated file (should be ignored)
        unrelated = home_plans_dir / "plan-ghi789.md"
        unrelated.write_text("# Plan for Issue #9999\n- [ ] Other")
        os.utime(unrelated, (3000000, 3000000))

        # Should return newest matching file (new_file)
        result = find_plan_file("1336", tmp_path)
        assert result == new_file


class TestUpdatePlanCheckboxes:
    """Tests for update_plan_checkboxes function."""

    def test_updates_unchecked_boxes(self, tmp_path: Path) -> None:
        """Should update [ ] to [x]."""
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan\n- [ ] Step 1\n- [ ] Step 2\n")

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 2
        assert plan_file.read_text() == "# Plan\n- [x] Step 1\n- [x] Step 2\n"

    def test_leaves_checked_boxes_unchanged(self, tmp_path: Path) -> None:
        """Should not modify already checked boxes."""
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan\n- [x] Done\n- [ ] Todo\n")

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 1
        assert plan_file.read_text() == "# Plan\n- [x] Done\n- [x] Todo\n"

    def test_returns_false_when_all_checked(self, tmp_path: Path) -> None:
        """Should return False when no unchecked boxes."""
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan\n- [x] Done\n- [x] Also done\n")

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is False
        assert count == 0

    def test_handles_mixed_formats(self, tmp_path: Path) -> None:
        """Should handle both - [ ] and [ ] formats."""
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("1. [ ] Numbered\n- [ ] Bullet\n")

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 2
        assert plan_file.read_text() == "1. [x] Numbered\n- [x] Bullet\n"

    def test_returns_false_for_nonexistent_file(self, tmp_path: Path) -> None:
        """Should return False for nonexistent file."""
        plan_file = tmp_path / "nonexistent.md"

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is False
        assert count == 0

    def test_does_not_replace_checkbox_in_code_block(self, tmp_path: Path) -> None:
        """Should NOT replace checkboxes inside code blocks."""
        plan_file = tmp_path / "plan.md"
        content = """# Plan
- [ ] Real task

```markdown
- [ ] Example checkbox in code
```

- [ ] Another real task
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 2  # Only real tasks, not code block
        result = plan_file.read_text()
        assert "- [x] Real task" in result
        assert "- [x] Another real task" in result
        # Code block checkbox should remain unchecked
        assert "- [ ] Example checkbox in code" in result

    def test_does_not_replace_checkbox_in_unclosed_code_block(self, tmp_path: Path) -> None:
        """Should NOT replace checkboxes in unclosed fence code blocks."""
        plan_file = tmp_path / "plan.md"
        content = """# Plan
- [ ] Real task

```python
# This code block is never closed
- [ ] This should not be replaced
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 1  # Only the real task
        result = plan_file.read_text()
        assert "- [x] Real task" in result
        # Unclosed code block checkbox should remain unchecked
        assert "- [ ] This should not be replaced" in result

    def test_handles_closed_and_unclosed_code_blocks(self, tmp_path: Path) -> None:
        """Should handle both closed and unclosed code blocks correctly."""
        plan_file = tmp_path / "plan.md"
        content = """# Plan
- [ ] Task 1

```
closed block
```

- [ ] Task 2

```python
# unclosed block
- [ ] Should not replace
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 2  # Task 1 and Task 2
        result = plan_file.read_text()
        assert "- [x] Task 1" in result
        assert "- [x] Task 2" in result
        # Unclosed code block checkbox should remain unchecked
        assert "- [ ] Should not replace" in result

    def test_does_not_treat_inline_backticks_as_code_block(self, tmp_path: Path) -> None:
        """Should NOT treat inline triple backticks as code block start."""
        plan_file = tmp_path / "plan.md"
        content = """# Plan
- [ ] Task 1

Use ``` to start a code block in markdown.

- [ ] Task 2
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 2  # Both tasks should be updated
        result = plan_file.read_text()
        assert "- [x] Task 1" in result
        assert "- [x] Task 2" in result
        # Inline backticks should remain unchanged
        assert "Use ``` to start a code block" in result

    def test_does_not_replace_plain_text_checkbox(self, tmp_path: Path) -> None:
        """Should NOT replace checkboxes that are not list items."""
        plan_file = tmp_path / "plan.md"
        content = """# Plan
- [ ] Real task

Use [ ] for checkboxes in markdown.

- [ ] Another task
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 2  # Only list items
        result = plan_file.read_text()
        assert "- [x] Real task" in result
        assert "- [x] Another task" in result
        # Plain text checkbox should remain unchecked
        assert "Use [ ] for checkboxes" in result

    def test_handles_asterisk_list_items(self, tmp_path: Path) -> None:
        """Should handle * list items."""
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("* [ ] Asterisk item\n")

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 1
        assert plan_file.read_text() == "* [x] Asterisk item\n"

    def test_handles_plus_list_items(self, tmp_path: Path) -> None:
        """Should handle + list items."""
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("+ [ ] Plus item\n")

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 1
        assert plan_file.read_text() == "+ [x] Plus item\n"

    def test_handles_indented_list_items(self, tmp_path: Path) -> None:
        """Should handle indented nested list items."""
        plan_file = tmp_path / "plan.md"
        content = """- [ ] Parent
  - [ ] Child
    - [ ] Grandchild
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 3
        expected = """- [x] Parent
  - [x] Child
    - [x] Grandchild
"""
        assert plan_file.read_text() == expected

    def test_does_not_replace_checkbox_in_indented_code_block(self, tmp_path: Path) -> None:
        """Should NOT replace checkboxes in indented code blocks (plain code)."""
        plan_file = tmp_path / "plan.md"
        # Indented code block with plain code (no list markers)
        content = """# Plan
- [ ] Real task

    checkbox syntax: [ ] should not be touched
    another line with [ ] in code

- [ ] Another real task
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 2  # Only real tasks, not indented code block
        result = plan_file.read_text()
        assert "- [x] Real task" in result
        assert "- [x] Another real task" in result
        # Indented code block content should remain unchanged
        assert "    checkbox syntax: [ ] should not be touched" in result
        assert "    another line with [ ] in code" in result

    def test_does_not_replace_checkbox_in_indented_code_block_at_file_start(
        self, tmp_path: Path
    ) -> None:
        """Should NOT replace checkboxes in indented code blocks at file start."""
        plan_file = tmp_path / "plan.md"
        # File starts with indented code block (plain code, no list marker)
        content = """    code line 1
    code with [ ] checkbox syntax

- [ ] Real task
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 1  # Only real task
        result = plan_file.read_text()
        assert "- [x] Real task" in result
        # File-start indented code block should remain unchanged
        assert "    code with [ ] checkbox syntax" in result

    def test_handles_nested_list_after_blank_line(self, tmp_path: Path) -> None:
        """Should update nested list items even after blank lines."""
        plan_file = tmp_path / "plan.md"
        content = """# Plan
- [ ] Parent

    - [ ] Nested child after blank line

- [ ] Another task
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 3  # All list items including nested
        result = plan_file.read_text()
        assert "- [x] Parent" in result
        assert "    - [x] Nested child after blank line" in result
        assert "- [x] Another task" in result

    def test_does_not_replace_list_like_lines_in_indented_code_block(self, tmp_path: Path) -> None:
        """Issue #1566: Should NOT replace list-like lines in indented code blocks.

        When a blank line follows non-list content and precedes a 4+ space
        indented line, it's an indented code block even if the line looks
        like a list item (e.g., "    - [ ] item").
        """
        plan_file = tmp_path / "plan.md"
        # Non-list content followed by blank line, then indented list-like lines
        content = """# Plan
- [ ] Real task

Some explanation text here.

    - [ ] This looks like a list but is actually code
    * [ ] Also code with asterisk marker
    + [ ] Code with plus marker
    1. [ ] Code with numbered marker

- [ ] Another real task
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 2  # Only real tasks, not indented code block
        result = plan_file.read_text()
        assert "- [x] Real task" in result
        assert "- [x] Another real task" in result
        # All list-like lines in indented code block should remain unchanged
        assert "    - [ ] This looks like a list but is actually code" in result
        assert "    * [ ] Also code with asterisk marker" in result
        assert "    + [ ] Code with plus marker" in result
        assert "    1. [ ] Code with numbered marker" in result

    def test_does_not_replace_list_like_lines_at_file_start_code_block(
        self, tmp_path: Path
    ) -> None:
        """Issue #1566: Should NOT replace list-like lines in file-start code block."""
        plan_file = tmp_path / "plan.md"
        # File starts with indented code block containing list-like lines
        content = """    - [ ] This is code, not a task
    * [ ] Also code

- [ ] Real task
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 1  # Only real task
        result = plan_file.read_text()
        assert "- [x] Real task" in result
        # File-start code block with list-like lines should remain unchanged
        assert "    - [ ] This is code, not a task" in result
        assert "    * [ ] Also code" in result

    def test_continuous_indented_code_block_with_list_like_lines(self, tmp_path: Path) -> None:
        """Issue #1566: Continuous indented block from file start is code."""
        plan_file = tmp_path / "plan.md"
        content = """    - [ ] First code line
    - [ ] Second code line
    - [ ] Third code line

- [ ] Real task
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 1  # Only real task
        result = plan_file.read_text()
        assert "- [x] Real task" in result
        # All indented lines should remain unchanged
        assert "    - [ ] First code line" in result
        assert "    - [ ] Second code line" in result
        assert "    - [ ] Third code line" in result

    def test_handles_large_numbered_list_items(self, tmp_path: Path) -> None:
        """Should handle numbered list items with 3+ digits (100., 999., etc.)."""
        plan_file = tmp_path / "plan.md"
        content = """# Plan
100. [ ] Task 100
999. [ ] Task 999

    - [ ] Nested under large number

1000. [ ] Task 1000
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 4  # All list items including nested
        result = plan_file.read_text()
        assert "100. [x] Task 100" in result
        assert "999. [x] Task 999" in result
        assert "    - [x] Nested under large number" in result
        assert "1000. [x] Task 1000" in result

    def test_nested_list_after_fenced_code_block(self, tmp_path: Path) -> None:
        """Issue #1566: Should update nested list items after fenced code blocks.

        When a fenced code block appears within a list, nested items after
        the fenced block should still be updated (not treated as indented code).
        """
        plan_file = tmp_path / "plan.md"
        content = """# Plan
- [ ] Parent task
  ```python
  code example
  ```
    - [ ] Child task after fenced block
- [ ] Another task
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 3  # Parent, child, and another
        result = plan_file.read_text()
        assert "- [x] Parent task" in result
        assert "    - [x] Child task after fenced block" in result
        assert "- [x] Another task" in result

    def test_non_list_marker_without_space(self, tmp_path: Path) -> None:
        """Markers without space (like -text, *bold*) should not be treated as lists.

        Markdown requires at least one space after list markers.
        """
        plan_file = tmp_path / "plan.md"
        content = """# Plan
-text is not a list item
*bold* is not a list item either
+1 is just text

- [ ] Real task with space
"""
        plan_file.write_text(content)

        updated, count = update_plan_checkboxes(plan_file)

        assert updated is True
        assert count == 1  # Only the real task
        result = plan_file.read_text()
        assert "- [x] Real task with space" in result
        # Non-list lines should remain unchanged
        assert "-text is not a list item" in result
        assert "*bold* is not a list item either" in result
        assert "+1 is just text" in result


class TestIsMergeSuccess:
    """Tests for is_merge_success function (common.py version).

    Note: is_merge_success was moved to common.py and uses signature:
    is_merge_success(exit_code, stdout, command, *, stderr="")
    """

    def test_returns_true_for_exit_code_zero_empty_output(self) -> None:
        """Should return True when exit_code is 0 and output is empty (squash merge)."""
        assert is_merge_success(0, "", "gh pr merge 123") is True

    def test_returns_false_for_auto_merge(self) -> None:
        """Should return False for --auto flag (scheduled, not immediate)."""
        assert is_merge_success(0, "", "gh pr merge --auto 123") is False

    def test_returns_true_for_stdout_merge_pattern(self) -> None:
        """Should return True when stdout contains merge success pattern."""
        assert is_merge_success(0, "✓ Merged pull request #123", "gh pr merge 123") is True

    def test_returns_true_for_squash_merge(self) -> None:
        """Should return True for squash merge output."""
        assert (
            is_merge_success(0, "✓ Squashed and merged pull request #456", "gh pr merge 456")
            is True
        )

    def test_returns_true_for_rebase_merge(self) -> None:
        """Should return True for rebase merge output."""
        assert (
            is_merge_success(0, "✓ Rebased and merged pull request #789", "gh pr merge 789") is True
        )

    def test_returns_false_for_failed_merge(self) -> None:
        """Should return False when merge actually failed."""
        assert is_merge_success(1, "Pull request is not mergeable", "gh pr merge 123") is False

    def test_returns_false_for_unknown_output(self) -> None:
        """Should return False for unknown output with exit code 0."""
        assert is_merge_success(0, "Some unknown output", "gh pr merge 123") is False


class TestMainIntegration:
    """Integration tests for main function."""

    def test_no_output_for_non_bash_tool(self) -> None:
        """Should produce no output for non-Bash tool."""
        stdin_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file"},
            "tool_result": {},
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        assert stdout == ""

    def test_no_output_for_non_merge_command(self) -> None:
        """Should produce no output for non-merge command."""
        stdin_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr view 123"},
            "tool_result": {"exit_code": 0},
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        assert stdout == ""

    def test_no_output_for_failed_merge(self) -> None:
        """Should produce no output when merge fails."""
        stdin_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_result": {"exit_code": 1, "stdout": "error: merge failed"},
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        assert stdout == ""

    def test_handles_empty_input(self) -> None:
        """Should handle empty input gracefully."""
        returncode, stdout, _ = run_hook({})
        assert returncode == 0
        assert stdout == ""

    def test_handles_missing_tool_input(self) -> None:
        """Should handle missing tool_input."""
        stdin_data = {
            "tool_name": "Bash",
            "tool_result": {"exit_code": 0},
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        assert stdout == ""

    def test_successful_checkbox_update_outputs_system_message(self, tmp_path: Path) -> None:
        """Should output systemMessage when checkboxes are updated successfully.

        This is the full integration test for the success case:
        1. gh pr merge succeeds
        2. PR branch contains issue number
        3. Plan file is found
        4. Checkboxes are updated
        5. systemMessage is output
        """
        # Create repo structure with plan file
        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan_file = plans_dir / "issue-999.md"
        plan_file.write_text("# Plan\n\n- [ ] Task 1\n- [ ] Task 2\n")

        # Create .git directory to simulate a git repo
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        stdin_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_result": {"exit_code": 0},
        }

        result = run_hook_with_mocked_branch(
            stdin_data,
            tmp_path,
            "feat/issue-999-add-feature",
        )

        assert result.returncode == 0

        # Verify checkboxes were updated
        updated_content = plan_file.read_text()
        assert "- [x] Task 1" in updated_content
        assert "- [x] Task 2" in updated_content

        # Verify systemMessage was output (must always be present on success)
        assert result.stdout.strip(), "Expected systemMessage output but got none"
        output = json.loads(result.stdout)
        assert output.get("continue") is True
        assert "systemMessage" in output
        assert "issue-999.md" in output["systemMessage"]


class TestEndToEndPlanNotFound:
    """End-to-end test when plan file is not found."""

    def test_no_output_when_plan_file_not_found(self, tmp_path: Path) -> None:
        """Should produce no output when plan file doesn't exist."""
        # Create repo structure without plan file
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        stdin_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_result": {"exit_code": 0},
        }

        result = run_hook_with_mocked_branch(
            stdin_data,
            tmp_path,
            "feat/issue-888-no-plan",
        )

        assert result.returncode == 0
        # No output when plan file not found
        assert result.stdout.strip() == ""
