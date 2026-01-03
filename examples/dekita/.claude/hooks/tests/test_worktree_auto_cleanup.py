"""worktree-auto-cleanup.py のテスト"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import functions for integration testing
from importlib import import_module

worktree_auto_cleanup = import_module("worktree-auto-cleanup")

# テスト用の共通git環境変数
GIT_TEST_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


def create_test_repo(tmpdir: Path | str) -> Path:
    """テスト用のgitリポジトリを初期化する.

    Args:
        tmpdir: 一時ディレクトリのパス

    Returns:
        作成されたリポジトリのパス
    """
    repo_path = Path(tmpdir) / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=repo_path,
        capture_output=True,
        check=True,
        env={**os.environ, **GIT_TEST_ENV},
    )
    return repo_path


def create_test_worktree(repo_path: Path, name: str, branch: str) -> Path:
    """テスト用のworktreeを作成する.

    Args:
        repo_path: gitリポジトリのパス
        name: worktreeの名前（.worktrees配下のディレクトリ名）
        branch: ブランチ名

    Returns:
        作成されたworktreeのパス
    """
    worktrees_dir = repo_path / ".worktrees"
    worktrees_dir.mkdir(exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", f".worktrees/{name}", "-b", branch],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    return worktrees_dir / name


def run_hook(stdin_data: dict) -> tuple[int, str, str]:
    """フックを実行して結果を返す"""
    hook_path = Path(__file__).parent.parent / "worktree-auto-cleanup.py"
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


class TestWorktreeAutoCleanup:
    """worktree自動削除フックのテスト"""

    def test_returns_continue_true_on_non_merge_command(self):
        """gh pr merge以外のコマンドでは何もしない"""
        stdin_data = {
            "tool_input": {"command": "echo test"},
            "tool_result": {"exit_code": 0},
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        output = json.loads(stdout)
        assert output.get("continue", True)

    def test_extracts_pr_from_prefixed_commands(self):
        """シェルプレフィックス付きコマンドからもPR番号を抽出できる"""
        import re

        # These should match the PR number
        prefixed_commands = [
            ("cd repo && gh pr merge 123", "123"),
            ("ENV=1 gh pr merge 456", "456"),
            ("time gh pr merge 789 --squash", "789"),
        ]
        pattern = r"gh pr merge\s+(?:--?\S+\s+)*#?(\d+)"
        for command, expected in prefixed_commands:
            match = re.search(pattern, command)
            assert match is not None, f"Pattern should match: {command}"
            assert match.group(1) == expected

    def test_returns_continue_true_on_failed_merge(self):
        """マージ失敗時は何もしない"""
        stdin_data = {
            "tool_input": {"command": "gh pr merge 123 --squash"},
            "tool_result": {"exit_code": 1},
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        output = json.loads(stdout)
        assert output.get("continue", True)
        # No systemMessage when merge fails
        assert "systemMessage" not in output

    def test_extracts_pr_number_from_command(self):
        """PRの番号を正しく抽出できる"""
        import re

        # Test pattern matching
        test_cases = [
            ("gh pr merge 123", "123"),
            ("gh pr merge #456", "456"),
            ("gh pr merge 789 --squash", "789"),
            ("gh pr merge 100 --squash --delete-branch", "100"),
            # Flags before PR number
            ("gh pr merge --squash 123", "123"),
            ("gh pr merge --squash --delete-branch 456", "456"),
            ("gh pr merge -s 789", "789"),
            ("gh pr merge --auto 100", "100"),
        ]
        pattern = r"gh pr merge\s+(?:--?\S+\s+)*#?(\d+)"
        for command, expected in test_cases:
            match = re.search(pattern, command)
            assert match is not None, f"Pattern should match: {command}"
            assert match.group(1) == expected

    def test_handles_missing_tool_input(self):
        """tool_inputがない場合も正常に処理"""
        stdin_data = {"tool_result": {"exit_code": 0}}
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        output = json.loads(stdout)
        assert output.get("continue", True)

    def test_handles_missing_tool_result(self):
        """tool_resultがない場合も正常に処理"""
        stdin_data = {"tool_input": {"command": "gh pr merge 123"}}
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        output = json.loads(stdout)
        assert output.get("continue", True)

    def test_handles_empty_stdin(self):
        """空の入力でも正常に処理"""
        hook_path = Path(__file__).parent.parent / "worktree-auto-cleanup.py"
        env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input="{}",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0


class TestGetRepoRoot:
    """get_repo_root() の統合テスト (common.py version)

    Note: get_repo_root was moved to common.py. Tests use common module directly.
    """

    def test_returns_project_dir_fallback_without_env(self):
        """CLAUDE_PROJECT_DIRが未設定の場合、_PROJECT_DIR（現在のディレクトリ）をフォールバックとして使用"""
        from lib.repo import get_repo_root

        # Without CLAUDE_PROJECT_DIR, get_repo_root uses _PROJECT_DIR (cwd at import time)
        with patch.dict(os.environ, {}, clear=True):
            # Just verify it doesn't raise - result depends on cwd at import time
            get_repo_root()

    def test_returns_repo_root_for_normal_git_dir(self):
        """通常のgitリポジトリでリポジトリルートを返す"""
        from lib.repo import get_repo_root

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(repo_path)}):
                result = get_repo_root()
                assert result == repo_path

    def test_returns_repo_root_for_worktree(self):
        """worktree内から実行した場合もメインリポジトリのルートを返す"""
        from lib.repo import get_repo_root

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            worktree_path = create_test_worktree(repo_path, "test-wt", "feature/test")
            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(worktree_path)}):
                result = get_repo_root()
                # macOSではシンボリックリンクの解決結果が異なる場合があるため正規化
                assert result.resolve() == repo_path.resolve()

    def test_returns_none_for_non_git_dir(self):
        """gitリポジトリでない場合はNoneを返す"""
        from lib.repo import get_repo_root

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": tmpdir}):
                result = get_repo_root()
                assert result is None


class TestGetPrBranchUnit:
    """get_pr_branch() のユニットテスト（モック使用）"""

    def test_returns_branch_name_on_success(self):
        """成功時はブランチ名を返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            # Mock gh command to return branch name
            mock_result = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="feature/test-branch\n", stderr=""
            )
            with patch("subprocess.run", return_value=mock_result):
                result = worktree_auto_cleanup.get_pr_branch(123, repo_path)
                assert result == "feature/test-branch"

    def test_returns_none_on_failure(self):
        """失敗時はNoneを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            mock_result = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="error"
            )
            with patch("subprocess.run", return_value=mock_result):
                result = worktree_auto_cleanup.get_pr_branch(999, repo_path)
                assert result is None

    def test_returns_none_on_timeout(self):
        """タイムアウト時はNoneを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            with patch(
                "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=10)
            ):
                result = worktree_auto_cleanup.get_pr_branch(123, repo_path)
                assert result is None


