#!/usr/bin/env python3
"""Tests for subprocess-lint-check hook."""

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Add parent directory to path for importing hook modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from subprocess_lint_check import SubprocessVisitor, analyze_file, is_git_commit_command


class TestIsGitCommitCommand:
    """Tests for is_git_commit_command function."""

    def test_simple_commit(self):
        assert is_git_commit_command("git commit -m 'test'") is True

    def test_commit_with_chain(self):
        assert is_git_commit_command("git add . && git commit -m 'test'") is True

    def test_not_commit(self):
        assert is_git_commit_command("git status") is False

    def test_echo_contains_commit(self):
        """Quoted strings should not trigger false positives."""
        assert is_git_commit_command('echo "git commit"') is False


class TestSubprocessVisitor:
    """Tests for SubprocessVisitor AST analyzer."""

    def test_detect_shell_true(self):
        """Should detect subprocess.run with shell=True."""
        code = """
import subprocess
subprocess.run("ls -la", shell=True)
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 1
        assert visitor.issues[0]["type"] == "shell_true"
        assert "shell=True" in visitor.issues[0]["message"]

    def test_allow_shell_false(self):
        """Should allow subprocess.run with shell=False."""
        code = """
import subprocess
subprocess.run(["ls", "-la"], shell=False)
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 0

    def test_detect_pipe_in_list(self):
        """Should detect pipe operator in list arguments."""
        code = """
import subprocess
subprocess.run(["ls", "|", "grep", "test"])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 1
        assert visitor.issues[0]["type"] == "shell_operator_in_list"
        assert "|" in visitor.issues[0]["message"]

    def test_detect_redirect_in_list(self):
        """Should detect redirect operator in list arguments."""
        code = """
import subprocess
subprocess.run(["ls", "2>&1"])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 1
        assert visitor.issues[0]["type"] == "shell_operator_in_list"
        assert "2>&1" in visitor.issues[0]["message"]

    def test_detect_and_operator_in_list(self):
        """Should detect && operator in list arguments."""
        code = """
import subprocess
subprocess.run(["cmd1", "&&", "cmd2"])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 1
        assert visitor.issues[0]["type"] == "shell_operator_in_list"
        assert "&&" in visitor.issues[0]["message"]

    def test_allow_jq_pipe_operator(self):
        """Should NOT flag pipe in jq expressions (jq uses | internally)."""
        code = """
import subprocess
subprocess.run([
    "gh", "api", "--jq",
    '.[] | select(.name) | {id: .id}'
])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 0

    def test_allow_git_format_pipe_separator(self):
        """Should NOT flag pipe in git --format strings (used as field separator)."""
        code = """
import subprocess
subprocess.run([
    "git", "log", "-1", "--format=%ar|%s", "main"
])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 0

    def test_detect_pipe_after_variable_jq_expression(self):
        """Should detect pipe operator after jq expression provided via variable.

        When jq expression is passed as a variable (not string literal),
        subsequent string arguments should still be checked for shell operators.
        """
        code = """
import subprocess
jq_expr = '.[] | select(.name)'
subprocess.run([
    "gh", "api", "--jq", jq_expr, "|", "grep", "test"
])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        # Should detect the pipe operator AFTER the variable
        assert len(visitor.issues) == 1
        assert visitor.issues[0]["type"] == "shell_operator_in_list"
        assert "|" in visitor.issues[0]["message"]

    def test_allow_git_format_with_multiple_pipes(self):
        """Should NOT flag multiple pipes in git --format strings."""
        code = """
import subprocess
subprocess.run([
    "git", "log", "--format=%H|%an|%ae|%s", "HEAD~5..HEAD"
])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 0

    def test_allow_git_format_separate_argument(self):
        """Should NOT flag pipe when --format and value are separate args (Codex review)."""
        code = """
import subprocess
subprocess.run([
    "git", "log", "--format", "%H|%s", "main"
])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 0

    def test_detect_pipe_after_format_argument(self):
        """Should detect shell pipe AFTER a --format argument."""
        code = """
import subprocess
subprocess.run([
    "git", "log", "--format=%s", "|", "grep", "fix"
])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        # Should detect the standalone pipe operator
        assert len(visitor.issues) == 1
        assert visitor.issues[0]["type"] == "shell_operator_in_list"
        assert "|" in visitor.issues[0]["message"]

    def test_allow_git_pretty_alias(self):
        """Should NOT flag pipe in git --pretty strings (Copilot review).

        --pretty is an alias for --format in git.
        """
        code = """
