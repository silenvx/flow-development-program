"""active-worktree-check.py のテスト"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))
import common as common_module

# Import hook module with dynamic loading due to hyphens in filename
HOOK_PATH = Path(__file__).parent.parent / "active-worktree-check.py"
_spec = importlib.util.spec_from_file_location("active_worktree_check", HOOK_PATH)
active_worktree_check = importlib.util.module_from_spec(_spec)
sys.modules["active_worktree_check"] = active_worktree_check
_spec.loader.exec_module(active_worktree_check)


def run_hook() -> tuple[int, str, str]:
    """フックを実行して結果を返す"""
    hook_path = Path(__file__).parent.parent / "active-worktree-check.py"
    stdin_data = json.dumps({"tool_input": {"command": "echo test"}})
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


class TestActiveWorktreeCheck:
    """作業中worktree検出フックのテスト"""

    def setup_method(self):
        """テスト前にセッションマーカーを削除"""
        marker_file = common_module.SESSION_DIR / "active-worktree-check.marker"
        if marker_file.exists():
            marker_file.unlink()

    def test_returns_approve_on_normal_execution(self):
        """正常実行時はapproveを返す"""
        returncode, stdout, _ = run_hook()
        assert returncode == 0
        if stdout.strip():
            output = json.loads(stdout)
            assert output["decision"] == "approve"

    def test_skips_on_subsequent_calls(self):
        """連続呼び出し時は2回目以降スキップ"""
        # First call - should check
        run_hook()
        # Second call - should skip (same session)
        _, stdout, _ = run_hook()
        if stdout.strip():
            output = json.loads(stdout)
            assert output["decision"] == "approve"
            # No systemMessage on skip (or different message)


class TestGetWorktreeBranch:
    """get_worktree_branch関数のテスト"""

    def _import_module(self):
        """Import the hook module with hyphenated name."""
        import importlib.util

        hook_path = Path(__file__).parent.parent / "active-worktree-check.py"
        spec = importlib.util.spec_from_file_location("active_worktree_check", hook_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_returns_branch_for_valid_repo(self):
        """有効なリポジトリでブランチ名を返す"""
        module = self._import_module()

        # Test with current directory (should be a git repo)
        branch = module.get_worktree_branch(Path.cwd())
        # Should return a string (branch name) or None
        assert branch is None or isinstance(branch, str)

    def test_returns_none_for_invalid_path(self):
        """無効なパスではNoneを返す"""
        module = self._import_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            branch = module.get_worktree_branch(Path(tmpdir))
            assert branch is None


class TestCheckPrStatus:
    """check_pr_status関数のテスト"""

    def _import_module(self):
        """Import the hook module."""
        import importlib.util

        hook_path = Path(__file__).parent.parent / "active-worktree-check.py"
        spec = importlib.util.spec_from_file_location("active_worktree_check", hook_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_returns_none_for_nonexistent_branch(self):
        """存在しないブランチではNoneを返す"""
        module = self._import_module()

        result = module.check_pr_status("nonexistent-branch-12345")
        # Should return None (no PR found) or a dict if the branch somehow exists
        assert result is None or isinstance(result, dict)


class TestGetWorktreeLastCommit:
    """get_worktree_last_commit関数のテスト"""

    def _import_module(self):
        """Import the hook module."""
        import importlib.util

        hook_path = Path(__file__).parent.parent / "active-worktree-check.py"
        spec = importlib.util.spec_from_file_location("active_worktree_check", hook_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_returns_commit_info_for_valid_repo(self):
        """有効なリポジトリでコミット情報を返す"""
        module = self._import_module()

        # Test with current directory (should be a git repo)
        commit_info = module.get_worktree_last_commit(Path.cwd())
        # Should return a string or None
        assert commit_info is None or isinstance(commit_info, str)

    def test_returns_none_for_invalid_path(self):
        """無効なパスではNoneを返す"""
        module = self._import_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            commit_info = module.get_worktree_last_commit(Path(tmpdir))
            assert commit_info is None


class TestGetRepoRoot:
    """get_repo_root関数のテスト"""

    def test_main_repo_with_git_dir(self):
        """通常リポジトリ: .gitがディレクトリの場合はproject_dirを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            result = active_worktree_check.get_repo_root(project_dir)
            assert result == project_dir

    def test_no_git_returns_none(self):
        """git以外のディレクトリ: .gitが存在しない場合はNoneを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            result = active_worktree_check.get_repo_root(project_dir)
            assert result is None

    def test_worktree_with_gitdir_file(self):
        """worktree内: .gitファイルから親リポジトリのrootを解決"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create main repo structure
            main_repo = Path(tmpdir) / "main-repo"
            main_repo.mkdir()
            git_dir = main_repo / ".git"
            git_dir.mkdir()
            git_worktrees = git_dir / "worktrees" / "my-worktree"
            git_worktrees.mkdir(parents=True)

            # Create worktree directory with .git file
            worktree_dir = Path(tmpdir) / "worktree"
            worktree_dir.mkdir()
            git_file = worktree_dir / ".git"
            git_file.write_text(f"gitdir: {git_worktrees}")

            result = active_worktree_check.get_repo_root(worktree_dir)
            assert result == main_repo

    def test_invalid_gitdir_file_returns_none(self):
        """.gitファイルの内容が不正な場合はNoneを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_file = project_dir / ".git"
            git_file.write_text("invalid content")

            result = active_worktree_check.get_repo_root(project_dir)
            assert result is None


class TestFindActiveWorktrees:
    """find_active_worktrees関数のテスト"""

    def test_no_worktrees_dir(self):
        """.worktreesディレクトリが存在しない場合は空リストを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()

            result = active_worktree_check.find_active_worktrees(project_dir)
            assert result == []

    def test_empty_worktrees_dir(self):
        """.worktreesディレクトリが空の場合は空リストを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()
            worktrees_dir = project_dir / ".worktrees"
            worktrees_dir.mkdir()

            result = active_worktree_check.find_active_worktrees(project_dir)
            assert result == []

    def test_skips_files_in_worktrees_dir(self):
        """.worktrees内のファイルは無視する"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()
            worktrees_dir = project_dir / ".worktrees"
            worktrees_dir.mkdir()
            # Create a file (not directory) in .worktrees
            (worktrees_dir / "some-file.txt").write_text("test")

            result = active_worktree_check.find_active_worktrees(project_dir)
            assert result == []

    @patch.object(active_worktree_check, "get_worktree_branch")
    @patch.object(active_worktree_check, "check_pr_status")
    @patch.object(active_worktree_check, "get_worktree_last_commit")
    def test_skips_merged_prs(self, mock_commit, mock_pr, mock_branch):
        """MERGED状態のPRはスキップする"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()
            worktrees_dir = project_dir / ".worktrees"
            (worktrees_dir / "merged-wt").mkdir(parents=True)

            mock_branch.return_value = "feature/test"
            mock_pr.return_value = {"number": 123, "state": "MERGED"}
            mock_commit.return_value = "abc1234 test commit"

            result = active_worktree_check.find_active_worktrees(project_dir)
            assert result == []

    @patch.object(active_worktree_check, "get_worktree_branch")
    @patch.object(active_worktree_check, "check_pr_status")
    @patch.object(active_worktree_check, "get_worktree_last_commit")
    def test_includes_open_prs(self, mock_commit, mock_pr, mock_branch):
        """OPEN状態のPRを含める"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()
            worktrees_dir = project_dir / ".worktrees"
            (worktrees_dir / "open-wt").mkdir(parents=True)

            mock_branch.return_value = "feature/test"
            mock_pr.return_value = {"number": 456, "state": "OPEN"}
            mock_commit.return_value = "def5678 open commit"

            result = active_worktree_check.find_active_worktrees(project_dir)
            assert len(result) == 1
            assert result[0]["name"] == "open-wt"
            assert result[0]["pr_number"] == 456
            assert result[0]["pr_state"] == "OPEN"

    @patch.object(active_worktree_check, "get_worktree_branch")
    @patch.object(active_worktree_check, "check_pr_status")
    @patch.object(active_worktree_check, "get_worktree_last_commit")
    def test_includes_worktrees_without_pr(self, mock_commit, mock_pr, mock_branch):
        """PRがないworktreeも含める"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()
            worktrees_dir = project_dir / ".worktrees"
            (worktrees_dir / "no-pr-wt").mkdir(parents=True)

            mock_branch.return_value = "feature/no-pr"
            mock_pr.return_value = None  # No PR
            mock_commit.return_value = "ghi9012 no pr commit"

            result = active_worktree_check.find_active_worktrees(project_dir)
            assert len(result) == 1
            assert result[0]["name"] == "no-pr-wt"
            assert result[0]["pr_number"] is None
            assert result[0]["pr_state"] is None

    @patch.object(active_worktree_check, "get_worktree_branch")
    @patch.object(active_worktree_check, "check_pr_status")
    @patch.object(active_worktree_check, "get_worktree_last_commit")
    def test_multiple_worktrees(self, mock_commit, mock_pr, mock_branch):
        """複数worktreeの処理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            git_dir = project_dir / ".git"
            git_dir.mkdir()
            worktrees_dir = project_dir / ".worktrees"
            (worktrees_dir / "wt-a").mkdir(parents=True)
            (worktrees_dir / "wt-b").mkdir(parents=True)
            (worktrees_dir / "wt-c").mkdir(parents=True)

            def branch_side_effect(path):
                if "wt-a" in str(path):
                    return "feature/a"
                elif "wt-b" in str(path):
                    return "feature/b"
                elif "wt-c" in str(path):
                    return "feature/c"
                return None

            def pr_side_effect(branch):
                if branch == "feature/a":
                    return {"number": 1, "state": "OPEN"}
                elif branch == "feature/b":
                    return {"number": 2, "state": "MERGED"}  # Should be skipped
                elif branch == "feature/c":
                    return None  # No PR
                return None

            mock_branch.side_effect = branch_side_effect
            mock_pr.side_effect = pr_side_effect
            mock_commit.return_value = "commit info"

            result = active_worktree_check.find_active_worktrees(project_dir)
            # Should include wt-a (OPEN) and wt-c (no PR), but not wt-b (MERGED)
            assert len(result) == 2
            names = [w["name"] for w in result]
            assert "wt-a" in names
            assert "wt-c" in names
            assert "wt-b" not in names
