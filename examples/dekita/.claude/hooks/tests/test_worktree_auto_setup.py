"""worktree-auto-setup.py のテスト"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent))


def run_hook(stdin_data: dict, env_override: dict | None = None) -> tuple[int, str, str]:
    """フックを実行して結果を返す"""
    hook_path = Path(__file__).parent.parent / "worktree-auto-setup.py"
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


class TestWorktreeAutoSetup:
    """worktree自動セットアップフックのテスト"""

    def test_returns_continue_true_on_non_worktree_command(self):
        """git worktree add以外のコマンドでは何もしない"""
        stdin_data = {
            "tool_input": {"command": "echo test"},
            "tool_result": {"exit_code": 0},
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        output = json.loads(stdout)
        assert output.get("continue") is True
        assert "systemMessage" not in output

    def test_returns_continue_true_on_failed_command(self):
        """worktree作成失敗時は何もしない"""
        stdin_data = {
            "tool_input": {"command": "git worktree add .worktrees/test -b feat/test"},
            "tool_result": {"exit_code": 1},
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        output = json.loads(stdout)
        assert output.get("continue") is True
        assert "systemMessage" not in output

    def test_returns_continue_true_when_project_dir_not_set(self):
        """CLAUDE_PROJECT_DIRが未設定の場合も正常処理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a worktree-like directory
            worktree_path = Path(tmpdir) / ".worktrees" / "test"
            worktree_path.mkdir(parents=True)

            stdin_data = {
                "tool_input": {"command": f"git worktree add {worktree_path} -b feat/test"},
                "tool_result": {"exit_code": 0},
            }
            # Unset CLAUDE_PROJECT_DIR
            env_override = {"CLAUDE_PROJECT_DIR": ""}
            returncode, stdout, _ = run_hook(stdin_data, env_override)
            assert returncode == 0
            output = json.loads(stdout)
            assert output.get("continue") is True

    def test_handles_missing_tool_input(self):
        """tool_inputがない場合も正常に処理"""
        stdin_data = {"tool_result": {"exit_code": 0}}
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        output = json.loads(stdout)
        assert output.get("continue") is True

    def test_handles_missing_tool_result(self):
        """tool_resultがない場合も正常に処理（exit_codeデフォルト0で成功扱い）"""
        stdin_data = {"tool_input": {"command": "git worktree add .worktrees/test"}}
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        output = json.loads(stdout)
        assert output.get("continue") is True

    def test_handles_empty_stdin(self):
        """空の入力でも正常に処理"""
        hook_path = Path(__file__).parent.parent / "worktree-auto-setup.py"
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

    def test_handles_prefixed_commands(self):
        """シェルプレフィックス付きコマンドでも正常処理"""
        prefixed_commands = [
            "cd repo && git worktree add .worktrees/test -b feat/test",
            "ENV=1 && git worktree add .worktrees/test -b feat/test",
        ]
        for command in prefixed_commands:
            stdin_data = {
                "tool_input": {"command": command},
                "tool_result": {"exit_code": 0},
            }
            returncode, stdout, _ = run_hook(stdin_data)
            assert returncode == 0
            output = json.loads(stdout)
            assert output.get("continue") is True

    def test_handles_env_prefixed_commands(self):
        """環境変数プレフィックス付きコマンドでも正常処理"""
        env_prefixed_commands = [
            "SKIP_PLAN=1 git worktree add .worktrees/test -b feat/test",
            "ENV=value git worktree add .worktrees/test",
        ]
        for command in env_prefixed_commands:
            stdin_data = {
                "tool_input": {"command": command},
                "tool_result": {"exit_code": 0},
            }
            returncode, stdout, _ = run_hook(stdin_data)
            assert returncode == 0
            output = json.loads(stdout)
            assert output.get("continue") is True

    def test_skips_when_worktree_path_not_found(self):
        """worktreeパスが見つからない場合はスキップ"""
        stdin_data = {
            "tool_input": {"command": "git worktree add /nonexistent/path -b feat/test"},
            "tool_result": {"exit_code": 0},
        }
        returncode, stdout, _ = run_hook(stdin_data)
        assert returncode == 0
        output = json.loads(stdout)
        assert output.get("continue") is True
        # No systemMessage when path not found
        assert "systemMessage" not in output

    def test_detects_worktree_add_command_patterns(self):
        """様々なgit worktree addパターンを認識"""
        patterns = [
            "git worktree add .worktrees/test",
            "git worktree add -b branch .worktrees/test",
            "git worktree add --detach .worktrees/test",
            "git worktree add .worktrees/test main",
        ]
        for pattern in patterns:
            stdin_data = {
                "tool_input": {"command": pattern},
                "tool_result": {"exit_code": 0},
            }
            returncode, stdout, _ = run_hook(stdin_data)
            assert returncode == 0
            # Should at least return continue: True without error
            output = json.loads(stdout)
            assert output.get("continue") is True

    def test_does_not_block_on_error(self):
        """エラー時もブロックしない（continue: Trueを返す）"""
        # Even with invalid JSON structure, hook should handle gracefully
        hook_path = Path(__file__).parent.parent / "worktree-auto-setup.py"
        env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input="not valid json",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        # Should not crash with non-zero exit code
        # The hook catches exceptions and returns continue: True
        assert result.returncode == 0
