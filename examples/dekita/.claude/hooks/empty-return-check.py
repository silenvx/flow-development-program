#!/usr/bin/env python3
"""except内での空コレクション返却アンチパターンを検出する。

Why:
    例外発生時に空リストを返すと、「成功したが結果が空」と「失敗」の
    区別がつかなくなる。呼び出し側が失敗を「データなし」と誤解し、
    不正な処理を続行するバグの原因となる。

What:
    - Python ファイルの Write/Edit を検出
    - AST解析で except ブロック内の `return []` や `return {}` を検出
    - 検出時は None を返すことを推奨する警告を表示

Remarks:
    - テストファイルは意図的なパターンの可能性があるため除外
    - ブロックはせず警告のみ（P2レベル）

Changelog:
    - silenvx/dekita#????: P2バグ再発防止の仕組み化
"""

import ast
import json
import sys
from pathlib import Path

from lib.execution import log_hook_execution
from lib.session import parse_hook_input


class EmptyReturnInExceptChecker(ast.NodeVisitor):
    """AST visitor to find empty collection returns inside except handlers."""

    def __init__(self):
        self.issues: list[dict] = []
        self.current_file = ""

    def check_file(self, file_path: str, content: str) -> list[dict]:
        """Check a single file for the antipattern."""
        self.issues = []
        self.current_file = file_path
        try:
            tree = ast.parse(content)
            self.visit(tree)
        except SyntaxError:
            pass  # Skip files with syntax errors
        return self.issues

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        """Visit except handler and check for empty returns."""
        for return_node in self._find_direct_returns(node.body):
            if self._is_empty_collection(return_node.value):
                self.issues.append(
                    {
                        "file": self.current_file,
                        "line": return_node.lineno,
                        "message": (
                            "Empty collection return in except block. "
                            "Consider returning None to distinguish failure from empty data."
                        ),
                    }
                )
        self.generic_visit(node)

    def _find_direct_returns(self, nodes: list) -> list[ast.Return]:
        """Find return statements, excluding nested try-except handlers.

        Walks through statements but skips nested try blocks' except handlers
        (they will be visited separately by visit_ExceptHandler).
        Also skips function/class definitions as their returns are not part
        of the enclosing except handler.

        For try statements: skips body and handlers (nested exceptions),
        but checks orelse (else) and finalbody (finally) as those run after
        the try body succeeds or regardless of exception.

        For match statements (Python 3.10+): checks all case bodies.
        """
        returns = []
        # Skip nodes that define new scopes (their returns are not part of enclosing except)
        scope_nodes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        for node in nodes:
            if isinstance(node, ast.Return) and node.value is not None:
                returns.append(node)
            elif isinstance(node, scope_nodes):
                # Skip scope-defining nodes entirely
                pass
            elif isinstance(node, ast.Try):
                # For nested try: skip body and handlers (visited by visit_ExceptHandler)
                # but check orelse (try-else) and finalbody (try-finally)
                if node.orelse:
                    returns.extend(self._find_direct_returns(node.orelse))
                if node.finalbody:
                    returns.extend(self._find_direct_returns(node.finalbody))
            elif hasattr(ast, "Match") and isinstance(node, ast.Match):
                # Handle match statement - hasattr check ensures Python < 3.10 compatibility
                for case in node.cases:
                    returns.extend(self._find_direct_returns(case.body))
            elif hasattr(node, "body"):
                returns.extend(self._find_direct_returns(node.body))
                if hasattr(node, "orelse"):
                    returns.extend(self._find_direct_returns(node.orelse))
        return returns

    def _is_empty_collection(self, node: ast.expr) -> bool:
        """Check if node is an empty list, dict, set, or tuple."""
        if isinstance(node, ast.List) and len(node.elts) == 0:
            return True
        if isinstance(node, ast.Dict) and len(node.keys) == 0:
            return True
        if isinstance(node, ast.Set) and len(node.elts) == 0:
            return True
        if isinstance(node, ast.Tuple) and len(node.elts) == 0:
            return True
        # Check for list(), dict(), set(), tuple() calls with no args
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in ("list", "dict", "set", "tuple"):
                    if len(node.args) == 0 and len(node.keywords) == 0:
                        return True
        return False


def main():
    """PreToolUse hook for Edit/Write commands.

    Checks Python files for empty collection returns in except blocks.
    """
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        # Only check Edit and Write operations
        if tool_name not in ("Edit", "Write"):
            log_hook_execution("empty-return-check", "approve", "Not Edit/Write")
            print(json.dumps(result))
            sys.exit(0)

        file_path = tool_input.get("file_path", "")

        # Only check Python files
        if not file_path.endswith(".py"):
            log_hook_execution("empty-return-check", "approve", "Not Python file")
            print(json.dumps(result))
            sys.exit(0)

        # Skip test files (the pattern may be intentional in tests)
        # Support both test_*.py (prefix) and *_test.py (suffix) patterns
        filename = Path(file_path).name
        if "/tests/" in file_path or filename.startswith("test_") or filename.endswith("_test.py"):
            log_hook_execution("empty-return-check", "approve", "Test file skipped")
            print(json.dumps(result))
            sys.exit(0)

        # Get the new content
        if tool_name == "Write":
            content = tool_input.get("content", "")
        else:  # Edit
            # For Edit, we need to read the file and apply the edit
            # to check the resulting content
            old_string = tool_input.get("old_string", "")
            new_string = tool_input.get("new_string", "")

            # Read current file content
            try:
                current_content = Path(file_path).read_text()
            except FileNotFoundError:
                log_hook_execution("empty-return-check", "approve", "File not found")
                print(json.dumps(result))
                sys.exit(0)

            # Apply the edit
            content = current_content.replace(old_string, new_string, 1)

        # Check for the antipattern
        checker = EmptyReturnInExceptChecker()
        issues = checker.check_file(file_path, content)

        if issues:
            warnings = []
            for issue in issues:
                warnings.append(f"⚠️ {issue['file']}:{issue['line']}: {issue['message']}")

            result["systemMessage"] = (
                "Empty collection return in except block detected:\n" + "\n".join(warnings) + "\n\n"
                "This pattern can cause bugs where failure is mistaken for empty data. "
                "Consider returning None instead to allow callers to distinguish."
            )
            log_hook_execution(
                "empty-return-check",
                "approve",
                f"Warning: {len(issues)} issue(s) found",
                {"file": file_path, "issues": len(issues)},
            )
        else:
            log_hook_execution("empty-return-check", "approve", None, {"file": file_path})

    except Exception as e:
        error_msg = f"Hook error: {e}"
        print(f"[empty-return-check] {error_msg}", file=sys.stderr)
        log_hook_execution("empty-return-check", "approve", error_msg)

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
