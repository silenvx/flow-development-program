#!/usr/bin/env python3
"""
check-hook-patterns.py のテスト
"""

import ast
import sys
from pathlib import Path

# scriptsディレクトリをパスに追加
SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

# ハイフン付きファイル名のインポート
import importlib.util

spec = importlib.util.spec_from_file_location(
    "check_hook_patterns",
    SCRIPTS_DIR / "check_hook_patterns.py",
)
check_hook_patterns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_hook_patterns)


class TestCheckToolResponseFallback:
    """check_tool_response_fallback のテスト"""

    def test_detects_tool_result_only(self):
        """tool_result のみ使用しているコードを検出"""
        source = """
input_data = parse_hook_input()
tool_result = input_data.get("tool_result", {})
"""
        tree = ast.parse(source)
        errors = check_hook_patterns.check_tool_response_fallback(tree, source, "test.py")
        assert len(errors) == 1
        assert errors[0].code == "PATTERN001"
        assert errors[0].level == "error"

    def test_detects_subscript_access(self):
        """サブスクリプトアクセス input_data["tool_result"] を検出"""
        source = """
input_data = parse_hook_input()
tool_result = input_data["tool_result"]
"""
        tree = ast.parse(source)
        errors = check_hook_patterns.check_tool_response_fallback(tree, source, "test.py")
        assert len(errors) == 1
        assert errors[0].code == "PATTERN001"
        assert errors[0].level == "error"

    def test_accepts_subscript_with_both_keys(self):
        """サブスクリプトアクセスで両方のキーに対応していれば通過"""
        source = """
input_data = parse_hook_input()
if "tool_result" in input_data:
    tool_result = input_data["tool_result"]
else:
    tool_result = input_data["tool_response"]
"""
        tree = ast.parse(source)
        errors = check_hook_patterns.check_tool_response_fallback(tree, source, "test.py")
        assert len(errors) == 0

    def test_accepts_both_keys(self):
        """両方のキーに対応しているコードは通過"""
        source = """
input_data = parse_hook_input()
if "tool_result" in input_data:
    tool_result = input_data.get("tool_result") or {}
else:
    tool_result = input_data.get("tool_response", {})
"""
        tree = ast.parse(source)
        errors = check_hook_patterns.check_tool_response_fallback(tree, source, "test.py")
        assert len(errors) == 0

    def test_accepts_tool_response_only(self):
        """tool_response のみ使用も通過（tool_result なし）"""
        source = """
input_data = parse_hook_input()
tool_response = input_data.get("tool_response", {})
"""
        tree = ast.parse(source)
        errors = check_hook_patterns.check_tool_response_fallback(tree, source, "test.py")
        assert len(errors) == 0

    def test_no_tool_result_usage(self):
        """tool_result を使っていないコードは通過"""
        source = """
input_data = parse_hook_input()
tool_name = input_data.get("tool_name", "")
"""
        tree = ast.parse(source)
        errors = check_hook_patterns.check_tool_response_fallback(tree, source, "test.py")
        assert len(errors) == 0

    def test_ignores_subscript_store(self):
        """辞書への書き込み（Store）は誤検知しない"""
        source = """
event = {}
event["tool_result"] = some_value
"""
        tree = ast.parse(source)
        errors = check_hook_patterns.check_tool_response_fallback(tree, source, "test.py")
        # 書き込みは読み込みではないため、エラーにならない
        assert len(errors) == 0

    def test_ignores_subscript_store_complex(self):
        """複雑な書き込みパターンも誤検知しない"""
        source = """
events = []
for tool_name, tool_result in results.items():
    event = {"name": tool_name}
    event["tool_result"] = tool_result  # 書き込み
    events.append(event)
"""
        tree = ast.parse(source)
        errors = check_hook_patterns.check_tool_response_fallback(tree, source, "test.py")
        # 書き込みは読み込みではないため、エラーにならない
        assert len(errors) == 0


class TestHasSkipComment:
    """has_skip_comment のテスト"""

    def test_detects_skip_comment(self):
        """スキップコメントを検出"""
        source = "# hook-pattern-check: skip\ncode here"
        assert check_hook_patterns.has_skip_comment(source) is True

    def test_no_skip_comment(self):
        """スキップコメントがない場合"""
        source = "# normal comment\ncode here"
        assert check_hook_patterns.has_skip_comment(source) is False


class TestGetPostToolUseHooks:
    """get_post_tool_use_hooks のテスト"""

    def test_extracts_hook_names(self):
        """フック名を正しく抽出"""
        settings = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/test-hook.py',
                            }
                        ],
                    }
                ]
            }
        }
        hooks = check_hook_patterns.get_post_tool_use_hooks(settings)
        assert "test_hook.py" in hooks

    def test_empty_settings(self):
        """空のsettingsの場合"""
        hooks = check_hook_patterns.get_post_tool_use_hooks({})
        assert len(hooks) == 0


class TestGetHooksByTrigger:
    """get_hooks_by_trigger のテスト"""

    def test_groups_by_trigger(self):
        """トリガーでグループ化"""
        settings = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook1.py',
                            },
                            {
                                "type": "command",
                                "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook2.py',
                            },
                        ],
                    }
                ]
            }
        }
        trigger_hooks = check_hook_patterns.get_hooks_by_trigger(settings)
        assert "Bash" in trigger_hooks
        assert "hook1.py" in trigger_hooks["Bash"]
        assert "hook2.py" in trigger_hooks["Bash"]


