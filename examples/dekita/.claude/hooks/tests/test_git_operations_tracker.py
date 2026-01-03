#!/usr/bin/env python3
"""Tests for git-operations-tracker.py hook."""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "git-operations-tracker.py"


def load_module():
    """Load the hook module for testing."""
    parent_dir = str(Path(__file__).parent.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    spec = importlib.util.spec_from_file_location("git_operations_tracker", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestGitOperationDetection:
    """Tests for git operation command detection."""

    def setup_method(self):
        self.module = load_module()

    def test_detect_git_pull(self):
        """Should detect git pull command."""
        assert self.module.is_git_operation_command("git pull")
        assert self.module.is_git_operation_command("git pull origin main")
        assert self.module.is_git_operation_command("git pull --rebase")

    def test_detect_git_merge(self):
        """Should detect git merge command."""
        assert self.module.is_git_operation_command("git merge main")
        assert self.module.is_git_operation_command("git merge --no-ff feature")

    def test_detect_git_rebase(self):
        """Should detect git rebase command."""
        assert self.module.is_git_operation_command("git rebase main")
        assert self.module.is_git_operation_command("git rebase -i HEAD~3")

    def test_detect_gh_pr_merge(self):
        """Should detect gh pr merge command."""
        assert self.module.is_git_operation_command("gh pr merge")
        assert self.module.is_git_operation_command("gh pr merge 123")
        assert self.module.is_git_operation_command("gh pr merge --squash")

    def test_detect_gh_pr_update_branch(self):
        """Should detect gh pr update-branch command."""
        assert self.module.is_git_operation_command("gh pr update-branch")

    def test_non_git_commands(self):
        """Should not match non-git commands."""
        assert not self.module.is_git_operation_command("ls -la")
        assert not self.module.is_git_operation_command("git status")
        assert not self.module.is_git_operation_command("git commit -m 'msg'")
        assert not self.module.is_git_operation_command("")


class TestConflictDetection:
    """Tests for conflict detection."""

    def setup_method(self):
        self.module = load_module()

    def test_detect_conflict_patterns(self):
        """Should detect various conflict patterns."""
        conflict_outputs = [
            "CONFLICT (content): Merge conflict in file.txt",
            "Automatic merge failed; fix conflicts and then commit",
            "error: merge conflict in src/app.py",
            "Unmerged files:\n  both modified: config.json",
            "both added: newfile.txt",
        ]
        for output in conflict_outputs:
            with self.subTest(output=output[:50]):
                assert self.module.detect_conflict(output)

    def test_no_conflict_patterns(self):
        """Should not detect conflict when none exists."""
        normal_outputs = [
            "Updating abc123..def456",
            "Fast-forward",
            "Already up to date.",
            "3 files changed, 10 insertions(+), 2 deletions(-)",
        ]
        for output in normal_outputs:
            with self.subTest(output=output[:50]):
                assert not self.module.detect_conflict(output)


class TestUpdateBranchDetection:
    """Tests for update branch detection."""

    def setup_method(self):
        self.module = load_module()

    def test_detect_update_patterns(self):
        """Should detect various update branch patterns."""
        assert self.module.detect_update_branch("git pull", "Updating abc1234..def5678")
        assert self.module.detect_update_branch("git pull", "Fast-forward")
        assert self.module.detect_update_branch("git pull", "Already up to date.")
        assert self.module.detect_update_branch("gh pr update-branch", "Branch updated")

    def test_explicit_update_branch_command(self):
        """Should detect gh pr update-branch command."""
        assert self.module.detect_update_branch("gh pr update-branch", "some output")


class TestRebaseDetection:
    """Tests for rebase detection."""

    def setup_method(self):
        self.module = load_module()

    def test_detect_rebase_command(self):
        """Should detect rebase in command."""
        assert self.module.detect_rebase("git rebase main", "")
        assert self.module.detect_rebase("git rebase -i HEAD~3", "")

    def test_detect_rebase_output(self):
        """Should detect rebase in output."""
        assert self.module.detect_rebase("git pull", "Rebasing (1/3)")
        assert self.module.detect_rebase("git pull", "Successfully rebased and updated")


class TestHookExecution:
    """Tests for hook execution."""

    def test_always_continues(self):
        """PostToolUse hook should always continue."""
        test_cases = [
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git pull"},
                "tool_result": {"stdout": "Already up to date.", "exit_code": 0},
            },
            {
                "tool_name": "Bash",
                "tool_input": {"command": "gh pr merge --squash"},
                "tool_result": {"stdout": "Merged", "exit_code": 0},
            },
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
                "tool_result": {"stdout": "file1\nfile2", "exit_code": 0},
            },
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/test"},
                "tool_result": {},
            },
        ]

        for input_data in test_cases:
            with self.subTest(command=input_data.get("tool_input", {}).get("command")):
                result = run_hook(input_data)
                assert result.get("continue", False)

    def test_handles_invalid_json(self):
        """Should handle invalid JSON gracefully."""
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input="not valid json",
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)
        assert output.get("continue", False)

    def test_skips_non_bash_tools(self):
        """Should skip non-Bash tools."""
        result = run_hook(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/test"},
                "tool_result": {},
            }
        )
        assert result.get("continue", False)


