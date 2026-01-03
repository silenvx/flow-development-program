#!/usr/bin/env python3
"""フック専用のカスタムLintルールを適用する。

Why:
    フック実装の一貫性を保証し、よくあるミスや
    アンチパターンを検出するため。

What:
    - check_parse_hook_input(): json.loads(stdin)の代わりにparse_hook_input()使用を強制
    - check_log_hook_execution(): 引数数を検証
    - check_make_block_result(): 引数数を検証
    - check_except_pass(): except-passブロックにコメント必須
    - check_hardcoded_paths(): /tmp等のハードコードを検出

Remarks:
    - --check-only でサマリーのみ表示
    - ファイル指定なしで全フックをチェック

Changelog:
    - silenvx/dekita#1200: フック専用Lint機能を追加
"""

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path
from typing import NamedTuple


class LintError(NamedTuple):
    """Represents a lint error."""

    file: str
    line: int
    code: str
    message: str


def get_comment_lines(source: str) -> set[int]:
    """Get line numbers that have REAL comments (not # in strings).

    Uses Python's tokenize module to accurately detect comments,
    avoiding false positives from # characters inside string literals.
    """
    comment_lines: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                comment_lines.add(tok.start[0])  # line number (1-based)
    except tokenize.TokenizeError:
        pass  # Best effort: if tokenization fails, return empty set
    return comment_lines


def get_docstring_lines(tree: ast.AST) -> set[int]:
    """Get line numbers of docstrings.

    Returns all line numbers that are part of docstrings in modules,
    classes, functions, and async functions.
    """
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body:
                first_stmt = node.body[0]
                if isinstance(first_stmt, ast.Expr) and isinstance(first_stmt.value, ast.Constant):
                    if isinstance(first_stmt.value.value, str):
                        # Add all lines of the docstring
                        if first_stmt.end_lineno is not None:
                            for line in range(first_stmt.lineno, first_stmt.end_lineno + 1):
                                docstring_lines.add(line)
    return docstring_lines


def check_parse_hook_input(tree: ast.AST, filepath: str) -> list[LintError]:
    """Check that hooks use parse_hook_input() instead of json.loads(sys.stdin.read())."""
    errors = []

    for node in ast.walk(tree):
        # Look for json.loads(sys.stdin.read())
        if isinstance(node, ast.Call):
            # Check if it's json.loads
            if isinstance(node.func, ast.Attribute):
                if (
                    node.func.attr == "loads"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "json"
                ):
                    # Check if argument is sys.stdin.read()
                    if node.args:
                        arg = node.args[0]
                        if isinstance(arg, ast.Call):
                            if isinstance(arg.func, ast.Attribute):
                                if (
                                    arg.func.attr == "read"
                                    and isinstance(arg.func.value, ast.Attribute)
                                    and arg.func.value.attr == "stdin"
                                    and isinstance(arg.func.value.value, ast.Name)
                                    and arg.func.value.value.id == "sys"
                                ):
                                    errors.append(
                                        LintError(
                                            file=filepath,
                                            line=node.lineno,
                                            code="HOOK001",
                                            message="Use parse_hook_input() instead of json.loads(sys.stdin.read()). "
                                            "parse_hook_input() handles errors gracefully and sets session_id.",
                                        )
                                    )

    return errors


def check_log_hook_execution(tree: ast.AST, filepath: str) -> list[LintError]:
    """Check that log_hook_execution has correct number of arguments (2-5)."""
    errors = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check if it's log_hook_execution
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name == "log_hook_execution":
                # Count positional args + keyword args
                num_args = len(node.args) + len(node.keywords)

                if num_args < 2:
                    errors.append(
                        LintError(
                            file=filepath,
                            line=node.lineno,
                            code="HOOK002",
                            message=f"log_hook_execution requires at least 2 arguments (hook_name, decision), got {num_args}. "
                            "Signature: log_hook_execution(hook_name, decision, reason=None, details=None, duration_ms=None)",
                        )
                    )
                elif num_args > 5:
                    errors.append(
                        LintError(
                            file=filepath,
                            line=node.lineno,
                            code="HOOK002",
                            message=f"log_hook_execution accepts at most 5 arguments, got {num_args}. "
                            "Signature: log_hook_execution(hook_name, decision, reason=None, details=None, duration_ms=None)",
                        )
                    )

    return errors


