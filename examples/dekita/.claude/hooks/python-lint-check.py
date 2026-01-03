#!/usr/bin/env python3
"""git commit前にPythonコードのフォーマット・lintを自動修正。

Why:
    Pythonコードのスタイル違反でCIが失敗すると手戻りが発生する。
    コミット前に自動修正することでCI失敗を未然に防ぐ。

What:
    - git commit コマンドを検出
    - ステージングされたPythonファイルを取得
    - ruff format/checkでフォーマット・lint問題を自動修正
    - 修正後にファイルを再ステージング
    - 自動修正不可能な場合のみブロック

Remarks:
    - ブロック型フック（自動修正失敗時のみ）
    - fail-open設計（エラー時は許可）
    - worktree対応（絶対パス変換）

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1628: 自動修正機能を追加
    - silenvx/dekita#1712: 変更されたファイルのみ再ステージング
    - silenvx/dekita#2162: worktree対応（絶対パス変換）
"""

import json
import os
import re
import shlex
import subprocess
import sys

from lib.constants import TIMEOUT_HEAVY, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input
from lib.strings import split_command_chain, strip_quoted_strings


def get_git_toplevel() -> str:
    """Get the root directory of the current git worktree.

    Issue #2162: When working in a worktree, git diff returns paths relative
    to the worktree root. We need absolute paths for ruff to work correctly
    when the hook is executed from a different directory.

    Returns:
        The absolute path to the git worktree root, or CWD if detection fails.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        # Fail open: if git command fails (e.g., git not available, timeout),
        # fall back to CWD. This is safe because ruff will still run, just
        # might not find files if paths are incorrect.
        pass
    return os.getcwd()


def to_absolute_paths(files: list[str], git_root: str) -> list[str]:
    """Convert paths to absolute paths based on git root.

    Issue #2162: git diff returns paths relative to the git worktree root.
    When the hook runs in a different directory, ruff cannot find the files.
    Converting to absolute paths ensures ruff can access them.

    This function is also robust to being passed absolute paths: any path
    that is already absolute is returned unchanged.

    Args:
        files: List of file paths (typically relative to git_root).
        git_root: The git worktree root directory.

    Returns:
        List of absolute file paths.
    """
    return [f if os.path.isabs(f) else os.path.join(git_root, f) for f in files]


def is_git_commit_command(command: str) -> bool:
    """Check if command contains git commit.

    Handles command chains like:
    - git add && git commit -m "msg"
    - git status; git commit

    Also handles quoted strings to avoid false positives like:
    - echo "git commit"
    """
    stripped = strip_quoted_strings(command)
    subcommands = split_command_chain(stripped)
    for subcmd in subcommands:
        # split_command_chain already strips whitespace, so no need for subcmd.strip() or ^\s*
        if re.search(r"^git\s+commit(\s|$)", subcmd):
            return True
    return False


def get_staged_python_files() -> list[str]:
    """Get list of staged Python files.

    For testing purposes, set _TEST_NO_STAGED_FILES=1 to simulate no staged files.
    This is needed because integration tests run in environments where the actual
    git state may have staged files, causing test failures.
    """
    # Test mode: simulate no staged files
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
        return [f for f in files if f.endswith(".py")]
    except Exception:
        return []


def check_ruff_format(files: list[str]) -> tuple[bool, str]:
    """Check if files pass ruff format.

    Returns:
        Tuple of (passed, error_message).
    """
    if not files:
        return True, ""
    try:
        result = subprocess.run(
            ["uvx", "ruff", "format", "--check"] + files,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )
        if result.returncode != 0:
            return False, result.stdout + result.stderr
        return True, ""
    except Exception as e:
        # On error, don't block (fail open)
        return True, f"Warning: Could not run ruff format: {e}"


def restage_files(files: list[str]) -> tuple[bool, str]:
    """Re-stage files after auto-fix.

    Issue #1628: After auto-fix, re-stage files so commit uses fixed content.

    Returns:
        Tuple of (success, message).
    """
    if not files:
        return True, ""
    try:
        result = subprocess.run(
            ["git", "add"] + files,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stdout + result.stderr
    except Exception as e:
        return False, f"Could not restage files: {e}"


def auto_fix_ruff_format(files: list[str]) -> tuple[bool, str]:
    """Auto-fix formatting issues with ruff format.

    Issue #1628: Instead of blocking, auto-fix format issues.

    Returns:
        Tuple of (success, message).
    """
    if not files:
        return True, ""
    try:
        result = subprocess.run(
            ["uvx", "ruff", "format"] + files,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )
        if result.returncode == 0:
            return True, result.stdout + result.stderr
        return False, result.stdout + result.stderr
    except Exception as e:
        return False, f"Could not run ruff format: {e}"


def auto_fix_ruff_lint(files: list[str]) -> tuple[bool, str]:
    """Auto-fix linting issues with ruff check --fix.

    Issue #1628: Instead of blocking, auto-fix fixable lint issues.

    Returns:
        Tuple of (success, message).
    """
    if not files:
        return True, ""
    try:
        result = subprocess.run(
            ["uvx", "ruff", "check", "--fix"] + files,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )
        # ruff check --fix returns 0 if all issues were fixed or no issues found
        # Returns non-zero if unfixable issues remain
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, f"Could not run ruff check --fix: {e}"


def check_ruff_lint(files: list[str]) -> tuple[bool, str]:
    """Check if files pass ruff lint.

    Returns:
        Tuple of (passed, error_message).
    """
    if not files:
        return True, ""
    try:
        result = subprocess.run(
            ["uvx", "ruff", "check"] + files,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )
        if result.returncode != 0:
            return False, result.stdout + result.stderr
        return True, ""
    except Exception as e:
        # On error, don't block (fail open)
        return True, f"Warning: Could not run ruff check: {e}"


def get_changed_files(files: list[str]) -> list[str]:
    """Get files that have unstaged changes.

    Issue #1712: Only restage files that were actually modified by ruff.
    Uses git diff to detect which files have changes.

    Args:
        files: List of file paths to check.

    Returns:
        List of files that have unstaged changes.
    """
    if not files:
        return []
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--"] + files,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            # Fallback: return all files if git diff fails
            print(
                f"[python-lint-check] Warning: git diff failed with exit code {result.returncode}.",
                file=sys.stderr,
            )
            if result.stderr:
                print(f"[python-lint-check] stderr: {result.stderr.strip()}", file=sys.stderr)
            return files
        stdout = result.stdout.strip()
        if not stdout:
            return []
        changed = [f.strip() for f in stdout.split("\n") if f.strip()]
        return changed
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        # Fallback: return all files on error
        print(f"[python-lint-check] Warning: git diff command failed: {e}", file=sys.stderr)
        return files


def main():
    """
    PreToolUse hook for Bash commands.

    Checks Python formatting and linting before git commit.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check git commit commands (handles command chains like git add && git commit)
        if not is_git_commit_command(command):
            result = {"decision": "approve"}
            print(json.dumps(result))
            sys.exit(0)

        # Get staged Python files
        py_files = get_staged_python_files()
        if not py_files:
            result = {"decision": "approve"}
            print(json.dumps(result))
            sys.exit(0)

        # Issue #2162: Convert to absolute paths for worktree compatibility
        # git diff returns paths relative to the worktree root, but ruff needs
        # absolute paths when the hook runs from a different directory.
        git_root = get_git_toplevel()
        py_files = to_absolute_paths(py_files, git_root)

        # Issue #1628: Auto-fix format and lint issues instead of blocking
        auto_fixed_messages = []
        files_to_restage = []

        # Check formatting
        format_ok, format_err = check_ruff_format(py_files)
        if not format_ok:
            # Try auto-fix
            fix_ok, fix_msg = auto_fix_ruff_format(py_files)
            if fix_ok:
                auto_fixed_messages.append("フォーマットを自動修正")
                # Issue #1712: 実際に変更されたファイルのみ再ステージ
                # Issue #2162: git diffは相対パスを返すため、絶対パスに変換
                changed_files = get_changed_files(py_files)
                changed_files = to_absolute_paths(changed_files, git_root)
                files_to_restage.extend(changed_files)
                log_hook_execution(
                    "python-lint-check",
                    "approve",
                    f"Auto-fixed format issues: {fix_msg}",
                )
            else:
                # Auto-fix failed, block
                quoted_files = " ".join(shlex.quote(f) for f in py_files)
                reason = (
                    "Pythonファイルのフォーマット自動修正に失敗しました。\n"
                    "手動で以下を実行してください:\n\n"
                    f"uvx ruff format {quoted_files}\n\n"
                    "エラー詳細:\n" + fix_msg
                )
                result = make_block_result("python-lint-check", reason)
                log_hook_execution("python-lint-check", "block", reason)
                print(json.dumps(result))
                sys.exit(0)

        # Check linting and auto-fix
        lint_ok, lint_err = check_ruff_lint(py_files)
        if not lint_ok:
            # Try auto-fix
            fix_ok, fix_msg = auto_fix_ruff_lint(py_files)
            if fix_ok:
                auto_fixed_messages.append("lintエラーを自動修正")
                # Issue #1712: 実際に変更されたファイルのみ追加（重複なく統合）
                # Issue #2162: git diffは相対パスを返すため、絶対パスに変換
                changed_files = get_changed_files(py_files)
                changed_files = to_absolute_paths(changed_files, git_root)
                restage_set = set(files_to_restage)
                restage_set.update(changed_files)
                files_to_restage = list(restage_set)
                log_hook_execution(
                    "python-lint-check",
                    "approve",
                    f"Auto-fixed lint issues: {fix_msg}",
                )
            else:
                # Auto-fix couldn't fix all issues, block
                quoted_files = " ".join(shlex.quote(f) for f in py_files)
                reason = (
                    "Pythonファイルのlintエラーを自動修正できませんでした。\n"
                    "手動で以下を確認・修正してください:\n\n"
                    f"uvx ruff check {quoted_files}\n\n"
                    "エラー詳細:\n" + fix_msg
                )
                result = make_block_result("python-lint-check", reason)
                log_hook_execution("python-lint-check", "block", reason)
                print(json.dumps(result))
                sys.exit(0)

        # Re-stage files after auto-fix
        if files_to_restage:
            restage_ok, restage_err = restage_files(files_to_restage)
            if not restage_ok:
                # Re-staging failed, block
                quoted_files = " ".join(shlex.quote(f) for f in files_to_restage)
                reason = (
                    "自動修正後のファイル再ステージングに失敗しました。\n"
                    "手動で以下を実行してください:\n\n"
                    f"git add {quoted_files}\n\n"
                    "エラー詳細:\n" + restage_err
                )
                result = make_block_result("python-lint-check", reason)
                log_hook_execution("python-lint-check", "block", reason)
                print(json.dumps(result))
                sys.exit(0)

        # All checks passed (with or without auto-fix)
        if auto_fixed_messages:
            result = {
                "decision": "approve",
                "systemMessage": (
                    f"✅ python-lint-check: {len(py_files)}個のPythonファイル\n"
                    f"  🔧 {', '.join(auto_fixed_messages)}し、再ステージング完了"
                ),
            }
        else:
            result = {
                "decision": "approve",
                "systemMessage": f"✅ python-lint-check: {len(py_files)}個のPythonファイルをチェックOK",
            }

    except Exception as e:
        # On error, approve to avoid blocking
        print(f"[python-lint-check] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve", "reason": f"Hook error: {e}"}

    log_hook_execution("python-lint-check", result.get("decision", "approve"), result.get("reason"))
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
