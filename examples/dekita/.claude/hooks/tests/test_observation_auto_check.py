"""Tests for observation-auto-check.py."""

import json
from unittest.mock import patch

from conftest import load_hook_module

hook = load_hook_module("observation-auto-check")


class TestFindMatchingChecklistItem:
    """Test find_matching_checklist_item function."""

    def test_matches_hook_log_grep(self):
        """grep hook-execution がフック発火項目にマッチする."""
        result = hook.find_matching_checklist_item(
            "Bash",
            "grep observation-auto-check .claude/logs/execution/hook-execution-*.jsonl",
        )
        assert result == "フックが正しく発火する"

    def test_matches_hook_log_cat_pipe_grep(self):
        """cat | grep パターンがフック発火項目にマッチする."""
        result = hook.find_matching_checklist_item(
            "Bash",
            "cat .claude/logs/execution/hook-execution-*.jsonl | grep my-hook | tail -5",
        )
        assert result == "フックが正しく発火する"

    def test_matches_hook_log_grep_pipe_tail(self):
        """grep | tail パターンがフック発火項目にマッチする."""
        result = hook.find_matching_checklist_item(
            "Bash",
            "grep my-hook .claude/logs/execution/hook-execution-*.jsonl | tail -10",
        )
        assert result == "フックが正しく発火する"

    def test_no_match_unrelated_hook_execution(self):
        """無関係なhook-executionファイル名はマッチしない."""
        result = hook.find_matching_checklist_item(
            "Bash",
            "grep something file.txt | cat hook-execution-result.jsonl",
        )
        assert result is None

    def test_matches_pnpm_build(self):
        """pnpm buildがビルド項目にマッチする."""
        result = hook.find_matching_checklist_item("Bash", "pnpm build")
        assert result == "ビルドが成功する（`pnpm build`）"

    def test_matches_pnpm_test_ci(self):
        """pnpm test:ciがテスト項目にマッチする."""
        result = hook.find_matching_checklist_item("Bash", "pnpm test:ci")
        assert result == "テストが全てパスする（`pnpm test:ci`）"

    def test_matches_typecheck(self):
        """pnpm typecheckが型定義項目にマッチする."""
        result = hook.find_matching_checklist_item("Bash", "pnpm typecheck")
        assert result == "型定義の変更がfrontend/workerで正しく反映される"

    def test_matches_script_help(self):
        """--helpオプションがヘルプ項目にマッチする."""
        result = hook.find_matching_checklist_item(
            "Bash", "python3 .claude/scripts/ci-monitor.py --help"
        )
        assert result == "ヘルプオプション（--help）が動作する"

    def test_matches_api_health(self):
        """curl APIがAPI項目にマッチする."""
        result = hook.find_matching_checklist_item(
            "Bash", "curl -s https://api.dekita.app/health | jq ."
        )
        assert result == "APIが正常にレスポンスを返す"

    def test_matches_gh_run_list(self):
        """gh run listがCI項目にマッチする."""
        result = hook.find_matching_checklist_item("Bash", "gh run list --limit 3")
        assert result == "CIが正常に動作する"

    def test_matches_settings_read(self):
        """settings.jsonの読み取りが設定項目にマッチする."""
        result = hook.find_matching_checklist_item("Read", "/path/to/settings.json")
        assert result == "設定変更が反映される"

    def test_matches_script_execution(self):
        """スクリプト実行がスクリプト項目にマッチする."""
        result = hook.find_matching_checklist_item(
            "Bash", "python3 .claude/scripts/analyze-review-quality.py"
        )
        assert result == "スクリプトが正常に実行できる"

    def test_no_match_unrelated_command(self):
        """無関係なコマンドはマッチしない."""
        result = hook.find_matching_checklist_item("Bash", "ls -la")
        assert result is None

    def test_no_match_wrong_tool(self):
        """ツール名が異なる場合はマッチしない."""
        result = hook.find_matching_checklist_item("Write", "python -m pytest")
        assert result is None


class TestIsHumanItem:
    """Test is_human_item function."""

    def test_ui_display_is_human(self):
        """UI表示確認は人間項目."""
        assert hook.is_human_item("UI表示が崩れていない（本番URL確認）")

    def test_mobile_is_human(self):
        """モバイル確認は人間項目."""
        assert hook.is_human_item("モバイル表示に問題がない")

    def test_accessibility_is_human(self):
        """アクセシビリティ確認は人間項目."""
        assert hook.is_human_item("アクセシビリティに問題がない")

    def test_error_handling_is_human(self):
        """エラーハンドリング確認は人間項目."""
        assert hook.is_human_item("エラーハンドリングが正しく動作する")

    def test_error_response_is_human(self):
        """エラーレスポンス確認は人間項目."""
        assert hook.is_human_item("エラーレスポンスが適切に返る")

    def test_build_is_not_human(self):
        """ビルドは人間項目ではない."""
        assert not hook.is_human_item("ビルドが成功する")


