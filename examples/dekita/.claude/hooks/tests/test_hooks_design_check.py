#!/usr/bin/env python3
"""Tests for hooks-design-check.py hook."""

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

# Load the module with hyphenated name using importlib
hooks_dir = Path(__file__).parent.parent
hook_file = hooks_dir / "hooks-design-check.py"
spec = importlib.util.spec_from_file_location("hooks_design_check", hook_file)
hooks_design_check = importlib.util.module_from_spec(spec)
sys.modules["hooks_design_check"] = hooks_design_check
spec.loader.exec_module(hooks_design_check)

# Extract symbols from dynamically loaded module for use in tests
REMEDIATION_KEYWORDS = hooks_design_check.REMEDIATION_KEYWORDS
BlockResultVisitor = hooks_design_check.BlockResultVisitor

HOOK_PATH = hook_file


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestHooksDesignCheckApprove:
    """Tests for commands that should be approved."""

    def test_approves_non_commit_commands(self):
        """Should approve non-commit commands."""
        result = run_hook({"tool_input": {"command": "git status"}})
        assert result["decision"] == "approve"

    def test_approves_git_push(self):
        """Should approve git push."""
        result = run_hook({"tool_input": {"command": "git push"}})
        assert result["decision"] == "approve"

    def test_approves_empty_command(self):
        """Should approve empty command."""
        result = run_hook({"tool_input": {"command": ""}})
        assert result["decision"] == "approve"

    def test_handles_missing_tool_input(self):
        """Should handle missing tool_input gracefully."""
        result = run_hook({})
        assert result["decision"] == "approve"


