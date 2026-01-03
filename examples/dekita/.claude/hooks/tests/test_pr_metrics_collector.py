#!/usr/bin/env python3
"""
pr_metrics_collector.py のテスト
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# テスト対象のモジュールをインポート
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.github import extract_pr_number


class TestExtractPrNumber:
    """extract_pr_numberのテスト

    Note: Tests the unified extract_pr_number from common.py (Issue #557).
    - Returns str instead of int
    - Extracts from ALL gh pr commands, not just merge
    """

    def test_extract_with_number(self):
        # Now returns str instead of int
        assert extract_pr_number("gh pr merge 123") == "123"

    def test_extract_with_hash(self):
        # Now returns str instead of int
        assert extract_pr_number("gh pr merge #123") == "123"

    def test_extract_with_squash_option(self):
        """--squashオプション付きでもPR番号を抽出"""
        assert extract_pr_number("gh pr merge --squash 123") == "123"

    def test_extract_with_trailing_option(self):
        """PR番号の後にオプションがあっても抽出"""
        assert extract_pr_number("gh pr merge 123 --delete-branch") == "123"

    def test_extract_with_multiple_options(self):
        """複数オプション付きでもPR番号を抽出"""
        assert extract_pr_number("gh pr merge --squash --delete-branch 456") == "456"

    def test_extract_no_number(self):
        assert extract_pr_number("gh pr merge") is None

    def test_extract_from_view_command(self):
        """gh pr viewコマンドからもPR番号を抽出（新動作）"""
        # Now extracts from all gh pr commands
        assert extract_pr_number("gh pr view 123") == "123"


class TestPrMetricsCollectorHook:
    """PRメトリクス収集フックのテスト"""

    def test_non_bash_tool_skipped(self):
        """Bash以外のツールはスキップされる"""
        hook_input = {"tool_name": "Read", "tool_input": {}}

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(hook_input)

            # モジュールを再インポートしてmainを実行
            import pr_metrics_collector

            with patch.object(pr_metrics_collector, "log_hook_execution"):
                with patch("builtins.print") as mock_print:
                    pr_metrics_collector.main()
                    output = json.loads(mock_print.call_args[0][0])
                    assert output["continue"]

    def test_non_merge_command_skipped(self):
        """gh pr merge以外のコマンドはスキップされる"""
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr view 123"},
            "tool_result": {},
        }

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(hook_input)

            import pr_metrics_collector

            with patch.object(pr_metrics_collector, "log_hook_execution"):
                with patch("builtins.print") as mock_print:
                    pr_metrics_collector.main()
                    output = json.loads(mock_print.call_args[0][0])
                    assert output["continue"]
