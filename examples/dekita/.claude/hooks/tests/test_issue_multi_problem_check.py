#!/usr/bin/env python3
"""Tests for issue-multi-problem-check.py hook."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "issue-multi-problem-check.py"


def load_hook_module():
    """Load the hook module for testing."""
    spec = importlib.util.spec_from_file_location("hook", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_hook(input_data: dict, env: dict = None) -> tuple[int, str, str]:
    """Run the hook with given input and return (exit_code, stdout, stderr)."""
    process_env = os.environ.copy()
    if env:
        process_env.update(env)

    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        env=process_env,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


class TestExtractTitleFromCommand:
    """Tests for extract_title_from_command function."""

    def setup_method(self):
        """Load the module once per test method."""
        self.module = load_hook_module()

    def test_double_quoted_title(self):
        """Should extract title from --title with double quotes."""
        result = self.module.extract_title_from_command('gh issue create --title "AとBの実装"')
        assert result == "AとBの実装"

    def test_single_quoted_title(self):
        """Should extract title from --title with single quotes."""
        result = self.module.extract_title_from_command("gh issue create --title 'テストタイトル'")
        assert result == "テストタイトル"

    def test_short_flag(self):
        """Should extract title from -t flag."""
        result = self.module.extract_title_from_command('gh issue create -t "テスト"')
        assert result == "テスト"

    def test_equals_format(self):
        """Should extract title from --title=value format."""
        result = self.module.extract_title_from_command("gh issue create --title=タイトル")
        assert result == "タイトル"

    def test_title_with_other_options(self):
        """Should extract title when other options are present."""
        result = self.module.extract_title_from_command(
            'gh issue create --body "body" --title "タイトル" --label bug'
        )
        assert result == "タイトル"

    def test_no_title(self):
        """Should return None when no title is found."""
        result = self.module.extract_title_from_command("gh issue create --body 'some body'")
        assert result is None

    def test_empty_command(self):
        """Should return None for command without title."""
        result = self.module.extract_title_from_command("gh issue create")
        assert result is None


class TestCheckMultiProblemPatterns:
    """Tests for check_multi_problem_patterns function."""

    def setup_method(self):
        """Load the module once per test method."""
        self.module = load_hook_module()

    def test_and_pattern_ja(self):
        """Should detect 'AとBの実装' pattern."""
        warnings = self.module.check_multi_problem_patterns("AとBの実装")
        assert len(warnings) == 1
        assert "A" in warnings[0] and "B" in warnings[0]

    def test_and_pattern_improvement(self):
        """Should detect 'AとBの改善' pattern."""
        warnings = self.module.check_multi_problem_patterns("機能AとBの改善")
        assert len(warnings) == 1

    def test_comma_pattern(self):
        """Should detect 'A、Bを実装' pattern."""
        warnings = self.module.check_multi_problem_patterns("機能A、Bを実装")
        assert len(warnings) == 1

    def test_oyobi_pattern(self):
        """Should detect 'AおよびB' pattern."""
        warnings = self.module.check_multi_problem_patterns("機能AおよびB")
        assert len(warnings) == 1

    def test_no_pattern(self):
        """Should not detect single problem titles."""
        warnings = self.module.check_multi_problem_patterns("フックの追加")
        assert len(warnings) == 0

    def test_exclude_detection_warning(self):
        """Should exclude '検出と警告' pattern (related actions)."""
        warnings = self.module.check_multi_problem_patterns("検出と警告の実装")
        assert len(warnings) == 0

    def test_exclude_create_delete(self):
        """Should exclude '作成と削除' pattern (paired operations)."""
        warnings = self.module.check_multi_problem_patterns("作成と削除のテスト")
        assert len(warnings) == 0

    def test_exclude_add_update(self):
        """Should exclude '追加と更新' pattern (related operations)."""
        warnings = self.module.check_multi_problem_patterns("追加と更新の処理")
        assert len(warnings) == 0

    def test_exclude_read_write(self):
        """Should exclude '読みと書き' pattern (paired operations)."""
        warnings = self.module.check_multi_problem_patterns("読み書きの実装")
        assert len(warnings) == 0

    def test_exclude_input_output(self):
        """Should exclude '入力と出力' pattern (paired operations)."""
        warnings = self.module.check_multi_problem_patterns("入力出力の処理")
        assert len(warnings) == 0

    def test_exclude_start_stop(self):
        """Should exclude 'startとstop' pattern (paired operations)."""
        warnings = self.module.check_multi_problem_patterns("start and stop implementation")
        assert len(warnings) == 0


class TestHookIntegration:
    """Integration tests for the hook."""

    def test_non_gh_issue_command(self):
        """Should exit 0 for non gh issue create commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        }
        exit_code, stdout, stderr = run_hook(input_data)
        assert exit_code == 0
        assert stdout == ""

    def test_gh_issue_without_title(self):
        """Should exit 0 for gh issue create without title."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --body 'test'"},
        }
        exit_code, stdout, stderr = run_hook(input_data)
        assert exit_code == 0
        assert stdout == ""

    def test_single_problem_title(self):
        """Should exit 0 for single problem title (no warning)."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": 'gh issue create --title "フックの追加"'},
        }
        exit_code, stdout, stderr = run_hook(input_data)
        assert exit_code == 0
        assert stdout == ""

    def test_multi_problem_title_block(self):
        """Should block for multi-problem title (Issue #2240)."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": 'gh issue create --title "AとBの実装"'},
        }
        exit_code, stdout, stderr = run_hook(input_data)
        assert exit_code == 2
        result = json.loads(stdout)
        assert result["decision"] == "block"
        assert "複数の問題を含んでいる" in result["reason"]

    def test_excluded_pattern_no_warning(self):
        """Should not warn for excluded patterns."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": 'gh issue create --title "検出と警告の実装"'},
        }
        exit_code, stdout, stderr = run_hook(input_data)
        assert exit_code == 0
        assert stdout == ""