class TestHooksDesignCheckDeletion:
    """Tests for hook file deletion detection (Issue #193).

    Note: Deletion is approved with a warning (systemMessage) rather than blocked,
    because blocking would make hook deletion impossible even in new sessions.
    """

    def test_warns_rm_hook_file(self):
        """Should warn on rm command targeting hook file."""
        result = run_hook({"tool_input": {"command": "rm .claude/hooks/foo-check.py"}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]
        assert "foo-check.py" in result["systemMessage"]

    def test_warns_rm_f_hook_file(self):
        """Should warn on rm -f command targeting hook file."""
        result = run_hook({"tool_input": {"command": "rm -f .claude/hooks/bar-check.py"}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]

    def test_warns_git_rm_hook_file(self):
        """Should warn on git rm command targeting hook file."""
        result = run_hook({"tool_input": {"command": "git rm .claude/hooks/baz-check.py"}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]

    def test_warns_rm_rf_hooks_directory(self):
        """Should warn on rm -rf on hooks directory."""
        result = run_hook({"tool_input": {"command": "rm -rf .claude/hooks/"}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]

    def test_approves_rm_non_hook_file(self):
        """Should approve rm command for non-hook files without warning."""
        result = run_hook({"tool_input": {"command": "rm some-other-file.py"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_approves_rm_test_hook_file(self):
        """Should approve rm command for test files in hooks directory."""
        result = run_hook({"tool_input": {"command": "rm .claude/hooks/tests/test_foo.py"}})
        assert result["decision"] == "approve"

    def test_warns_multiple_hook_files(self):
        """Should warn on rm command with multiple hook files."""
        result = run_hook(
            {"tool_input": {"command": "rm .claude/hooks/foo-check.py .claude/hooks/bar-check.py"}}
        )
        assert result["decision"] == "approve"
        assert "foo-check.py" in result["systemMessage"]
        assert "bar-check.py" in result["systemMessage"]


class TestHooksDesignCheckDeletionEdgeCases:
    """Tests for edge cases in hook file deletion detection (Issue #198).

    Covers:
    - Quoted paths (single/double quotes)
    - Non-standard flag positions (rm file -f)
    - Shell operators without space (&&rm, ;rm)
    """

    def test_warns_rm_double_quoted_path(self):
        """Should warn on rm with double-quoted hook file path."""
        result = run_hook({"tool_input": {"command": 'rm ".claude/hooks/foo-check.py"'}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]
        assert "foo-check.py" in result["systemMessage"]

    def test_warns_rm_single_quoted_path(self):
        """Should warn on rm with single-quoted hook file path."""
        result = run_hook({"tool_input": {"command": "rm '.claude/hooks/bar-check.py'"}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]
        assert "bar-check.py" in result["systemMessage"]

    def test_warns_rm_flag_after_path(self):
        """Should warn on rm with flags after file path (non-standard position)."""
        result = run_hook({"tool_input": {"command": "rm .claude/hooks/baz-check.py -f"}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]
        assert "baz-check.py" in result["systemMessage"]

    def test_warns_git_rm_f_quoted_path(self):
        """Should warn on git rm -f with quoted path."""
        result = run_hook({"tool_input": {"command": 'git rm -f ".claude/hooks/qux-check.py"'}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]
        assert "qux-check.py" in result["systemMessage"]

    def test_warns_rm_rf_quoted_directory(self):
        """Should warn on rm -rf with quoted hooks directory."""
        result = run_hook({"tool_input": {"command": 'rm -rf ".claude/hooks/"'}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]

    def test_warns_rm_chained_with_and_operator(self):
        """Should warn on rm chained after && without space."""
        result = run_hook({"tool_input": {"command": "cd repo&&rm .claude/hooks/foo-check.py"}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]
        assert "foo-check.py" in result["systemMessage"]

    def test_warns_rm_chained_with_semicolon(self):
        """Should warn on rm chained after ; without space."""
        result = run_hook({"tool_input": {"command": "cd repo;rm .claude/hooks/bar-check.py"}})
        assert result["decision"] == "approve"
        assert "フックファイル削除を検出" in result["systemMessage"]
        assert "bar-check.py" in result["systemMessage"]


class TestHooksDesignCheckMismatchedQuotes:
    """Tests for mismatched quote handling (Issue #363).

    Mismatched quotes like 'path" or "path' are invalid shell syntax
    and should not be detected as hook file deletions.
    """

    def test_ignores_mismatched_single_then_double_quote(self):
        """Should NOT detect hook when quotes are mismatched (single then double)."""
        result = run_hook({"tool_input": {"command": "rm '.claude/hooks/foo-check.py\""}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignores_mismatched_double_then_single_quote(self):
        """Should NOT detect hook when quotes are mismatched (double then single)."""
        result = run_hook({"tool_input": {"command": "rm \".claude/hooks/bar-check.py'"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignores_opening_quote_only(self):
        """Should NOT detect hook with only opening quote."""
        result = run_hook({"tool_input": {"command": "rm '.claude/hooks/baz-check.py"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_ignores_closing_quote_only(self):
        """Should NOT detect hook with only closing quote."""
        result = run_hook({"tool_input": {"command": "rm .claude/hooks/qux-check.py'"}})
        assert result["decision"] == "approve"
        assert "systemMessage" not in result


class TestHooksDesignCheckIntegration:
    """Integration tests that require git operations.

    These tests are skipped in CI but can be run locally.
    """

    def test_approves_commit_without_new_hooks(self):
        """Should approve commit when no new hooks are staged.

        Note: This test runs git commit detection but won't find new hooks
        in the current staging area (since we're not actually staging files).
        """
        result = run_hook({"tool_input": {"command": "git commit -m 'test'"}})
        # Will approve because no new hooks are staged
        assert result["decision"] == "approve"


class TestBlockResultVisitor:
    """Tests for the AST visitor that checks make_block_result calls (Issue #1111)."""

    def test_detects_missing_remediation_simple(self):
        """Should detect missing remediation in simple string."""
        code = """
make_block_result("hook-name", "エラーが発生しました")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        assert len(visitor.issues) == 1
        assert visitor.issues[0][0] == 2  # line number

    def test_allows_with_taishohou_bracket(self):
        """Should allow messages with 【対処法】."""
        code = """
make_block_result("hook-name", "エラー\\n\\n【対処法】\\n1. 修正してください")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        assert len(visitor.issues) == 0

    def test_allows_with_remediation_header(self):
        """Should allow messages with ## 対処法."""
        code = """
make_block_result("hook-name", "エラー\\n\\n## 対処法\\n1. 修正してください")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        assert len(visitor.issues) == 0

    def test_allows_with_remediation_colon(self):
        """Should allow messages with 対処法:."""
        code = """
make_block_result("hook-name", "エラー 対処法: 修正してください")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        assert len(visitor.issues) == 0

    def test_handles_f_string_with_remediation(self):
        """Should skip dynamic f-strings even with remediation (Issue #1125).

        Dynamic f-strings (with {variable}) are skipped entirely because
        we can't statically determine if the dynamic parts contain remediation.
        """
        code = """
make_block_result("hook-name", f"エラー: {error}\\n\\n【対処法】\\n修正してください")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        # Dynamic f-string is skipped, so no issues reported
        assert len(visitor.issues) == 0

    def test_skips_dynamic_f_string_without_remediation(self):
        """Should skip dynamic f-strings even without remediation (Issue #1125).

        This is the key fix for Issue #1125: previously, this would extract
        static parts only ("エラー: \\n\\n") and report a false positive.
        Now it skips the check entirely because of the dynamic {error} part.
        """
        code = """
make_block_result("hook-name", f"エラー: {error}\\n\\n{remediation_section}")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        # Dynamic f-string is skipped (no false positive)
        assert len(visitor.issues) == 0

    def test_checks_fully_static_f_string(self):
        """Should check f-strings without any dynamic parts.

        A fully static f-string (no {variable}) should be checked normally.
        """
        code = """
make_block_result("hook-name", f"エラーが発生しました")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        # Fully static f-string without remediation should be flagged
        assert len(visitor.issues) == 1

    def test_allows_fully_static_f_string_with_remediation(self):
        """Should allow fully static f-strings with remediation."""
        code = """
make_block_result("hook-name", f"エラー\\n\\n【対処法】\\n修正してください")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        assert len(visitor.issues) == 0

    def test_handles_string_concatenation_with_remediation(self):
        """Should recognize remediation in concatenated strings."""
        code = """
make_block_result("hook-name", "エラー\\n\\n" + "【対処法】\\n修正してください")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        assert len(visitor.issues) == 0

    def test_handles_empty_prefix_concatenation(self):
        """Should recognize remediation when left side is empty string."""
        code = """
make_block_result("hook-name", "" + "【対処法】\\n修正してください")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        assert len(visitor.issues) == 0

    def test_skips_variable_reference(self):
        """Should skip variable references (can't statically analyze)."""
        code = """
reason = build_reason()
make_block_result("hook-name", reason)
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        # Variable references can't be analyzed, so we skip them
        assert len(visitor.issues) == 0

    def test_skips_variable_plus_literal_concatenation(self):
        """Should skip variable + literal concatenation (Issue #1128).

        When a variable is concatenated with a string literal containing
        remediation keywords, we can't statically determine the full string,
        so we should skip checking it (not report as missing remediation).
        """
        code = """
reason = build_reason()
make_block_result("hook-name", reason + "\\n\\n【対処法】\\n修正してください")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        # Variable + literal can't be analyzed, so we skip
        assert len(visitor.issues) == 0

    def test_skips_literal_plus_variable_concatenation(self):
        """Should skip literal + variable concatenation (Issue #1128).

        When a literal is concatenated with a variable, we can't statically
        determine the full content, regardless of whether the literal contains
        remediation keywords.
        """
        code = """
suffix = get_suffix()
make_block_result("hook-name", "エラー発生\\n\\n【対処法】\\n" + suffix)
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        # Literal + variable can't be analyzed, so we skip
        assert len(visitor.issues) == 0

    def test_detects_multiple_missing_remediations(self):
        """Should detect multiple missing remediations."""
        code = """
make_block_result("hook1", "エラー1")
make_block_result("hook2", "エラー2\\n\\n【対処法】\\n修正")
make_block_result("hook3", "エラー3")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        # Only hook1 and hook3 should be flagged
        assert len(visitor.issues) == 2
        assert visitor.issues[0][0] == 2  # line 2
        assert visitor.issues[1][0] == 4  # line 4

    def test_allows_kaiketsu_houhou(self):
        """Should allow 【解決方法】."""
        code = """
make_block_result("hook-name", "エラー\\n\\n【解決方法】\\n修正してください")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        assert len(visitor.issues) == 0

    def test_allows_kaihi_houhou(self):
        """Should allow 【回避方法】."""
        code = """
make_block_result("hook-name", "エラー\\n\\n【回避方法】\\n回避してください")
"""
        tree = ast.parse(code)
        visitor = BlockResultVisitor()
        visitor.visit(tree)
        assert len(visitor.issues) == 0


class TestRemediationKeywords:
    """Tests for the REMEDIATION_KEYWORDS list."""

    def test_keywords_exist(self):
        """Should have remediation keywords defined."""
        assert len(REMEDIATION_KEYWORDS) > 0

    def test_contains_taishohou(self):
        """Should contain 【対処法】."""
        assert "【対処法】" in REMEDIATION_KEYWORDS

    def test_contains_kaiketsu(self):
        """Should contain 【解決方法】."""
        assert "【解決方法】" in REMEDIATION_KEYWORDS

    def test_contains_kaihi(self):
        """Should contain 【回避方法】."""
        assert "【回避方法】" in REMEDIATION_KEYWORDS


class TestRemediationCheckIntegration:
    """Integration tests for remediation check in git commit flow.

    Note: Full integration tests require git staging which is complex to set up.
    These tests verify the check_remediation_in_hooks function works correctly
    with realistic file paths and content.
    """

    def test_check_remediation_function_exists(self):
        """Verify the check_remediation_in_hooks function is accessible."""
        check_fn = getattr(hooks_design_check, "check_remediation_in_hooks", None)
        assert check_fn is not None
        assert callable(check_fn)

    def test_remediation_warning_message_format(self):
        """Verify the warning message contains expected elements."""
        warning = getattr(hooks_design_check, "REMEDIATION_MISSING_WARNING", "")
        assert "make_block_result()" in warning
        assert "Issue #1111" in warning
        assert "【対処法】" in warning


# Extract LogExecutionVisitor from dynamically loaded module
LogExecutionVisitor = getattr(hooks_design_check, "LogExecutionVisitor", None)


class TestLogExecutionVisitor:
    """Tests for the LogExecutionVisitor class (Issue #2589)."""

    def test_detects_log_hook_execution_call(self):
        """Should detect log_hook_execution function call."""
        code = """
log_hook_execution("hook-name", "approve", "reason")
"""
        tree = ast.parse(code)
        visitor = LogExecutionVisitor()
        visitor.visit(tree)
        assert visitor.has_call is True

    def test_detects_log_hook_execution_in_function(self):
        """Should detect log_hook_execution inside a function."""
        code = """
def main():
    result = do_something()
    log_hook_execution("my-hook", "approve")
    print(result)
"""
        tree = ast.parse(code)
        visitor = LogExecutionVisitor()
        visitor.visit(tree)
        assert visitor.has_call is True

    def test_reports_missing_log_hook_execution(self):
        """Should report when log_hook_execution is not called."""
        code = """
def main():
    result = do_something()
    print(result)
"""
        tree = ast.parse(code)
        visitor = LogExecutionVisitor()
        visitor.visit(tree)
        assert visitor.has_call is False

    def test_detects_log_hook_execution_with_extra_args(self):
        """Should detect log_hook_execution with additional arguments."""
        code = """
log_hook_execution("hook-name", "block", "reason", {"key": "value"})
"""
        tree = ast.parse(code)
        visitor = LogExecutionVisitor()
        visitor.visit(tree)
        assert visitor.has_call is True

    def test_ignores_similar_function_names(self):
        """Should not match similar but different function names."""
        code = """
log_execution("hook-name", "approve")
hook_execution_log("hook-name", "approve")
"""
        tree = ast.parse(code)
        visitor = LogExecutionVisitor()
        visitor.visit(tree)
        assert visitor.has_call is False

    def test_detects_log_hook_execution_as_attribute(self):
        """Should detect log_hook_execution called as an attribute (module.func)."""
        code = """
execution.log_hook_execution("hook-name", "approve")
"""
        tree = ast.parse(code)
        visitor = LogExecutionVisitor()
        visitor.visit(tree)
        assert visitor.has_call is True


class TestLogExecutionMissingMessage:
    """Tests for the LOG_EXECUTION_MISSING_MSG message (Issue #2589)."""

    def test_message_exists(self):
        """Verify the log execution missing message is defined."""
        msg = getattr(hooks_design_check, "LOG_EXECUTION_MISSING_MSG", "")
        assert len(msg) > 0

    def test_message_contains_issue_number(self):
        """Message should reference Issue #2589."""
        msg = getattr(hooks_design_check, "LOG_EXECUTION_MISSING_MSG", "")
        assert "Issue #2589" in msg

    def test_message_contains_example(self):
        """Message should contain usage example."""
        msg = getattr(hooks_design_check, "LOG_EXECUTION_MISSING_MSG", "")
        assert "log_hook_execution" in msg
        assert "lib.execution" in msg

    def test_message_contains_taishohou(self):
        """Message should contain remediation section."""
        msg = getattr(hooks_design_check, "LOG_EXECUTION_MISSING_MSG", "")
        assert "対処法" in msg


class TestCheckLogExecutionUsage:
    """Tests for the check_log_execution_usage function (Issue #2589)."""

    def test_function_exists(self):
        """Verify the check_log_execution_usage function is accessible."""
        check_fn = getattr(hooks_design_check, "check_log_execution_usage", None)
        assert check_fn is not None
        assert callable(check_fn)
