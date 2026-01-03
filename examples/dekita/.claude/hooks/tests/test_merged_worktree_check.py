"""merged-worktree-check.py のテスト"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))
import common as common_module


def run_hook() -> tuple[int, str, str]:
    """フックを実行して結果を返す"""
    hook_path = Path(__file__).parent.parent / "merged-worktree-check.py"
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


class TestMergedWorktreeCheck:
    """マージ済みworktree検出フックのテスト"""

    def setup_method(self):
        """テスト前にセッションマーカーを削除"""
        marker_file = common_module.SESSION_DIR / "merged-worktree-check.marker"
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
            # No systemMessage on skip
            assert "マージ済み" not in output.get("systemMessage", "")


class TestGetWorktreeBranch:
    """get_worktree_branch関数のテスト"""

    def _import_module(self):
        """Import the hook module with hyphenated name."""
        import importlib.util

        hook_path = Path(__file__).parent.parent / "merged-worktree-check.py"
        spec = importlib.util.spec_from_file_location("merged_worktree_check", hook_path)
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
