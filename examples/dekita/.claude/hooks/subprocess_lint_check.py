#!/usr/bin/env python3
"""Pythonフック内の問題のあるsubprocess使用パターンを検出。

Why:
    shell=Trueはセキュリティリスク、リスト引数でのシェル演算子は動作しない。
    コミット前にこれらの問題を検出して修正を強制する。

What:
    - git commit時（PreToolUse:Bash）に発火
    - .claude/hooks/配下のステージ済みPythonファイルを解析
    - shell=True使用、リスト引数内のシェル演算子を検出
    - 問題がある場合はコミットをブロック

Remarks:
    - ブロック型フック（問題検出時はコミットをブロック）
    - AST解析でsubprocess.run/call/Popenを検出
    - --jq引数やgit --format内の|はスキップ（Issue #1226）

Changelog:
    - silenvx/dekita#1110: フック追加
    - silenvx/dekita#1226: git --format内の|をスキップ
"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input
from lib.strings import split_command_chain, strip_quoted_strings

# Shell operators that don't work with shell=False (list arguments)
# Ordered from most specific to least specific to avoid duplicate detection
SHELL_OPERATORS = ["2>&1", "2>", ">&", ">>", "&&", "||", ">", "<", "|"]

# Bare function names that indicate subprocess usage when imported directly
SUBPROCESS_BARE_NAMES = {"run", "call", "Popen"}


def is_git_commit_command(command: str) -> bool:
    """Check if command contains git commit."""
    stripped = strip_quoted_strings(command)
    subcommands = split_command_chain(stripped)
    for subcmd in subcommands:
        if re.search(r"^git\s+commit(\s|$)", subcmd):
            return True
    return False


def get_staged_python_files() -> list[str]:
    """Get list of staged Python files in .claude/hooks/."""
    if os.environ.get("_TEST_NO_STAGED_FILES") == "1":
        return []

    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []
        files = result.stdout.strip().split("\n") if result.stdout.strip() else []
        # Only check hooks directory Python files
        return [f for f in files if f.endswith(".py") and f.startswith(".claude/hooks/")]
    except Exception:
        return []


class SubprocessVisitor(ast.NodeVisitor):
    """AST visitor to detect problematic subprocess usage."""

    def __init__(self, filename: str):
        self.filename = filename
        self.issues: list[dict] = []

    def visit_Call(self, node: ast.Call) -> None:
        """Visit function call nodes."""
        # Check if this is a subprocess call
        func_name = self._get_func_name(node)
        if not func_name:
            self.generic_visit(node)
            return

        # Check subprocess.run, subprocess.call, subprocess.Popen
        # Also check bare names (run, call, Popen) when imported directly
        is_subprocess_call = (
            func_name
            in (
                "subprocess.run",
                "subprocess.call",
                "subprocess.Popen",
            )
            or func_name in SUBPROCESS_BARE_NAMES
        )
        if not is_subprocess_call:
            self.generic_visit(node)
            return

        # Check for shell=True
        for keyword in node.keywords:
            if keyword.arg == "shell":
                if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    self.issues.append(
                        {
                            "file": self.filename,
                            "line": node.lineno,
                            "type": "shell_true",
                            "message": f"subprocess with shell=True detected at line {node.lineno}. "
                            "Use list arguments with shell=False instead for security.",
                        }
                    )

        # Check first argument (command) for shell operators in list
        if node.args:
            first_arg = node.args[0]
            shell_ops = self._check_list_for_shell_operators(first_arg)
            if shell_ops:
                self.issues.append(
                    {
                        "file": self.filename,
                        "line": node.lineno,
                        "type": "shell_operator_in_list",
                        "message": f"Shell operator(s) {shell_ops} in list argument at line {node.lineno}. "
                        "Shell operators don't work with list arguments (shell=False). "
                        "Use capture_output=True and stderr=subprocess.STDOUT for stderr handling.",
                    }
                )

        self.generic_visit(node)

    def _get_func_name(self, node: ast.Call) -> str | None:
        """Get function name from call node."""
        if isinstance(node.func, ast.Attribute):
            # subprocess.run(...)
            if isinstance(node.func.value, ast.Name):
                return f"{node.func.value.id}.{node.func.attr}"
        elif isinstance(node.func, ast.Name):
            # run(...) after from subprocess import run
            return node.func.id
        return None

    def _check_list_for_shell_operators(self, node: ast.AST) -> list[str]:
        """Check if a list node contains shell operators.

        SHELL_OPERATORS is ordered from most specific to least specific.
        Once an operator is found in a string, we mark the matched portion
        to avoid duplicate detection (e.g., "2>&1" matching both "2>&1" and ">&").

        Note: Arguments following --jq are skipped because jq expressions use |
        as an internal pipe operator, not a shell pipe.

        Note: Arguments starting with --format= are skipped because git uses |
        as a format separator (Issue #1226).
        """
        found_ops: list[str] = []

        if isinstance(node, ast.List):
            prev_value = ""
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    value = elt.value

                    # Skip jq expressions - jq uses | as its own pipe operator
                    if prev_value == "--jq":
                        prev_value = value
                        continue

                    # Issue #1226: Skip git format strings - | is used as separator
                    # Handles --format=VALUE, --format VALUE, --pretty=VALUE, --pretty VALUE
                    if value.startswith("--format=") or value.startswith("--pretty="):
                        prev_value = value
                        continue
                    if prev_value in ("--format", "--pretty"):
                        prev_value = value
                        continue

                    prev_value = value

                    for op in SHELL_OPERATORS:
                        if op in value:
                            # Check if this operator is already covered by a more specific one
                            is_covered = any(
                                op in found_op and op != found_op for found_op in found_ops
                            )
                            if not is_covered:
                                found_ops.append(op)
                            # Remove the matched portion to avoid duplicate detection
                            value = value.replace(op, "", 1)
                else:
                    # Reset prev_value for non-string elements (e.g., variables)
                    # to avoid incorrectly skipping subsequent string elements
                    prev_value = ""

        return list(set(found_ops))


def analyze_file(filepath: str) -> list[dict]:
    """Analyze a Python file for subprocess issues."""
    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
        visitor = SubprocessVisitor(filepath)
        visitor.visit(tree)
        return visitor.issues
    except SyntaxError as e:
        return [
            {
                "file": filepath,
                "line": e.lineno or 0,
                "type": "syntax_error",
                "message": f"Syntax error: {e.msg}",
            }
        ]
    except Exception as e:
        return [
            {
                "file": filepath,
                "line": 0,
                "type": "error",
                "message": f"Could not analyze file: {e}",
            }
        ]


def main():
    """PreToolUse hook for Bash commands."""
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check git commit commands
        if not is_git_commit_command(command):
            print(json.dumps({"decision": "approve"}))
            sys.exit(0)

        # Get staged Python files in hooks directory
        py_files = get_staged_python_files()
        if not py_files:
            print(json.dumps({"decision": "approve"}))
            sys.exit(0)

        # Analyze each file
        all_issues: list[dict] = []
        for filepath in py_files:
            if Path(filepath).exists():
                issues = analyze_file(filepath)
                all_issues.extend(issues)

        if all_issues:
            # Format error message
            error_lines = ["subprocessの使用に問題があります:\n"]
            for issue in all_issues:
                error_lines.append(f"  - {issue['file']}:{issue['line']}: {issue['message']}")

            error_lines.append("\n修正方法:")
            error_lines.append("  - shell=True は使用しない（セキュリティリスク）")
            error_lines.append("  - コマンドはリスト形式で指定: ['git', 'status']")
            error_lines.append(
                "  - stderr処理は capture_output=True または stderr=subprocess.STDOUT を使用"
            )
            error_lines.append(
                "  - パイプは複数のsubprocess.runで実現するか、shell=Trueを避ける設計に変更"
            )

            reason = "\n".join(error_lines)
            result = make_block_result("subprocess-lint-check", reason)
            log_hook_execution("subprocess-lint-check", "block", reason)
            print(json.dumps(result))
            sys.exit(0)

        # All checks passed
        result = {
            "decision": "approve",
            "systemMessage": f"✅ subprocess-lint-check: {len(py_files)}個のファイルをチェックOK",
        }

    except Exception as e:
        print(f"[subprocess-lint-check] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve", "reason": f"Hook error: {e}"}

    log_hook_execution(
        "subprocess-lint-check", result.get("decision", "approve"), result.get("reason")
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
