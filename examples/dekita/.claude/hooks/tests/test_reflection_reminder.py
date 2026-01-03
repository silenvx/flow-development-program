#!/usr/bin/env python3
"""
reflection-reminder.py のテスト
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# hooks ディレクトリをパスに追加
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))


# ハイフン付きファイル名のモジュールをロード
def load_module(name: str, filename: str):
    """Load a Python module from a hyphenated filename."""
    spec = importlib.util.spec_from_file_location(
        name,
        HOOKS_DIR / filename,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


reflection_reminder = load_module("reflection_reminder", "reflection-reminder.py")


class TestIsPrMergeCommand:
    """is_pr_merge_commandのテスト"""

    def test_gh_pr_merge_detected(self):
        """gh pr mergeコマンドを検出"""
        assert reflection_reminder.is_pr_merge_command("gh pr merge 123")
        assert reflection_reminder.is_pr_merge_command("gh pr merge --squash")
        assert reflection_reminder.is_pr_merge_command("gh  pr  merge")

    def test_git_merge_feature_branch_detected(self):
        """git merge feat/xxxを検出"""
        assert reflection_reminder.is_pr_merge_command("git merge feat/new-feature")
        assert reflection_reminder.is_pr_merge_command("git merge fix/bug-fix")
        assert reflection_reminder.is_pr_merge_command("git merge docs/update-readme")

    def test_non_merge_commands_not_detected(self):
        """非マージコマンドは検出しない"""
        assert not reflection_reminder.is_pr_merge_command("git status")
        assert not reflection_reminder.is_pr_merge_command("gh pr list")
        assert not reflection_reminder.is_pr_merge_command("git merge main")


class TestCheckPrMergeResult:
    """check_pr_merge_resultのテスト"""

    def test_successful_gh_merge_detected(self):
        """gh pr mergeの成功を検出"""
        result = {"exit_code": 0, "stdout": "Merged pull request #123"}
        assert reflection_reminder.check_pr_merge_result(result)

        result = {"exit_code": 0, "stdout": "Pull request merged successfully"}
        assert reflection_reminder.check_pr_merge_result(result)

    def test_successful_git_merge_detected(self):
        """git mergeの成功を検出"""
        # ort strategy
        result = {"exit_code": 0, "stdout": "Merge made by the 'ort' strategy."}
        assert reflection_reminder.check_pr_merge_result(result)

        # Fast-forward
        result = {"exit_code": 0, "stdout": "Fast-forward\n README.md | 1 +"}
        assert reflection_reminder.check_pr_merge_result(result)

    def test_failed_merge_not_detected(self):
        """マージ失敗は検出しない"""
        result = {"exit_code": 1, "stdout": "Error: merge conflict"}
        assert not reflection_reminder.check_pr_merge_result(result)

        result = {"exit_code": 0, "stdout": "No changes"}
        assert not reflection_reminder.check_pr_merge_result(result)


class TestIncrementActionCount:
    """increment_action_countのテスト"""

    def test_increment_from_zero(self):
        """0からインクリメント"""
        state = {"action_count": 0}
        count = reflection_reminder.increment_action_count(state)
        assert count == 1
        assert state["action_count"] == 1

    def test_increment_existing(self):
        """既存の値をインクリメント"""
        state = {"action_count": 5}
        count = reflection_reminder.increment_action_count(state)
        assert count == 6
        assert state["action_count"] == 6

    def test_increment_missing_key(self):
        """キーがない場合は0から開始"""
        state = {}
        count = reflection_reminder.increment_action_count(state)
        assert count == 1
        assert state["action_count"] == 1


class TestReflectionReminderHook:
    """振り返りリマインダーフックのテスト"""

    def test_non_bash_tool_skipped(self):
        """Bash以外のツールはスキップ"""
        hook_input = {
            "tool_name": "Edit",
            "tool_input": {},
            "tool_result": {},
        }

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(hook_input)

            with patch.object(reflection_reminder, "log_hook_execution"):
                with patch("builtins.print") as mock_print:
                    reflection_reminder.main()
                    output = json.loads(mock_print.call_args[0][0])
                    assert output.get("continue", True)
                    assert "systemMessage" not in output

    def test_pr_merge_shows_reminder(self):
        """PRマージ時にリマインダーを表示"""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 123"},
            "tool_result": {"exit_code": 0, "stdout": "Merged pull request #123"},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)

            with patch("sys.stdin") as mock_stdin:
                mock_stdin.read.return_value = json.dumps(hook_input)

                with patch.object(reflection_reminder, "SESSION_DIR", state_dir):
                    with patch.object(
                        reflection_reminder,
                        "get_reflection_state_file",
                        lambda sid: state_dir / f"reflection-state-{sid}.json",
                    ):
                        mock_ctx = MagicMock()
                        mock_ctx.get_session_id.return_value = "test"
                        with patch.object(
                            reflection_reminder,
                            "create_hook_context",
                            return_value=mock_ctx,
                        ):
                            with patch.object(reflection_reminder, "log_hook_execution"):
                                with patch("builtins.print") as mock_print:
                                    reflection_reminder.main()
                                    output = json.loads(mock_print.call_args[0][0])
                                    assert "systemMessage" in output
                                    assert "PR" in output["systemMessage"]

    def test_periodic_reminder_after_stops(self):
        """一定回数のStop後に定期リマインダーを表示"""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_result": {"exit_code": 0, "stdout": ""},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)

            with patch("sys.stdin") as mock_stdin:
                mock_stdin.read.return_value = json.dumps(hook_input)

                with patch.object(reflection_reminder, "SESSION_DIR", state_dir):
                    with patch.object(
                        reflection_reminder,
                        "get_reflection_state_file",
                        lambda sid: state_dir / f"reflection-state-{sid}.json",
                    ):
                        mock_ctx = MagicMock()
                        mock_ctx.get_session_id.return_value = "test"
                        with patch.object(
                            reflection_reminder,
                            "create_hook_context",
                            return_value=mock_ctx,
                        ):
                            # 10回以上のアクションをシミュレート
                            with patch.object(
                                reflection_reminder,
                                "increment_action_count",
                                return_value=15,
                            ):
                                with patch.object(reflection_reminder, "log_hook_execution"):
                                    with patch("builtins.print") as mock_print:
                                        reflection_reminder.main()
                                        output = json.loads(mock_print.call_args[0][0])
                                        assert "systemMessage" in output
                                        assert "15" in output["systemMessage"]

    def test_exception_handling(self):
        """例外発生時もcontinue: trueを返す"""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.side_effect = Exception("Test error")

            with patch("builtins.print") as mock_print:
                reflection_reminder.main()
                output = json.loads(mock_print.call_args[0][0])
                assert output.get("continue", True)