class TestFindWorktreeByBranch:
    """find_worktree_by_branch() の統合テスト"""

    def test_returns_none_when_no_worktrees_dir(self):
        """worktreesディレクトリがない場合はNoneを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            result = worktree_auto_cleanup.find_worktree_by_branch(repo_path, "main")
            assert result is None

    def test_finds_worktree_by_branch(self):
        """ブランチ名でworktreeを見つける"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            create_test_worktree(repo_path, "test-wt", "feature/test")

            result = worktree_auto_cleanup.find_worktree_by_branch(repo_path, "feature/test")
            assert result is not None
            assert result.name == "test-wt"

    def test_returns_none_for_nonexistent_branch(self):
        """存在しないブランチではNoneを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            create_test_worktree(repo_path, "test-wt", "feature/test")

            result = worktree_auto_cleanup.find_worktree_by_branch(repo_path, "nonexistent-branch")
            assert result is None


class TestRemoveWorktree:
    """remove_worktree() の統合テスト"""

    def test_removes_worktree_successfully(self):
        """worktreeを正常に削除できる"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            worktree_path = create_test_worktree(repo_path, "to-remove", "feature/remove")
            assert worktree_path.exists()

            success, message = worktree_auto_cleanup.remove_worktree(repo_path, worktree_path)
            assert success
            assert "削除しました" in message
            assert not worktree_path.exists()

    def test_removes_locked_worktree(self):
        """ロックされたworktreeも削除できる"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            worktree_path = create_test_worktree(repo_path, "locked-wt", "feature/locked")
            # Lock the worktree
            subprocess.run(
                ["git", "worktree", "lock", ".worktrees/locked-wt"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

            success, message = worktree_auto_cleanup.remove_worktree(repo_path, worktree_path)
            assert success
            assert "削除しました" in message

    def test_handles_timeout(self):
        """タイムアウト時は失敗を返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            worktree_path = repo_path / ".worktrees" / "test"
            with patch(
                "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5)
            ):
                success, message = worktree_auto_cleanup.remove_worktree(repo_path, worktree_path)
                assert not success
                assert "タイムアウト" in message


