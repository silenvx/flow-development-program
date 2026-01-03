"""Tests for session-resume-warning.py hook."""

import importlib.util
import json
from pathlib import Path

# Load hook module with hyphenated filename
HOOKS_DIR = Path(__file__).parent.parent
HOOK_PATH = HOOKS_DIR / "session-resume-warning.py"
spec = importlib.util.spec_from_file_location("session_resume_warning", HOOK_PATH)
session_resume_warning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session_resume_warning)


class TestSessionResumeWarning:
    """Test cases for session resume warning hook."""

    def test_resume_session_shows_warning(self, monkeypatch):
        """source=resume の場合、警告メッセージが表示される"""
        hook_input = {
            "session_id": "test-session-123",
            "source": "resume",
            "hook_event_name": "SessionStart",
        }

        # Mock stdin with hook input
        monkeypatch.setattr(
            "sys.stdin", type("stdin", (), {"read": lambda self: json.dumps(hook_input)})()
        )

        # Capture stdout
        import io

        captured_output = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured_output)

        session_resume_warning.main()

        output = captured_output.getvalue()
        result = json.loads(output)

        assert result["continue"] is True
        assert "message" in result
        assert "セッション再開検出" in result["message"]
        assert "AGENTS.md原則" in result["message"]
        assert "既存Worktree" in result["message"]

    def test_startup_session_no_warning(self, monkeypatch):
        """source=startup の場合、警告メッセージは表示されない"""
        hook_input = {
            "session_id": "test-session-456",
            "source": "startup",
            "hook_event_name": "SessionStart",
        }

        monkeypatch.setattr(
            "sys.stdin", type("stdin", (), {"read": lambda self: json.dumps(hook_input)})()
        )

        import io

        captured_output = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured_output)

        session_resume_warning.main()

        output = captured_output.getvalue()
        result = json.loads(output)

        assert result["continue"] is True
        assert "message" not in result

    def test_compact_session_shows_warning(self, monkeypatch):
        """source=compact の場合も警告メッセージが表示される（Issue #2265）"""
        hook_input = {
            "session_id": "test-session-789",
            "source": "compact",
            "hook_event_name": "SessionStart",
        }

        monkeypatch.setattr(
            "sys.stdin", type("stdin", (), {"read": lambda self: json.dumps(hook_input)})()
        )

        import io

        captured_output = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured_output)

        session_resume_warning.main()

        output = captured_output.getvalue()
        result = json.loads(output)

        assert result["continue"] is True
        assert "message" in result
        assert "セッション再開検出" in result["message"]
        assert "AGENTS.md原則" in result["message"]
        assert "既存Worktree" in result["message"]

    def test_clear_session_no_warning(self, monkeypatch):
        """source=clear の場合、警告メッセージは表示されない"""
        hook_input = {
            "session_id": "test-session-abc",
            "source": "clear",
            "hook_event_name": "SessionStart",
        }

        monkeypatch.setattr(
            "sys.stdin", type("stdin", (), {"read": lambda self: json.dumps(hook_input)})()
        )

        import io

        captured_output = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured_output)

        session_resume_warning.main()

        output = captured_output.getvalue()
        result = json.loads(output)

        assert result["continue"] is True
        assert "message" not in result

    def test_empty_source_no_warning(self, monkeypatch):
        """source が空の場合、警告メッセージは表示されない"""
        hook_input = {
            "session_id": "test-session-def",
            "source": "",
            "hook_event_name": "SessionStart",
        }

        monkeypatch.setattr(
            "sys.stdin", type("stdin", (), {"read": lambda self: json.dumps(hook_input)})()
        )

        import io

        captured_output = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured_output)

        session_resume_warning.main()

        output = captured_output.getvalue()
        result = json.loads(output)

        assert result["continue"] is True
        assert "message" not in result

    def test_missing_source_no_warning(self, monkeypatch):
        """source がない場合、警告メッセージは表示されない"""
        hook_input = {
            "session_id": "test-session-ghi",
            "hook_event_name": "SessionStart",
        }

        monkeypatch.setattr(
            "sys.stdin", type("stdin", (), {"read": lambda self: json.dumps(hook_input)})()
        )

        import io

        captured_output = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured_output)

        session_resume_warning.main()

        output = captured_output.getvalue()
        result = json.loads(output)

        assert result["continue"] is True
        assert "message" not in result

    def test_error_handling(self, monkeypatch):
        """エラーが発生しても continue=True を返す"""
        # Invalid JSON
        monkeypatch.setattr("sys.stdin", type("stdin", (), {"read": lambda self: "invalid json"})())

        import io

        captured_output = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured_output)

        session_resume_warning.main()

        output = captured_output.getvalue()
        result = json.loads(output)

        assert result["continue"] is True


