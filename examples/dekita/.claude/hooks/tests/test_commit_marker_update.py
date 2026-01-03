"""Tests for commit-marker-update.py hook (Issue #960)."""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

# Load the hook module dynamically (it has hyphens in the name)
HOOK_PATH = Path(__file__).parent.parent / "commit-marker-update.py"


@pytest.fixture
def hook_module():
    """Load the hook module."""
    spec = importlib.util.spec_from_file_location("commit_marker_update", str(HOOK_PATH))
    module = importlib.util.module_from_spec(spec)
    # Add hooks directory to path for common imports
    hooks_dir = str(Path(__file__).parent.parent)
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    spec.loader.exec_module(module)
    return module


class TestIsGitCommitCommand:
    """Tests for is_git_commit_command function."""

    def test_simple_git_commit(self, hook_module):
        """単純な git commit を検出"""
        assert hook_module.is_git_commit_command("git commit")

    def test_git_commit_with_message(self, hook_module):
        """git commit -m "msg" を検出"""
        assert hook_module.is_git_commit_command('git commit -m "test message"')

    def test_git_commit_amend(self, hook_module):
        """git commit --amend を検出"""
        assert hook_module.is_git_commit_command("git commit --amend")

    def test_git_commit_all(self, hook_module):
        """git commit -a を検出"""
        assert hook_module.is_git_commit_command("git commit -a -m 'msg'")

    def test_git_add_and_commit_chain(self, hook_module):
        """git add && git commit を検出"""
        assert hook_module.is_git_commit_command('git add . && git commit -m "test"')

    def test_git_status_semicolon_commit(self, hook_module):
        """git status; git commit を検出"""
        assert hook_module.is_git_commit_command("git status; git commit")

    def test_git_add_or_commit_chain(self, hook_module):
        """false || git commit を検出"""
        assert hook_module.is_git_commit_command("false || git commit -m 'test'")

    def test_triple_chain_with_commit(self, hook_module):
        """git add && git commit && git push を検出"""
        assert hook_module.is_git_commit_command('git add . && git commit -m "msg" && git push')

    def test_echo_git_commit_ignored(self, hook_module):
        """echo "git commit" は検出しない"""
        assert not hook_module.is_git_commit_command('echo "git commit"')

    def test_printf_git_commit_ignored(self, hook_module):
        """printf 'git commit' は検出しない"""
        assert not hook_module.is_git_commit_command("printf 'git commit'")

    def test_non_commit_command(self, hook_module):
        """git status は検出しない"""
        assert not hook_module.is_git_commit_command("git status")

    def test_git_push_chain(self, hook_module):
        """git add && git push は検出しない"""
        assert not hook_module.is_git_commit_command("git add . && git push")

    def test_empty_command(self, hook_module):
        """空コマンドは検出しない"""
        assert not hook_module.is_git_commit_command("")

    def test_whitespace_only_command(self, hook_module):
        """空白のみは検出しない"""
        assert not hook_module.is_git_commit_command("   ")


class TestUpdateMarker:
    """Tests for update_marker function."""

    def test_updates_existing_marker(self, hook_module, tmp_path):
        """既存マーカーを更新"""
        # Create markers directory and file
        markers_dir = tmp_path / "markers"
        markers_dir.mkdir()
        marker_file = markers_dir / "codex-review-test-branch.done"
        marker_file.write_text("test-branch:old123:oldhash")

        with patch.object(hook_module, "MARKERS_LOG_DIR", markers_dir):
            with patch.object(hook_module, "sanitize_branch_name", return_value="test-branch"):
                result = hook_module.update_marker("test-branch", "new456", "newhash123")

        assert result is True
        assert marker_file.read_text() == "test-branch:new456:newhash123"

    def test_no_update_if_no_marker(self, hook_module, tmp_path):
        """マーカーがなければ何もしない"""
        markers_dir = tmp_path / "markers"
        markers_dir.mkdir()

        with patch.object(hook_module, "MARKERS_LOG_DIR", markers_dir):
            with patch.object(hook_module, "sanitize_branch_name", return_value="test-branch"):
                result = hook_module.update_marker("test-branch", "abc123", "diffhash")

        assert result is False
        assert not (markers_dir / "codex-review-test-branch.done").exists()

    def test_marker_format(self, hook_module, tmp_path):
        """branch:commit:diff_hash 形式で保存"""
        markers_dir = tmp_path / "markers"
        markers_dir.mkdir()
        marker_file = markers_dir / "codex-review-feat-issue-123.done"
        marker_file.write_text("feat/issue-123:oldcommit:olddiff")

        with patch.object(hook_module, "MARKERS_LOG_DIR", markers_dir):
            with patch.object(hook_module, "sanitize_branch_name", return_value="feat-issue-123"):
                hook_module.update_marker("feat/issue-123", "abc1234", "diffhash5678")

        content = marker_file.read_text()
        parts = content.split(":")
        assert len(parts) == 3
        assert parts[0] == "feat/issue-123"  # Original branch name
        assert parts[1] == "abc1234"  # Commit hash
        assert parts[2] == "diffhash5678"  # Diff hash

    def test_updates_marker_with_empty_file(self, hook_module, tmp_path):
        """空のマーカーファイルを正しい形式で上書き (Issue #983)"""
        markers_dir = tmp_path / "markers"
        markers_dir.mkdir()
        marker_file = markers_dir / "codex-review-feat-test.done"
        marker_file.write_text("")  # Empty file

        with patch.object(hook_module, "MARKERS_LOG_DIR", markers_dir):
            with patch.object(hook_module, "sanitize_branch_name", return_value="feat-test"):
                result = hook_module.update_marker("feat/test", "newcommit", "newdiff")

        assert result is True
        assert marker_file.read_text() == "feat/test:newcommit:newdiff"

    def test_updates_marker_with_malformed_content(self, hook_module, tmp_path):
        """不正形式のマーカーファイルを正しい形式で上書き (Issue #983)

        コロンが不足している不正な形式でも、正しい形式で上書きされる。
        """
        markers_dir = tmp_path / "markers"
        markers_dir.mkdir()
        marker_file = markers_dir / "codex-review-feat-test.done"
        marker_file.write_text("invalid-no-colons")  # Malformed content

        with patch.object(hook_module, "MARKERS_LOG_DIR", markers_dir):
            with patch.object(hook_module, "sanitize_branch_name", return_value="feat-test"):
                result = hook_module.update_marker("feat/test", "newcommit", "newdiff")

        assert result is True
        assert marker_file.read_text() == "feat/test:newcommit:newdiff"