class TestExtractConflictFiles:
    """Tests for extract_conflict_files function (Issue #1689)."""

    def setup_method(self):
        self.module = load_module()

    def test_extract_from_conflict_content(self):
        """Should extract files from CONFLICT (content) pattern."""
        output = """
CONFLICT (content): Merge conflict in src/app.py
CONFLICT (content): Merge conflict in config.json
Auto-merging README.md
"""
        files = self.module.extract_conflict_files(output)
        assert "src/app.py" in files
        assert "config.json" in files
        assert len(files) == 2

    def test_extract_from_both_modified(self):
        """Should extract files from 'both modified' pattern."""
        output = """
Unmerged files:
  both modified: src/app.py
  both modified: tests/test_app.py
"""
        files = self.module.extract_conflict_files(output)
        assert "src/app.py" in files
        assert "tests/test_app.py" in files
        assert len(files) == 2

    def test_extract_from_both_added(self):
        """Should extract files from 'both added' pattern."""
        output = """
CONFLICT (add/add): Merge conflict in newfile.txt
Automatic merge failed; fix conflicts and then commit the result.
  both added: newfile.txt
"""
        files = self.module.extract_conflict_files(output)
        assert "newfile.txt" in files
        assert len(files) == 1

    def test_extract_from_both_deleted(self):
        """Should extract files from 'both deleted' pattern."""
        output = """
Unmerged files:
  both deleted: old_file.txt
"""
        files = self.module.extract_conflict_files(output)
        assert "old_file.txt" in files
        assert len(files) == 1

    def test_extract_quoted_filenames_with_spaces(self):
        """Should extract files with spaces when quoted."""
        output = """
CONFLICT (content): Merge conflict in "my file.txt"
  both modified: "another file.py"
"""
        files = self.module.extract_conflict_files(output)
        assert "my file.txt" in files
        assert "another file.py" in files
        assert len(files) == 2

    def test_no_duplicates(self):
        """Should not return duplicate files."""
        output = """
CONFLICT (content): Merge conflict in file.py
  both modified: file.py
"""
        files = self.module.extract_conflict_files(output)
        assert files.count("file.py") == 1

    def test_empty_output(self):
        """Should return empty list for no conflicts."""
        files = self.module.extract_conflict_files("")
        assert files == []

    def test_extract_from_deleted_by_us(self):
        """Should extract files from 'deleted by us' pattern (Issue #1706)."""
        output = """
  deleted by us: src/old_module.py
"""
        files = self.module.extract_conflict_files(output)
        assert files == ["src/old_module.py"]

    def test_extract_from_deleted_by_them(self):
        """Should extract files from 'deleted by them' pattern (Issue #1706)."""
        output = """
  deleted by them: lib/deprecated.py
"""
        files = self.module.extract_conflict_files(output)
        assert files == ["lib/deprecated.py"]

    def test_extract_deleted_by_us_quoted_filename(self):
        """Should extract quoted filenames from 'deleted by us' pattern (Issue #1706)."""
        output = """
  deleted by us: "path with spaces/file.py"
"""
        files = self.module.extract_conflict_files(output)
        assert files == ["path with spaces/file.py"]

    def test_extract_deleted_by_them_quoted_filename(self):
        """Should extract quoted filenames from 'deleted by them' pattern (Issue #1706)."""
        output = """
  deleted by them: "another path/special file.txt"
"""
        files = self.module.extract_conflict_files(output)
        assert files == ["another path/special file.txt"]

    def test_extract_mixed_conflict_types(self):
        """Should extract files from all conflict types including deleted by us/them (Issue #1706)."""
        output = """
CONFLICT (content): Merge conflict in src/main.py
  both modified: src/common.py
  deleted by us: src/removed.py
  deleted by them: src/also_removed.py
  both added: src/newfile.py
"""
        files = self.module.extract_conflict_files(output)
        expected_files = [
            "src/also_removed.py",
            "src/common.py",
            "src/main.py",
            "src/newfile.py",
            "src/removed.py",
        ]
        assert files == expected_files