class TestGetWorktreeList:
    """Tests for get_worktree_list function."""

    def test_returns_empty_list_when_subprocess_fails(self, monkeypatch):
        """subprocessが失敗した場合、空リストを返す"""
        import subprocess

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")
            return result

        monkeypatch.setattr("subprocess.run", mock_run)
        assert session_resume_warning.get_worktree_list() == []

    def test_returns_empty_list_when_output_is_empty(self, monkeypatch):
        """出力が空の場合、空リストを返す"""
        import subprocess

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", mock_run)
        assert session_resume_warning.get_worktree_list() == []

    def test_excludes_main_worktree(self, monkeypatch):
        """メインworktreeは除外される"""
        import subprocess

        porcelain_output = """worktree /home/user/project
branch refs/heads/main

worktree /home/user/project/.worktrees/issue-123
branch refs/heads/feat/issue-123
"""

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=porcelain_output, stderr=""
            )

        monkeypatch.setattr("subprocess.run", mock_run)
        result = session_resume_warning.get_worktree_list()
        assert len(result) == 1
        assert "issue-123" in result[0]
        assert "main" not in "".join(result)

    def test_includes_detached_head_worktree(self, monkeypatch):
        """detachedヘッド状態のworktreeも含まれる"""
        import subprocess

        porcelain_output = """worktree /home/user/project
branch refs/heads/main

worktree /home/user/project/.worktrees/issue-456
HEAD abc1234567890
detached
"""

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=porcelain_output, stderr=""
            )

        monkeypatch.setattr("subprocess.run", mock_run)
        result = session_resume_warning.get_worktree_list()
        assert len(result) == 1
        assert "issue-456" in result[0]
        assert "HEAD detached" in result[0]

    def test_handles_multiple_worktrees(self, monkeypatch):
        """複数のworktreeを正しく処理する"""
        import subprocess

        porcelain_output = """worktree /home/user/project
branch refs/heads/main

worktree /home/user/project/.worktrees/issue-1
branch refs/heads/feat/issue-1

worktree /home/user/project/.worktrees/issue-2
branch refs/heads/fix/issue-2
"""

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=porcelain_output, stderr=""
            )

        monkeypatch.setattr("subprocess.run", mock_run)
        result = session_resume_warning.get_worktree_list()
        assert len(result) == 2


class TestGetOpenPrs:
    """Tests for get_open_prs function."""

    def test_returns_empty_list_when_no_prs(self, monkeypatch):
        """PRがない場合、空リストを返す"""
        import subprocess

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", mock_run)
        assert session_resume_warning.get_open_prs() == []

    def test_returns_empty_list_when_gh_fails(self, monkeypatch):
        """ghコマンドが失敗した場合、空リストを返す"""
        import subprocess

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="error")

        monkeypatch.setattr("subprocess.run", mock_run)
        assert session_resume_warning.get_open_prs() == []

    def test_parses_pr_list_correctly(self, monkeypatch):
        """PR一覧を正しくパースする"""
        import subprocess

        pr_output = """  - #123 feat/issue-123: Add new feature
  - #456 fix/issue-456: Fix bug"""

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=pr_output, stderr="")

        monkeypatch.setattr("subprocess.run", mock_run)
        result = session_resume_warning.get_open_prs()
        assert len(result) == 2
        assert "#123" in result[0]
        assert "#456" in result[1]


class TestFormatResumeSessionMessage:
    """Tests for format_resume_session_message function."""

    def test_includes_worktrees_and_prs(self):
        """worktreeとPRの両方が含まれる"""
        worktrees = ["  - issue-1 (feat/issue-1)"]
        prs = ["  - #100 feat/issue-1: Feature"]
        result = session_resume_warning.format_resume_session_message(worktrees, prs)
        assert "issue-1" in result
        assert "#100" in result
        assert "セッション再開検出" in result

    def test_handles_empty_worktrees_and_prs(self):
        """worktreeとPRが空の場合"""
        result = session_resume_warning.format_resume_session_message([], [])
        assert "**既存Worktree**: なし" in result
        assert "**オープンPR**: なし" in result

    def test_handles_only_worktrees(self):
        """worktreeのみある場合"""
        worktrees = ["  - issue-1 (feat/issue-1)"]
        result = session_resume_warning.format_resume_session_message(worktrees, [])
        assert "issue-1" in result
        assert "**オープンPR**: なし" in result

    def test_handles_only_prs(self):
        """PRのみある場合"""
        prs = ["  - #100 feat/issue-1: Feature"]
        result = session_resume_warning.format_resume_session_message([], prs)
        assert "**既存Worktree**: なし" in result
        assert "#100" in result