class TestUpdateChecklistItem:
    """Test update_checklist_item function."""

    def test_updates_unchecked_item(self):
        """未チェック項目をチェック済みに更新する."""
        body = """## Claude Code確認項目

- [ ] ビルドが成功する
- [ ] フックが正しく発火する
"""
        success, updated = hook.update_checklist_item(body, "ビルドが成功する")
        assert success
        assert "- [x] ビルドが成功する" in updated
        assert "- [ ] フックが正しく発火する" in updated

    def test_already_checked_returns_false(self):
        """既にチェック済みの場合はFalseを返す."""
        body = """## Claude Code確認項目

- [x] ビルドが成功する
- [ ] フックが正しく発火する
"""
        success, _ = hook.update_checklist_item(body, "ビルドが成功する")
        assert not success

    def test_item_not_found_returns_false(self):
        """項目が見つからない場合はFalseを返す."""
        body = """## Claude Code確認項目

- [ ] フックが正しく発火する
"""
        success, _ = hook.update_checklist_item(body, "ビルドが成功する")
        assert not success


class TestCountClaudeItems:
    """Test count_claude_items function."""

    def test_counts_all_items(self):
        """全項目を正しくカウントする."""
        body = """## Claude Code確認項目

- [x] ビルドが成功する
- [ ] フックが正しく発火する
- [x] スクリプトが正常に実行できる

## 人間確認項目
- [ ] UI表示が崩れていない
"""
        total, checked = hook.count_claude_items(body)
        assert total == 3
        assert checked == 2

    def test_no_section_returns_zero(self):
        """セクションがない場合は0を返す."""
        body = "No checklist here"
        total, checked = hook.count_claude_items(body)
        assert total == 0
        assert checked == 0

    def test_all_checked(self):
        """全項目チェック済みの場合."""
        body = """## Claude Code確認項目

- [x] ビルドが成功する
- [x] フックが正しく発火する
"""
        total, checked = hook.count_claude_items(body)
        assert total == 2
        assert checked == 2


class TestMainIntegration:
    """Integration tests for main function."""

    def test_skips_non_bash_tool(self):
        """Bash/Read以外のツールはスキップする."""
        hook_input = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/path/to/file"},
        }

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(hook_input)
            # Should return without processing
            with patch.object(hook, "process_observation_issues") as mock_process:
                hook.main()
                mock_process.assert_not_called()

    def test_skips_failed_command(self):
        """失敗したコマンドはスキップする."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest"},
            "tool_result": {"exit_code": 1},
        }

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(hook_input)
            with patch.object(hook, "process_observation_issues") as mock_process:
                hook.main()
                mock_process.assert_not_called()

    def test_skips_unmatched_command(self):
        """マッチしないコマンドはスキップする."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "tool_result": {"exit_code": 0},
        }

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(hook_input)
            with patch.object(hook, "process_observation_issues") as mock_process:
                hook.main()
                mock_process.assert_not_called()

    def test_processes_matched_successful_command(self):
        """マッチする成功コマンドを処理する."""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "pnpm build"},
            "tool_result": {"exit_code": 0},
        }

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(hook_input)
            with patch.object(hook, "process_observation_issues", return_value=[]) as mock_process:
                hook.main()
                mock_process.assert_called_once_with("ビルドが成功する（`pnpm build`）")

    def test_skips_read_tool_with_error(self):
        """Readツールでエラーがある場合はスキップする."""
        hook_input = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/path/to/settings.json"},
            "tool_result": {"is_error": True},
        }

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(hook_input)
            with patch.object(hook, "process_observation_issues") as mock_process:
                hook.main()
                mock_process.assert_not_called()

    def test_processes_successful_read_tool(self):
        """成功したReadツールを処理する."""
        hook_input = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/path/to/settings.json"},
            "tool_result": {"content": "file content"},
        }

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(hook_input)
            with patch.object(hook, "process_observation_issues", return_value=[]) as mock_process:
                hook.main()
                mock_process.assert_called_once_with("設定変更が反映される")
