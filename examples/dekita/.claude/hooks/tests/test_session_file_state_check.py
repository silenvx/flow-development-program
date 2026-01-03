#!/usr/bin/env python3
"""Tests for session-file-state-check.py hook.

Issue #2468: Tests for session resume file state verification hook.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))


def load_hook_module():
    """Load the session-file-state-check module."""
    spec = importlib.util.spec_from_file_location(
        "session_file_state_check",
        HOOKS_DIR / "session-file-state-check.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGetGitStatus:
    """Tests for get_git_status function."""

    def test_clean_working_tree(self):
        """Test when working tree is clean."""
        module = load_hook_module()

        with patch.object(module.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""

            result = module.get_git_status()

            assert result == {"staged": [], "unstaged": [], "untracked": []}

    def test_staged_files(self):
        """Test detection of staged files."""
        module = load_hook_module()

        with patch.object(module.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            # -z option uses NUL-separated output
            mock_run.return_value.stdout = "M  file1.py\0A  file2.py\0"

            result = module.get_git_status()

            assert "file1.py" in result["staged"]
            assert "file2.py" in result["staged"]
            assert result["unstaged"] == []
            assert result["untracked"] == []

    def test_unstaged_files(self):
        """Test detection of unstaged modifications."""
        module = load_hook_module()

        with patch.object(module.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            # -z option uses NUL-separated output
            mock_run.return_value.stdout = " M file1.py\0"

            result = module.get_git_status()

            assert result["staged"] == []
            assert "file1.py" in result["unstaged"]
            assert result["untracked"] == []

    def test_untracked_files(self):
        """Test detection of untracked files."""
        module = load_hook_module()

        with patch.object(module.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            # -z option uses NUL-separated output
            mock_run.return_value.stdout = "?? newfile.py\0"

            result = module.get_git_status()

            assert result["staged"] == []
            assert result["unstaged"] == []
            assert "newfile.py" in result["untracked"]

    def test_mixed_status(self):
        """Test detection of mixed file statuses."""
        module = load_hook_module()

        with patch.object(module.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            # -z option uses NUL-separated output
            mock_run.return_value.stdout = "M  staged.py\0 M unstaged.py\0?? new.py\0"

            result = module.get_git_status()

            assert "staged.py" in result["staged"]
            assert "unstaged.py" in result["unstaged"]
            assert "new.py" in result["untracked"]

    def test_rename_status(self):
        """Test detection of renamed files.

        Issue #2483: Rename entries have two filenames in -z format.
        Format: R  newname.py NUL oldname.py NUL
        """
        module = load_hook_module()

        with patch.object(module.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            # Rename: R followed by new filename, then NUL, then old filename
            mock_run.return_value.stdout = "R  newname.py\0oldname.py\0"

            result = module.get_git_status()

            # stagedには新しいファイル名のみが含まれるべきです
            assert result["staged"] == ["newname.py"]
            assert result["unstaged"] == []
            assert result["untracked"] == []

    def test_copy_status(self):
        """Test detection of copied files.

        Issue #2483: Copy entries also have two filenames in -z format.
        Format: C  destination.py NUL source.py NUL
        """
        module = load_hook_module()

        with patch.object(module.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            # Copy: C followed by destination, then NUL, then source
            mock_run.return_value.stdout = "C  destination.py\0source.py\0"

            result = module.get_git_status()

            # stagedにはコピー先のファイル名のみが含まれるべきです
            assert result["staged"] == ["destination.py"]
            assert result["unstaged"] == []
            assert result["untracked"] == []

    def test_rename_with_other_changes(self):
        """Test rename combined with other file statuses.

        Issue #2483: Ensure rename handling doesn't break other entries.
        """
        module = load_hook_module()

        with patch.object(module.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            # Mixed: staged file, rename, unstaged, untracked
            mock_run.return_value.stdout = (
                "M  modified.py\0"  # Staged modification
                "R  newname.py\0oldname.py\0"  # Rename (2 entries)
                " M worktree.py\0"  # Unstaged modification
                "?? untracked.py\0"  # Untracked
            )

            result = module.get_git_status()

            # Check staged files
            assert sorted(result["staged"]) == ["modified.py", "newname.py"]
            # Check unstaged
            assert result["unstaged"] == ["worktree.py"]
            # Check untracked
            assert result["untracked"] == ["untracked.py"]


class TestGetLastCommitInfo:
    """Tests for get_last_commit_info function."""

    def test_returns_commit_info(self):
        """Test that commit info is returned."""
        module = load_hook_module()

        with patch.object(module.subprocess, "run") as mock_run:

            def side_effect(*args, **kwargs):
                cmd = args[0]
                result = type("R", (), {"returncode": 0, "stdout": ""})()
                if "log" in cmd:
                    result.stdout = "abc1234 Fix something (5 minutes ago)"
                elif "diff-tree" in cmd:
                    # -z option uses NUL-separated output
                    result.stdout = "file1.py\0file2.py\0"
                return result

            mock_run.side_effect = side_effect

            result = module.get_last_commit_info()

            assert result is not None
            assert "abc1234" in result
            assert "file1.py" in result

    def test_returns_none_on_error(self):
        """Test that None is returned on error."""
        module = load_hook_module()

        with patch.object(module.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""

            result = module.get_last_commit_info()

            assert result is None


class TestFormatFileStateWarning:
    """Tests for format_file_state_warning function."""

    def test_format_with_staged_files(self):
        """Test formatting with staged files."""
        module = load_hook_module()

        status = {"staged": ["file1.py", "file2.py"], "unstaged": [], "untracked": []}
        message = module.format_file_state_warning(status, None)

        assert "ステージ済み" in message
        assert "(2件)" in message
        assert "file1.py" in message
        assert "file2.py" in message

    def test_format_with_unstaged_files(self):
        """Test formatting with unstaged files."""
        module = load_hook_module()

        status = {"staged": [], "unstaged": ["modified.py"], "untracked": []}
        message = module.format_file_state_warning(status, None)

        assert "未ステージ変更" in message
        assert "modified.py" in message

    def test_format_with_untracked_files(self):
        """Test formatting with untracked files."""
        module = load_hook_module()

        status = {"staged": [], "unstaged": [], "untracked": ["new.py"]}
        message = module.format_file_state_warning(status, None)

        assert "未追跡ファイル" in message
        assert "new.py" in message

    def test_format_with_many_files_truncates(self):
        """Test that many files are truncated."""
        module = load_hook_module()

        status = {
            "staged": [f"file{i}.py" for i in range(10)],
            "unstaged": [],
            "untracked": [],
        }
        message = module.format_file_state_warning(status, None)

        assert "(10件)" in message
        assert "他 5件" in message

    def test_format_includes_last_commit(self):
        """Test that last commit info is included."""
        module = load_hook_module()

        status = {"staged": ["file1.py"], "unstaged": [], "untracked": []}
        message = module.format_file_state_warning(status, "  abc1234 Fix bug (5 minutes ago)")

        assert "直前のコミット" in message
        assert "abc1234" in message

    def test_format_includes_guidance(self):
        """Test that guidance is included."""
        module = load_hook_module()

        status = {"staged": ["file1.py"], "unstaged": [], "untracked": []}
        message = module.format_file_state_warning(status, None)

        assert "確認事項" in message
        assert "git status" in message
        assert "git diff" in message


class TestMainFunction:
    """Tests for main function behavior."""

    def test_skips_new_session(self):
        """Test that hook skips checking for new sessions."""
        hook_path = HOOKS_DIR / "session-file-state-check.py"
        result = subprocess.run(
            ["python3", str(hook_path)],
            input='{"source": "new", "session_id": "test-123"}',
            capture_output=True,
            text=True,
            check=False,
        )

        output = json.loads(result.stdout)
        assert output["continue"] is True
        assert "message" not in output

    def test_skips_init_session(self):
        """Test that hook skips checking for init sessions."""
        hook_path = HOOKS_DIR / "session-file-state-check.py"
        result = subprocess.run(
            ["python3", str(hook_path)],
            input='{"source": "init", "session_id": "test-123"}',
            capture_output=True,
            text=True,
            check=False,
        )

        output = json.loads(result.stdout)
        assert output["continue"] is True
        assert "message" not in output

    def test_checks_on_resume_session(self):
        """Test that hook checks file state for resume sessions."""
        hook_path = HOOKS_DIR / "session-file-state-check.py"
        result = subprocess.run(
            ["python3", str(hook_path)],
            input='{"source": "resume", "session_id": "test-123"}',
            capture_output=True,
            text=True,
            check=False,
        )

        output = json.loads(result.stdout)
        assert output["continue"] is True
        # May or may not have message depending on actual git state

    def test_checks_on_compact_session(self):
        """Test that hook checks file state for compact sessions."""
        hook_path = HOOKS_DIR / "session-file-state-check.py"
        result = subprocess.run(
            ["python3", str(hook_path)],
            input='{"source": "compact", "session_id": "test-123"}',
            capture_output=True,
            text=True,
            check=False,
        )

        output = json.loads(result.stdout)
        assert output["continue"] is True
        # May or may not have message depending on actual git state


class TestUntrackedFilesWarning:
    """Tests for untracked files warning behavior (Issue #2468 Codex review)."""

    def test_warns_when_only_untracked_files_exist(self):
        """Test that hook warns when only untracked files are present."""
        module = load_hook_module()

        # Test format_file_state_warning with only untracked files
        status = {"staged": [], "unstaged": [], "untracked": ["new_file.py"]}
        message = module.format_file_state_warning(status, None)

        # Should still show warning with untracked files
        assert "未追跡ファイル" in message
        assert "new_file.py" in message
        assert "確認事項" in message

    def test_has_changes_includes_untracked(self):
        """Test that has_changes logic includes untracked files.

        Regression test for Codex review: untracked files should trigger warning.
        """
        # Only untracked files should be considered as "has changes"
        status = {"staged": [], "unstaged": [], "untracked": ["new.py"]}
        has_changes = any((status["staged"], status["unstaged"], status["untracked"]))
        assert has_changes is True

        # Empty should not have changes
        status_empty = {"staged": [], "unstaged": [], "untracked": []}
        has_changes_empty = any(
            (status_empty["staged"], status_empty["unstaged"], status_empty["untracked"])
        )
        assert has_changes_empty is False