class TestCheckSimilarHooks:
    """check_similar_hooks のテスト"""

    def test_warns_similar_hooks(self):
        """類似フックの警告"""
        trigger_hooks = {
            "Bash": ["hook1.py", "hook2.py", "hook3.py"],
        }
        errors = check_hook_patterns.check_similar_hooks(".claude/hooks/hook1.py", trigger_hooks)
        assert len(errors) == 1
        assert errors[0].code == "PATTERN002"
        assert errors[0].level == "warning"
        assert "hook2.py" in errors[0].message

    def test_no_warning_single_hook(self):
        """単独フックの場合は警告なし"""
        trigger_hooks = {
            "Bash": ["hook1.py"],
        }
        errors = check_hook_patterns.check_similar_hooks(".claude/hooks/hook1.py", trigger_hooks)
        assert len(errors) == 0


class TestLintFile:
    """lint_file のテスト"""

    def test_skips_test_files(self, tmp_path):
        """テストファイルをスキップ"""
        test_file = tmp_path / "tests" / "test_hook.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("# test file")

        errors = check_hook_patterns.lint_file(test_file, {"test_hook.py"}, {})
        assert len(errors) == 0

    def test_skips_with_skip_comment(self, tmp_path):
        """スキップコメント付きファイルをスキップ"""
        hook_file = tmp_path / "hook.py"
        hook_file.write_text(
            '# hook-pattern-check: skip\ntool_result = input_data.get("tool_result", {})'
        )

        errors = check_hook_patterns.lint_file(hook_file, {"hook.py"}, {})
        assert len(errors) == 0


class TestFindToolResultPatternFiles:
    """find_tool_result_pattern_files のテスト (Issue #1852)"""

    def test_finds_pattern_files(self, tmp_path):
        """tool_resultパターンを持つファイルを検出"""
        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)

        # パターンを持つファイル
        (hooks_dir / "hook1.py").write_text('tool_result = input_data.get("tool_result", {})')
        (hooks_dir / "hook2.py").write_text('result = data.get("tool_response")')

        # パターンを持たないファイル
        (hooks_dir / "hook3.py").write_text("# no pattern here")

        # テストファイル（スキップ対象）
        (hooks_dir / "test_hook.py").write_text('input_data.get("tool_result", {})')

        result = check_hook_patterns.find_tool_result_pattern_files(hooks_dir)
        assert "hook1.py" in result
        assert "hook2.py" in result
        assert "hook3.py" not in result
        assert "test_hook.py" not in result  # テストファイルは除外

    def test_skips_files_with_skip_comment(self, tmp_path):
        """スキップコメント付きファイルを除外"""
        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)

        (hooks_dir / "hook.py").write_text(
            '# hook-pattern-check: skip\ninput_data.get("tool_result", {})'
        )

        result = check_hook_patterns.find_tool_result_pattern_files(hooks_dir)
        assert len(result) == 0

    def test_empty_dir(self, tmp_path):
        """空ディレクトリの場合"""
        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)

        result = check_hook_patterns.find_tool_result_pattern_files(hooks_dir)
        assert len(result) == 0

    def test_nonexistent_dir(self, tmp_path):
        """存在しないディレクトリの場合"""
        hooks_dir = tmp_path / "nonexistent"

        result = check_hook_patterns.find_tool_result_pattern_files(hooks_dir)
        assert len(result) == 0


class TestCheckPatternCoverage:
    """check_pattern_coverage のテスト (Issue #1852)"""

    def test_warns_missing_files(self, tmp_path):
        """コミット対象に含まれていないパターンファイルを警告"""
        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)

        # コミット対象ファイル
        modified = [hooks_dir / "hook1.py"]

        # 全パターンファイル（hook1, hook2, hook3）
        all_pattern_files = {"hook1.py", "hook2.py", "hook3.py"}

        errors = check_hook_patterns.check_pattern_coverage(modified, all_pattern_files)
        assert len(errors) == 1
        assert errors[0].code == "PATTERN003"
        assert errors[0].level == "warning"
        assert "hook2.py" in errors[0].message or "hook3.py" in errors[0].message

    def test_no_warning_when_all_covered(self, tmp_path):
        """全てのパターンファイルがコミット対象に含まれる場合は警告なし"""
        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)

        modified = [hooks_dir / "hook1.py", hooks_dir / "hook2.py"]
        all_pattern_files = {"hook1.py", "hook2.py"}

        errors = check_hook_patterns.check_pattern_coverage(modified, all_pattern_files)
        assert len(errors) == 0

    def test_no_warning_when_no_pattern_files_modified(self, tmp_path):
        """パターンファイルが修正対象に含まれない場合は警告なし"""
        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)

        # 別のファイルのみ修正
        modified = [hooks_dir / "other.py"]
        all_pattern_files = {"hook1.py", "hook2.py"}

        errors = check_hook_patterns.check_pattern_coverage(modified, all_pattern_files)
        assert len(errors) == 0

    def test_handles_non_hook_files(self, tmp_path):
        """非フックファイルは無視"""
        modified = [tmp_path / "frontend" / "app.tsx"]
        all_pattern_files = {"hook1.py"}

        errors = check_hook_patterns.check_pattern_coverage(modified, all_pattern_files)
        assert len(errors) == 0