import subprocess
subprocess.run([
    "git", "log", "--pretty=%H|%s", "main"
])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 0

    def test_allow_git_pretty_separate_argument(self):
        """Should NOT flag pipe when --pretty and value are separate args."""
        code = """
import subprocess
subprocess.run([
    "git", "log", "--pretty", "%H|%an|%s", "main"
])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 0

    def test_allow_capture_output(self):
        """Should allow proper stderr handling with capture_output."""
        code = """
import subprocess
subprocess.run(["git", "status"], capture_output=True, text=True)
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 0

    def test_multiple_issues(self):
        """Should detect multiple issues in one file."""
        code = """
import subprocess
subprocess.run("ls", shell=True)
subprocess.run(["cmd", "|", "other"])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 2

    def test_subprocess_call(self):
        """Should also check subprocess.call."""
        code = """
import subprocess
subprocess.call("ls", shell=True)
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 1
        assert visitor.issues[0]["type"] == "shell_true"

    def test_subprocess_popen(self):
        """Should also check subprocess.Popen."""
        code = """
import subprocess
subprocess.Popen("ls", shell=True)
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 1
        assert visitor.issues[0]["type"] == "shell_true"

    def test_from_subprocess_import_run(self):
        """Should detect from subprocess import run pattern."""
        code = """
from subprocess import run
run("ls", shell=True)
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 1
        assert visitor.issues[0]["type"] == "shell_true"

    def test_from_subprocess_import_run_with_list_operators(self):
        """Should detect shell operators in from subprocess import run pattern."""
        code = """
from subprocess import run
run(["cmd", "|", "other"])
"""
        tree = ast.parse(code)
        visitor = SubprocessVisitor("test.py")
        visitor.visit(tree)

        assert len(visitor.issues) == 1
        assert visitor.issues[0]["type"] == "shell_operator_in_list"


class TestAnalyzeFile:
    """Tests for analyze_file function."""

    def test_analyze_valid_file(self):
        """Should return no issues for valid code."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('import subprocess\nsubprocess.run(["ls"])\n')
            f.flush()
            try:
                issues = analyze_file(f.name)
                assert len(issues) == 0
            finally:
                Path(f.name).unlink(missing_ok=True)

    def test_analyze_file_with_issues(self):
        """Should return issues for problematic code."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('import subprocess\nsubprocess.run("ls", shell=True)\n')
            f.flush()
            try:
                issues = analyze_file(f.name)
                assert len(issues) == 1
            finally:
                Path(f.name).unlink(missing_ok=True)

    def test_analyze_nonexistent_file(self):
        """Should handle nonexistent file gracefully."""
        issues = analyze_file("/nonexistent/path/file.py")
        assert len(issues) == 1
        assert issues[0]["type"] == "error"

    def test_analyze_syntax_error(self):
        """Should handle syntax errors gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def broken(\n")  # Syntax error
            f.flush()
            try:
                issues = analyze_file(f.name)
                assert len(issues) == 1
                assert issues[0]["type"] == "syntax_error"
            finally:
                Path(f.name).unlink(missing_ok=True)


class TestHookIntegration:
    """Integration tests for the hook."""

    def test_hook_approves_non_commit(self):
        """Hook should approve non-commit commands."""
        hook_path = Path(__file__).parent.parent / "subprocess_lint_check.py"
        hooks_dir = Path(__file__).parent.parent
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        }

        env = subprocess.os.environ.copy()
        env["_TEST_NO_STAGED_FILES"] = "1"
        env["PYTHONPATH"] = str(hooks_dir)

        result = subprocess.run(
            ["python3", str(hook_path)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            env=env,
            cwd=str(hooks_dir),
        )

        output = json.loads(result.stdout)
        assert output["decision"] == "approve"

    def test_hook_approves_commit_with_no_staged_files(self):
        """Hook should approve commit when no Python files staged."""
        hook_path = Path(__file__).parent.parent / "subprocess_lint_check.py"
        hooks_dir = Path(__file__).parent.parent
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'test'"},
        }

        env = subprocess.os.environ.copy()
        env["_TEST_NO_STAGED_FILES"] = "1"
        env["PYTHONPATH"] = str(hooks_dir)

        result = subprocess.run(
            ["python3", str(hook_path)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            env=env,
            cwd=str(hooks_dir),
        )

        output = json.loads(result.stdout)
        assert output["decision"] == "approve"
