"""pr-merge-pull-reminder.py のテスト"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))

from importlib import import_module

pr_merge_pull_reminder = import_module("pr-merge-pull-reminder")

# テスト用の共通git環境変数
GIT_TEST_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


def create_test_repo(tmpdir: Path | str) -> Path:
    """テスト用のgitリポジトリを初期化する."""
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
    """テスト用のworktreeを作成する."""
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
    hook_path = Path(__file__).parent.parent / "pr-merge-pull-reminder.py"
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


class TestIsPrMergeCommand:
    """is_pr_merge_command() のテスト"""

    def test_detects_simple_merge_command(self):
        """シンプルなgh pr mergeコマンドを検知"""
        assert pr_merge_pull_reminder.is_pr_merge_command("gh pr merge 123")

    def test_detects_merge_with_flags(self):
        """フラグ付きのマージコマンドを検知"""
        assert pr_merge_pull_reminder.is_pr_merge_command("gh pr merge 123 --squash")
        assert pr_merge_pull_reminder.is_pr_merge_command(
            "gh pr merge --squash --delete-branch 456"
        )

    def test_detects_prefixed_command(self):
        """cd等のプレフィックス付きコマンドを検知"""
        assert pr_merge_pull_reminder.is_pr_merge_command("cd /path && gh pr merge 123")

    def test_returns_false_for_non_merge(self):
        """マージ以外のコマンドはFalse"""
        assert not pr_merge_pull_reminder.is_pr_merge_command("gh pr list")
        assert not pr_merge_pull_reminder.is_pr_merge_command("gh pr view 123")
        assert not pr_merge_pull_reminder.is_pr_merge_command("echo test")


class TestIsMergeSuccess:
    """is_merge_success() のテスト (common.py version)

    Note: is_merge_success was moved to common.py with signature:
    is_merge_success(exit_code, stdout, command, *, stderr="")
    """

    def test_detects_merged_pull_request(self):
        """'Merged pull request'の検知"""
        from lib.repo import is_merge_success

        assert is_merge_success(0, "Merged pull request #123", "gh pr merge 123")

    def test_detects_pull_request_merged(self):
        """'Pull request ... merged'の検知"""
        from lib.repo import is_merge_success

        assert is_merge_success(0, "Pull request #456 merged", "gh pr merge 456")

    def test_detects_already_merged(self):
        """'was already merged'の検知"""
        from lib.repo import is_merge_success

        assert is_merge_success(0, "! Pull request #789 was already merged", "gh pr merge 789")

    def test_returns_true_on_non_zero_exit_with_success_pattern(self):
        """exit_codeが0以外でも成功パターンがあればTrue (worktree --delete-branch edge case)"""
        from lib.repo import is_merge_success

        # This handles the worktree --delete-branch edge case where merge succeeds
        # but branch deletion fails
        assert is_merge_success(1, "Merged pull request #123", "gh pr merge 123")

    def test_returns_false_on_no_success_pattern(self):
        """成功パターンがなければFalse"""
        from lib.repo import is_merge_success

        assert not is_merge_success(0, "Error: something went wrong", "gh pr merge 123")


class TestIsInWorktree:
    """is_in_worktree() のテスト"""

    def test_detects_worktree_path(self):
        """worktreeパスの検知"""
        with patch("os.getcwd", return_value="/path/to/repo/.worktrees/issue-123"):
            assert pr_merge_pull_reminder.is_in_worktree()

    def test_detects_worktree_subdir(self):
        """worktree内のサブディレクトリの検知"""
        with patch("os.getcwd", return_value="/path/to/repo/.worktrees/issue-123/src/components"):
            assert pr_merge_pull_reminder.is_in_worktree()

    def test_detects_main_repo(self):
        """メインリポジトリはFalse"""
        with patch("os.getcwd", return_value="/path/to/repo"):
            assert not pr_merge_pull_reminder.is_in_worktree()

    def test_detects_main_repo_subdir(self):
        """メインリポジトリのサブディレクトリはFalse"""
        with patch("os.getcwd", return_value="/path/to/repo/src"):
            assert not pr_merge_pull_reminder.is_in_worktree()


class TestGetRepoRoot:
    """get_repo_root() のテスト (common.py version)

    Note: get_repo_root was moved to common.py. Tests use common module directly.
    """

    def test_returns_project_dir_fallback_without_env(self):
        """CLAUDE_PROJECT_DIRが未設定の場合、_PROJECT_DIR（現在のディレクトリ）をフォールバックとして使用"""
        from lib.repo import get_repo_root

        # Without CLAUDE_PROJECT_DIR, get_repo_root uses _PROJECT_DIR (cwd at import time)
        # which is typically the git repo root in test environment
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
                assert result.resolve() == repo_path.resolve()

    def test_returns_none_for_non_git_dir(self):
        """gitリポジトリでない場合はNoneを返す"""
        from lib.repo import get_repo_root

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": tmpdir}):
                result = get_repo_root()
                assert result is None


class TestGetCurrentBranch:
    """_get_current_branch() のテスト

    Note: Function was renamed to _get_current_branch (private).
    Tests access it via the module for coverage purposes.
    """

    def test_returns_branch_name(self):
        """現在のブランチ名を返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            # デフォルトブランチ名を取得（git initの設定による）
            result = pr_merge_pull_reminder._get_current_branch(repo_path)
            assert result is not None
            # master または main のどちらか
            assert result in ["master", "main"]

    def test_returns_feature_branch_name(self):
        """フィーチャーブランチ名を返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            # フィーチャーブランチを作成
            subprocess.run(
                ["git", "checkout", "-b", "feature/test"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )
            result = pr_merge_pull_reminder._get_current_branch(repo_path)
            assert result == "feature/test"

    def test_returns_none_on_timeout(self):
        """タイムアウト時はNoneを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
            ):
                result = pr_merge_pull_reminder._get_current_branch(repo_path)
                assert result is None

    def test_returns_none_on_missing_git(self):
        """gitコマンドが見つからない場合はNoneを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            with patch("subprocess.run", side_effect=FileNotFoundError()):
                result = pr_merge_pull_reminder._get_current_branch(repo_path)
                assert result is None


class TestPullMain:
    """pull_main() のユニットテスト"""

    def test_returns_success_on_pull(self):
        """正常なpull時はTrueを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            mock_result = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="Already up to date.\n", stderr=""
            )
            with patch("subprocess.run", return_value=mock_result):
                success, output = pr_merge_pull_reminder.pull_main(repo_path)
                assert success
                assert "Already up to date" in output

    def test_returns_failure_on_error(self):
        """エラー時はFalseを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            mock_result = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="error: could not fetch"
            )
            with patch("subprocess.run", return_value=mock_result):
                success, output = pr_merge_pull_reminder.pull_main(repo_path)
                assert not success
                assert "could not fetch" in output

    def test_returns_failure_on_timeout(self):
        """タイムアウト時はFalseを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
            ):
                success, output = pr_merge_pull_reminder.pull_main(repo_path)
                assert not success
                assert "Timeout" in output

    def test_returns_failure_on_missing_git(self):
        """gitコマンドが見つからない場合はFalseを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            with patch("subprocess.run", side_effect=FileNotFoundError()):
                success, output = pr_merge_pull_reminder.pull_main(repo_path)
                assert not success
                assert "git command not found" in output


class TestMainHook:
    """main() フック全体のテスト"""

    def test_ignores_non_bash_tool(self):
        """Bash以外のツールは無視"""
        stdin_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/path/to/file"},
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        assert stdout.strip() == ""

    def test_ignores_non_merge_command(self):
        """gh pr merge以外のコマンドは無視"""
        stdin_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr list"},
            "tool_output": "PR #123",
            "exit_code": 0,
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        assert stdout.strip() == ""

    def test_ignores_failed_merge(self):
        """マージ失敗時は無視"""
        stdin_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_output": "Error: merge failed",
            "exit_code": 1,
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        assert stdout.strip() == ""

    def test_handles_invalid_json(self):
        """無効なJSONでも正常終了"""
        hook_path = Path(__file__).parent.parent / "pr-merge-pull-reminder.py"
        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input="invalid json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0

    def test_handles_empty_input(self):
        """空の入力でも正常終了"""
        hook_path = Path(__file__).parent.parent / "pr-merge-pull-reminder.py"
        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input="{}",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0


class TestIntegration:
    """統合テスト: 実際のgitリポジトリを使用"""

    def test_worktree_auto_pulls_main_repo(self):
        """worktree内からメインリポジトリのmainを自動pull"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            # メインリポジトリをmainブランチに変更
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )
            worktree_path = create_test_worktree(repo_path, "test-wt", "feature/test")

            hook_path = Path(__file__).parent.parent / "pr-merge-pull-reminder.py"
            stdin_data = {
                "tool_name": "Bash",
                "tool_input": {"command": "gh pr merge 123 --squash"},
                "tool_output": "Merged pull request #123",
                "exit_code": 0,
            }
            env = os.environ.copy()
            env["CLAUDE_PROJECT_DIR"] = str(worktree_path)

            # cwdをworktreeに設定して実行
            result = subprocess.run(
                [sys.executable, str(hook_path)],
                input=json.dumps(stdin_data),
                capture_output=True,
                text=True,
                env=env,
                cwd=str(worktree_path),
                check=False,
            )
            assert result.returncode == 0
            assert "pr-merge-pull-reminder" in result.stdout
            # リモートがないのでpullは失敗するが、試行したことを確認
            assert "pull" in result.stdout.lower()

    def test_worktree_skips_when_main_repo_not_on_main(self):
        """worktree内でメインリポジトリがmain以外ならスキップ"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            # メインリポジトリはmasterブランチのまま（mainではない）
            # git initのデフォルトがmasterの場合を想定
            subprocess.run(
                ["git", "branch", "-M", "master"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )
            worktree_path = create_test_worktree(repo_path, "test-wt", "feature/test")

            hook_path = Path(__file__).parent.parent / "pr-merge-pull-reminder.py"
            stdin_data = {
                "tool_name": "Bash",
                "tool_input": {"command": "gh pr merge 123 --squash"},
                "tool_output": "Merged pull request #123",
                "exit_code": 0,
            }
            env = os.environ.copy()
            env["CLAUDE_PROJECT_DIR"] = str(worktree_path)

            result = subprocess.run(
                [sys.executable, str(hook_path)],
                input=json.dumps(stdin_data),
                capture_output=True,
                text=True,
                env=env,
                cwd=str(worktree_path),
                check=False,
            )
            assert result.returncode == 0
            assert "pr-merge-pull-reminder" in result.stdout
            # masterブランチなので自動pullをスキップ
            assert "自動pullをスキップ" in result.stdout

    def test_main_repo_on_feature_branch_shows_reminder(self):
        """メインリポジトリでフィーチャーブランチにいる場合はリマインダーを表示"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            # フィーチャーブランチを作成
            subprocess.run(
                ["git", "checkout", "-b", "feature/test"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

            hook_path = Path(__file__).parent.parent / "pr-merge-pull-reminder.py"
            stdin_data = {
                "tool_name": "Bash",
                "tool_input": {"command": "gh pr merge 123 --squash"},
                "tool_output": "Merged pull request #123",
                "exit_code": 0,
            }
            env = os.environ.copy()
            env["CLAUDE_PROJECT_DIR"] = str(repo_path)

            result = subprocess.run(
                [sys.executable, str(hook_path)],
                input=json.dumps(stdin_data),
                capture_output=True,
                text=True,
                env=env,
                cwd=str(repo_path),
                check=False,
            )
            assert result.returncode == 0
            assert "pr-merge-pull-reminder" in result.stdout
            assert "mainブランチに切り替えてpull" in result.stdout

    def test_main_repo_on_main_branch_auto_pulls(self):
        """メインリポジトリでmainブランチにいる場合は自動pull（リモートがなくても動作確認）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = create_test_repo(tmpdir)
            # mainブランチに名前を変更（古いgitではmasterがデフォルト）
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

            hook_path = Path(__file__).parent.parent / "pr-merge-pull-reminder.py"
            stdin_data = {
                "tool_name": "Bash",
                "tool_input": {"command": "gh pr merge 123 --squash"},
                "tool_output": "Merged pull request #123",
                "exit_code": 0,
            }
            env = os.environ.copy()
            env["CLAUDE_PROJECT_DIR"] = str(repo_path)

            result = subprocess.run(
                [sys.executable, str(hook_path)],
                input=json.dumps(stdin_data),
                capture_output=True,
                text=True,
                env=env,
                cwd=str(repo_path),
                check=False,
            )
            assert result.returncode == 0
            # リモートがないので失敗するが、試行したことを確認
            assert "pr-merge-pull-reminder" in result.stdout
            # 自動pullを試行（リモートがないので失敗メッセージ）
            assert "pull" in result.stdout.lower()