class TestExtractPrNumberFromOutput:
    """extract_pr_number_from_output() のテスト"""

    def test_extracts_merged_pr_number(self):
        """'Merged pull request #123' から PR番号を抽出できる"""
        stdout = "✓ Merged pull request #789 (Title of PR)\n"
        result = worktree_auto_cleanup.extract_pr_number_from_output(stdout)
        assert result == 789

    def test_extracts_squashed_pr_number(self):
        """'Squashed and merged pull request #123' から PR番号を抽出できる"""
        stdout = "✓ Squashed and merged pull request #456 (Fix bug)\n"
        result = worktree_auto_cleanup.extract_pr_number_from_output(stdout)
        assert result == 456

    def test_extracts_rebased_pr_number(self):
        """'Rebased and merged pull request #123' から PR番号を抽出できる"""
        stdout = "✓ Rebased and merged pull request #123 (Feature)\n"
        result = worktree_auto_cleanup.extract_pr_number_from_output(stdout)
        assert result == 123

    def test_returns_none_for_no_match(self):
        """マッチしない場合は None を返す"""
        stdout = "Some other output without PR info\n"
        result = worktree_auto_cleanup.extract_pr_number_from_output(stdout)
        assert result is None

    def test_returns_none_for_empty_string(self):
        """空文字列の場合は None を返す"""
        result = worktree_auto_cleanup.extract_pr_number_from_output("")
        assert result is None

    def test_handles_lowercase_merged(self):
        """小文字の 'merged' も対応できる"""
        stdout = "merged pull request #100\n"
        result = worktree_auto_cleanup.extract_pr_number_from_output(stdout)
        assert result == 100


class TestCwdInsideWorktreeCheck:
    """Issue #803: cwdがworktree内の場合の削除スキップテスト"""

    def test_skips_deletion_when_cwd_inside_worktree(self):
        """cwdがworktree内の場合、削除をスキップして警告メッセージを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            worktree_path = create_test_worktree(repo_path, "test-wt", "feature/test")

            # Test using direct function call with mocking
            # (run_hook uses subprocess which doesn't propagate mocks)
            with patch.object(worktree_auto_cleanup, "check_cwd_inside_path", return_value=True):
                with patch.object(
                    worktree_auto_cleanup, "find_worktree_by_branch", return_value=worktree_path
                ):
                    with patch.object(
                        worktree_auto_cleanup, "get_pr_branch", return_value="feature/test"
                    ):
                        with patch.object(
                            worktree_auto_cleanup, "get_repo_root", return_value=repo_path
                        ):
                            with patch.object(
                                worktree_auto_cleanup,
                                "parse_hook_input",
                                return_value={
                                    "tool_input": {"command": "gh pr merge 123 --squash"},
                                    "tool_result": {
                                        "exit_code": 0,
                                        "stdout": "✓ Merged pull request #123",
                                    },
                                },
                            ):
                                with patch("builtins.print") as mock_print:
                                    worktree_auto_cleanup.main()

                                # Check that systemMessage contains the skip warning
                                call_args = mock_print.call_args[0][0]
                                output = json.loads(call_args)
                                assert "systemMessage" in output
                                assert "スキップ" in output["systemMessage"]
                                assert "test-wt" in output["systemMessage"]

            # worktree should still exist (not deleted)
            assert worktree_path.exists()

    def test_proceeds_with_deletion_when_cwd_outside_worktree(self):
        """cwdがworktree外の場合、通常通り削除を実行する"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            worktree_path = create_test_worktree(repo_path, "test-wt", "feature/test")

            # Verify worktree exists before test
            assert worktree_path.exists()

            # Test using direct function call with mocking
            with patch.object(worktree_auto_cleanup, "check_cwd_inside_path", return_value=False):
                with patch.object(worktree_auto_cleanup, "get_repo_root", return_value=repo_path):
                    with patch.object(
                        worktree_auto_cleanup, "find_worktree_by_branch", return_value=worktree_path
                    ):
                        with patch.object(
                            worktree_auto_cleanup, "get_pr_branch", return_value="feature/test"
                        ):
                            with patch.object(
                                worktree_auto_cleanup,
                                "parse_hook_input",
                                return_value={
                                    "tool_input": {"command": "gh pr merge 123 --squash"},
                                    "tool_result": {
                                        "exit_code": 0,
                                        "stdout": "✓ Merged pull request #123",
                                    },
                                },
                            ):
                                with patch("builtins.print") as mock_print:
                                    worktree_auto_cleanup.main()

                                # Check that systemMessage indicates successful deletion
                                call_args = mock_print.call_args[0][0]
                                output = json.loads(call_args)
                                assert "systemMessage" in output
                                assert "削除しました" in output["systemMessage"]

            # worktree should be deleted
            assert not worktree_path.exists()