class TestMainFunction:
    """Tests for main function behavior."""

    def test_skips_non_bash_tool(self, hook_module, capsys):
        """Bash以外のツールはスキップ"""
        hook_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/some/file"},
            "tool_result": {},
        }

        with patch.object(hook_module, "parse_hook_input", return_value=hook_input):
            hook_module.main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}

    def test_skips_non_commit_command(self, hook_module, capsys):
        """git commit以外のコマンドはスキップ"""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_result": {"exit_code": 0},
        }

        with patch.object(hook_module, "parse_hook_input", return_value=hook_input):
            hook_module.main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}

    def test_skips_when_head_unchanged(self, hook_module, capsys, tmp_path):
        """HEADが変わっていない場合はスキップ"""
        markers_dir = tmp_path / "markers"
        markers_dir.mkdir()
        marker_file = markers_dir / "codex-review-feat-test.done"
        marker_file.write_text("feat/test:samecommit:olddiff")

        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_result": {"exit_code": 1},  # exit_code is ignored now
        }

        # Use ExitStack to reduce nesting while keeping proper mock behavior
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(hook_module, "parse_hook_input", return_value=hook_input)
            )
            stack.enter_context(
                patch.object(hook_module, "get_current_branch", return_value="feat/test")
            )
            stack.enter_context(
                patch.object(hook_module, "get_head_commit", return_value="samecommit")
            )
            stack.enter_context(patch.object(hook_module, "get_diff_hash", return_value="newdiff"))
            stack.enter_context(patch.object(hook_module, "MARKERS_LOG_DIR", markers_dir))
            stack.enter_context(
                patch.object(hook_module, "sanitize_branch_name", return_value="feat-test")
            )
            stack.enter_context(patch.object(hook_module, "log_hook_execution"))
            hook_module.main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}
        # Marker should NOT be updated
        assert marker_file.read_text() == "feat/test:samecommit:olddiff"

    def test_skips_main_branch(self, hook_module, capsys):
        """mainブランチではスキップ"""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_result": {"exit_code": 0},
        }

        with patch.object(hook_module, "parse_hook_input", return_value=hook_input):
            with patch.object(hook_module, "get_current_branch", return_value="main"):
                hook_module.main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}

    def test_skips_master_branch(self, hook_module, capsys):
        """masterブランチではスキップ"""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_result": {"exit_code": 0},
        }

        with patch.object(hook_module, "parse_hook_input", return_value=hook_input):
            with patch.object(hook_module, "get_current_branch", return_value="master"):
                hook_module.main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}

    def test_updates_marker_on_successful_commit(self, hook_module, capsys, tmp_path):
        """成功したコミットでマーカーを更新"""
        markers_dir = tmp_path / "markers"
        markers_dir.mkdir()
        marker_file = markers_dir / "codex-review-feat-test.done"
        marker_file.write_text("feat/test:oldcommit:olddiff")

        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_result": {"exit_code": 0},
        }

        # Use ExitStack to reduce nesting (addresses Copilot review comment)
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(hook_module, "parse_hook_input", return_value=hook_input)
            )
            stack.enter_context(
                patch.object(hook_module, "get_current_branch", return_value="feat/test")
            )
            stack.enter_context(
                patch.object(hook_module, "get_head_commit", return_value="newcommit")
            )
            stack.enter_context(
                patch.object(hook_module, "get_diff_hash", return_value="newdiffhash")
            )
            stack.enter_context(patch.object(hook_module, "MARKERS_LOG_DIR", markers_dir))
            stack.enter_context(
                patch.object(hook_module, "sanitize_branch_name", return_value="feat-test")
            )
            stack.enter_context(patch.object(hook_module, "log_hook_execution"))
            hook_module.main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}
        assert marker_file.read_text() == "feat/test:newcommit:newdiffhash"

    def test_updates_marker_even_with_nonzero_exit_code(self, hook_module, capsys, tmp_path):
        """exit_code != 0 でもHEADが変わっていればマーカーを更新

        git commit && git push でpushが失敗した場合、exit_code=1 だが
        commitは成功しているのでマーカーを更新すべき。
        """
        markers_dir = tmp_path / "markers"
        markers_dir.mkdir()
        marker_file = markers_dir / "codex-review-feat-test.done"
        marker_file.write_text("feat/test:oldcommit:olddiff")

        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test' && git push"},
            "tool_result": {"exit_code": 1},  # Push failed, but commit succeeded
        }

        # Use ExitStack to reduce nesting
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(hook_module, "parse_hook_input", return_value=hook_input)
            )
            stack.enter_context(
                patch.object(hook_module, "get_current_branch", return_value="feat/test")
            )
            stack.enter_context(
                patch.object(hook_module, "get_head_commit", return_value="newcommit")
            )
            stack.enter_context(
                patch.object(hook_module, "get_diff_hash", return_value="newdiffhash")
            )
            stack.enter_context(patch.object(hook_module, "MARKERS_LOG_DIR", markers_dir))
            stack.enter_context(
                patch.object(hook_module, "sanitize_branch_name", return_value="feat-test")
            )
            stack.enter_context(patch.object(hook_module, "log_hook_execution"))
            hook_module.main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}
        # Marker should be updated even with exit_code=1
        assert marker_file.read_text() == "feat/test:newcommit:newdiffhash"

    def test_handles_empty_input(self, hook_module, capsys):
        """空の入力を処理"""
        with patch.object(hook_module, "parse_hook_input", return_value=None):
            hook_module.main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}

    def test_updates_marker_with_empty_marker_file(self, hook_module, capsys, tmp_path):
        """空のマーカーファイルでもマーカーを更新 (Issue #983)

        マーカーファイルが空の場合、len(marker_parts) < 2 となり、
        HEAD比較をスキップしてマーカーを更新する。
        """
        markers_dir = tmp_path / "markers"
        markers_dir.mkdir()
        marker_file = markers_dir / "codex-review-feat-test.done"
        marker_file.write_text("")  # Empty file

        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_result": {"exit_code": 0},
        }

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(hook_module, "parse_hook_input", return_value=hook_input)
            )
            stack.enter_context(
                patch.object(hook_module, "get_current_branch", return_value="feat/test")
            )
            stack.enter_context(
                patch.object(hook_module, "get_head_commit", return_value="newcommit")
            )
            stack.enter_context(patch.object(hook_module, "get_diff_hash", return_value="newdiff"))
            stack.enter_context(patch.object(hook_module, "MARKERS_LOG_DIR", markers_dir))
            stack.enter_context(
                patch.object(hook_module, "sanitize_branch_name", return_value="feat-test")
            )
            stack.enter_context(patch.object(hook_module, "log_hook_execution"))
            hook_module.main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}
        assert marker_file.read_text() == "feat/test:newcommit:newdiff"

    def test_updates_marker_with_malformed_marker_file(self, hook_module, capsys, tmp_path):
        """不正形式のマーカーファイルでもマーカーを更新 (Issue #983)

        マーカーファイルが不正形式（コロン不足）の場合、len(marker_parts) < 2 となり、
        HEAD比較をスキップしてマーカーを正しい形式で上書きする。
        """
        markers_dir = tmp_path / "markers"
        markers_dir.mkdir()
        marker_file = markers_dir / "codex-review-feat-test.done"
        marker_file.write_text("malformed-no-colons")  # Malformed content

        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_result": {"exit_code": 0},
        }

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(hook_module, "parse_hook_input", return_value=hook_input)
            )
            stack.enter_context(
                patch.object(hook_module, "get_current_branch", return_value="feat/test")
            )
            stack.enter_context(
                patch.object(hook_module, "get_head_commit", return_value="newcommit")
            )
            stack.enter_context(patch.object(hook_module, "get_diff_hash", return_value="newdiff"))
            stack.enter_context(patch.object(hook_module, "MARKERS_LOG_DIR", markers_dir))
            stack.enter_context(
                patch.object(hook_module, "sanitize_branch_name", return_value="feat-test")
            )
            stack.enter_context(patch.object(hook_module, "log_hook_execution"))
            hook_module.main()

        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"continue": True}
        assert marker_file.read_text() == "feat/test:newcommit:newdiff"