class TestRebaseResolutionDetection:
    """Tests for detect_rebase_resolution function (Issue #1689)."""

    def setup_method(self):
        self.module = load_module()

    def test_detect_skip(self):
        """Should detect git rebase --skip."""
        assert self.module.detect_rebase_resolution("git rebase --skip") == "skip"

    def test_detect_continue(self):
        """Should detect git rebase --continue."""
        assert self.module.detect_rebase_resolution("git rebase --continue") == "continue"

    def test_detect_abort(self):
        """Should detect git rebase --abort."""
        assert self.module.detect_rebase_resolution("git rebase --abort") == "abort"

    def test_no_resolution_regular_rebase(self):
        """Should return None for regular rebase command."""
        assert self.module.detect_rebase_resolution("git rebase main") is None
        assert self.module.detect_rebase_resolution("git rebase -i HEAD~3") is None

    def test_no_resolution_other_commands(self):
        """Should return None for non-rebase commands."""
        assert self.module.detect_rebase_resolution("git pull") is None
        assert self.module.detect_rebase_resolution("git merge main") is None

    def test_detect_with_additional_options(self):
        """Should detect resolution in various command contexts."""
        # --continue followed by other text
        assert (
            self.module.detect_rebase_resolution("git rebase --continue && echo done") == "continue"
        )
        # --skip with trailing content
        assert self.module.detect_rebase_resolution("git rebase --skip 2>&1") == "skip"
        # --abort at end of longer command
        assert self.module.detect_rebase_resolution("cd /repo && git rebase --abort") == "abort"


class TestLogFileCreation:
    """Tests for log file creation."""

    def test_log_git_operation(self):
        """Test that log_git_operation creates log entries."""
        module = load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = module.EXECUTION_LOG_DIR
            original_git_operations_log = module.GIT_OPERATIONS_LOG
            module.EXECUTION_LOG_DIR = Path(tmpdir)
            module.GIT_OPERATIONS_LOG = Path(tmpdir) / "git-operations.log"

            try:
                module.log_git_operation(
                    "conflict",
                    "git merge main",
                    success=False,
                    details={"exit_code": 1},
                )

                log_file = Path(tmpdir) / "git-operations.log"
                assert log_file.exists()

                with open(log_file) as f:
                    entry = json.loads(f.read().strip())

                assert entry["type"] == "git_operation"
                assert entry["operation"] == "conflict"
                assert entry["command"] == "git merge main"
                assert not entry["success"]
                assert entry["details"]["exit_code"] == 1
            finally:
                module.EXECUTION_LOG_DIR = original_log_dir
                module.GIT_OPERATIONS_LOG = original_git_operations_log