def check_make_block_result(tree: ast.AST, filepath: str) -> list[LintError]:
    """Check that make_block_result has 2-3 arguments.

    Issue #2456: HookContext DI移行により、オプショナルなctx引数を追加。
    Signature: make_block_result(hook_name, reason, ctx=None)
    """
    errors = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check if it's make_block_result
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name == "make_block_result":
                num_args = len(node.args) + len(node.keywords)

                if num_args < 2:
                    errors.append(
                        LintError(
                            file=filepath,
                            line=node.lineno,
                            code="HOOK003",
                            message=f"make_block_result requires at least 2 arguments (hook_name, reason), got {num_args}. "
                            "Signature: make_block_result(hook_name, reason, ctx=None)",
                        )
                    )
                elif num_args > 3:
                    errors.append(
                        LintError(
                            file=filepath,
                            line=node.lineno,
                            code="HOOK003",
                            message=f"make_block_result accepts at most 3 arguments (hook_name, reason, ctx), got {num_args}. "
                            "Signature: make_block_result(hook_name, reason, ctx=None)",
                        )
                    )

    return errors


def check_except_pass_comment(tree: ast.AST, source: str, filepath: str) -> list[LintError]:
    """Check that except-pass blocks have explanatory comments.

    Detects patterns like:
        except SomeError:
            pass  # <- needs a comment explaining why

    Valid patterns:
        except SomeError:  # Best effort, ignore errors
            pass

        except SomeError:
            pass  # Intentionally ignored

        except SomeError:
            # This error is expected when ...
            pass

    Note: Uses tokenize module to detect real comments, avoiding false positives
    from # characters inside string literals (e.g., `x = "val#123"`).
    """
    errors = []
    # Use tokenize to get accurate comment line numbers
    comment_lines = get_comment_lines(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Check if the except body is just a single 'pass' statement
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                pass_node = node.body[0]
                pass_line = pass_node.lineno
                except_line = node.lineno

                # Check for comments on:
                # 1. The except line (inline comment)
                # 2. The pass line (inline comment)
                # 3. Any line between except and pass
                has_comment = False

                # Check if any relevant line has a real comment
                for line_num in range(except_line, pass_line + 1):
                    if line_num in comment_lines:
                        has_comment = True
                        break

                if not has_comment:
                    exception_type = "Exception"
                    if node.type:
                        if isinstance(node.type, ast.Name):
                            exception_type = node.type.id
                        elif isinstance(node.type, ast.Tuple):
                            types = []
                            for elt in node.type.elts:
                                if isinstance(elt, ast.Name):
                                    types.append(elt.id)
                            exception_type = ", ".join(types) if types else "Exception"

                    errors.append(
                        LintError(
                            file=filepath,
                            line=pass_line,
                            code="HOOK004",
                            message=f"except {exception_type}: pass requires an explanatory comment. "
                            "Add a comment explaining why the exception is intentionally ignored.",
                        )
                    )

    return errors


def check_hardcoded_tmp_path(tree: ast.AST, source: str, filepath: str) -> list[LintError]:
    """Check for hardcoded /tmp paths that should use tempfile.gettempdir().

    Detects patterns like:
        Path("/tmp")
        "/tmp/foo"
        os.environ.get("TMPDIR", "/tmp")

    These patterns are not cross-platform and should use:
        tempfile.gettempdir()
        Path(tempfile.gettempdir())

    Note: Skips docstrings to avoid false positives from documentation
    that mentions /tmp paths (e.g., usage examples in docstrings).
    """
    errors = []
    # Pattern to match /tmp paths (but not paths like /templates or /temporary)
    tmp_pattern = re.compile(r'"/tmp(?:/|")')
    # Get docstring lines to skip
    docstring_lines = get_docstring_lines(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Skip if this string is part of a docstring
            if node.lineno in docstring_lines:
                continue
            if tmp_pattern.search(f'"{node.value}"'):
                errors.append(
                    LintError(
                        file=filepath,
                        line=node.lineno,
                        code="HOOK005",
                        message=f'Hardcoded "/tmp" path detected: "{node.value}". '
                        "Use tempfile.gettempdir() for cross-platform compatibility.",
                    )
                )

    return errors


def check_log_hook_execution_requires_parse_hook_input(
    tree: ast.AST, filepath: str
) -> list[LintError]:
    """Check that hooks using log_hook_execution also call parse_hook_input first.

    Hooks that use log_hook_execution() without calling parse_hook_input() will
    have session_id logged in the fallback ppid-XXXX format instead of UUID format.

    This is because:
    - log_hook_execution() uses get_claude_session_id() to get session_id
    - get_claude_session_id() returns UUID if set via set_hook_session_id()
    - parse_hook_input() calls set_hook_session_id() with the session_id from stdin
    - Without parse_hook_input(), get_claude_session_id() falls back to ppid-XXXX

    Also checks call order within main(): parse_hook_input() must be called before
    log_hook_execution(). Order checking is only done within the main() function
    to avoid false positives from helper functions that may be defined before main().
    """
    errors = []

    # First, check if any log_hook_execution exists in the file
    has_log_hook_execution = False
    has_parse_hook_input = False
    log_hook_execution_line = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name == "log_hook_execution":
                has_log_hook_execution = True
                if log_hook_execution_line == 0:
                    log_hook_execution_line = node.lineno
            elif func_name == "parse_hook_input":
                has_parse_hook_input = True

    # Check call order within main() function only
    main_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break

    if main_func is None:
        # No main() function - this is a module meant to be imported.
        # Skip HOOK006 entirely since the importing file should ensure
        # parse_hook_input() is called before using any functions that log.
        return errors

    # If log_hook_execution is used but parse_hook_input is not called anywhere
    if has_log_hook_execution and not has_parse_hook_input:
        errors.append(
            LintError(
                file=filepath,
                line=log_hook_execution_line,
                code="HOOK006",
                message="log_hook_execution() requires parse_hook_input() to be called first. "
                "Without parse_hook_input(), session_id will fallback to ppid-XXXX format.",
            )
        )
        return errors

    # Find first parse_hook_input and log_hook_execution in main()
    parse_hook_input_line_in_main = 0
    log_hook_execution_line_in_main = 0

    for node in ast.walk(main_func):
        if isinstance(node, ast.Call):
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name == "parse_hook_input" and parse_hook_input_line_in_main == 0:
                parse_hook_input_line_in_main = node.lineno
            elif func_name == "log_hook_execution" and log_hook_execution_line_in_main == 0:
                log_hook_execution_line_in_main = node.lineno

    # Check order only if both are called in main()
    if parse_hook_input_line_in_main > 0 and log_hook_execution_line_in_main > 0:
        if parse_hook_input_line_in_main > log_hook_execution_line_in_main:
            errors.append(
                LintError(
                    file=filepath,
                    line=log_hook_execution_line_in_main,
                    code="HOOK006",
                    message="parse_hook_input() must be called before log_hook_execution() in main(). "
                    f"Found parse_hook_input() at line {parse_hook_input_line_in_main}, "
                    f"but log_hook_execution() at line {log_hook_execution_line_in_main}.",
                )
            )

    return errors


def lint_file(filepath: Path) -> list[LintError]:
    """Lint a single hook file."""
    try:
        with open(filepath) as f:
            source = f.read()
    except OSError as e:
        return [
            LintError(
                file=str(filepath),
                line=0,
                code="HOOK000",
                message=f"Failed to read file: {e}",
            )
        ]

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        return [
            LintError(
                file=str(filepath),
                line=e.lineno or 0,
                code="HOOK000",
                message=f"Syntax error: {e.msg}",
            )
        ]

    errors = []
    errors.extend(check_parse_hook_input(tree, str(filepath)))
    errors.extend(check_log_hook_execution(tree, str(filepath)))
    errors.extend(check_make_block_result(tree, str(filepath)))
    errors.extend(check_except_pass_comment(tree, source, str(filepath)))
    errors.extend(check_hardcoded_tmp_path(tree, source, str(filepath)))
    errors.extend(check_log_hook_execution_requires_parse_hook_input(tree, str(filepath)))

    return errors


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Custom lint rules for Claude Code hooks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Files to check (default: .claude/hooks/**/*.py)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Show summary only without detailed errors (useful for assessing impact of new rules)",
    )
    return parser.parse_args()


def print_summary(errors: list[LintError], num_files: int) -> None:
    """Print a summary of errors by code."""
    if not errors:
        print(f"No violations found in {num_files} file(s)")
        return

    # Count errors by code
    code_counts: dict[str, int] = {}
    for error in errors:
        code_counts[error.code] = code_counts.get(error.code, 0) + 1

    # Format: "45 violations found (HOOK004: 34, HOOK005: 11)"
    breakdown = ", ".join(f"{code}: {count}" for code, count in sorted(code_counts.items()))
    print(f"{len(errors)} violations found ({breakdown})")


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Get files to check
    if args.files:
        files = [Path(f) for f in args.files]
    else:
        # Default: check all hook files (recursive to match lefthook glob pattern)
        hooks_dir = Path(".claude/hooks")
        if not hooks_dir.exists():
            print("No .claude/hooks directory found", file=sys.stderr)
            return 1
        files = list(hooks_dir.glob("**/*.py"))

    # Skip test files and files that define the functions we're checking usage of
    # - common.py: re-exports hook utilities
    # - lib/session.py: defines parse_hook_input() implementation
    files = [
        f
        for f in files
        if "/tests/" not in str(f)
        and not f.name.startswith("test_")
        and f.name != "common.py"
        and str(f) != ".claude/hooks/lib/session.py"
    ]

    all_errors: list[LintError] = []

    for filepath in sorted(files):
        errors = lint_file(filepath)
        all_errors.extend(errors)

    # Output based on mode
    if args.check_only:
        print_summary(all_errors, len(files))
    else:
        # Print detailed errors
        for error in all_errors:
            print(f"{error.file}:{error.line}: [{error.code}] {error.message}")

        if all_errors:
            print(f"\nFound {len(all_errors)} error(s)", file=sys.stderr)
        else:
            print(f"Checked {len(files)} file(s), no errors found")

    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
