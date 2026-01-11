#!/usr/bin/env python3
"""gh pr create時に変更されたフックのテストカバレッジを確認。

Why:
    フック変更時にテストがないとエッジケースの見落としが発生する。
    PR作成前にテスト不足を警告し、品質維持を促す。

What:
    - gh pr create コマンドを検出
    - mainブランチとの差分から変更ファイルを取得
    - .claude/hooks/*.py に対応する tests/test_{name}.py の存在確認
    - テストがないフックについて警告

Remarks:
    - 非ブロック型（警告のみ、fail-open設計）
    - __init__.py, common.py はスキップ

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings

# Directory containing hooks (relative to project root)
HOOKS_DIR = ".claude/hooks"
TESTS_DIR = ".claude/hooks/tests"


def is_gh_pr_create_command(command: str) -> bool:
    """Check if command is a gh pr create command."""
    if not command.strip():
        return False

    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"gh\s+pr\s+create\b", stripped_command))


def get_changed_files(base_branch: str = "main") -> list[str]:
    """Get list of changed files compared to base branch."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_branch],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except (subprocess.TimeoutExpired, OSError) as e:
        # fail-openポリシー: エラー時も処理継続し、呼び出し側には空リストを返す
        print(f"[pr-test-coverage-check] Failed to get changed files: {e}", file=sys.stderr)
    return []


def get_hook_files_without_tests(changed_files: list[str]) -> list[tuple[str, str]]:
    """Find hook files that don't have corresponding test files.

    Args:
        changed_files: List of changed file paths

    Returns:
        List of tuples (hook_file, expected_test_file) for missing tests
    """
    missing_tests = []
    project_root = os.environ.get("CLAUDE_PROJECT_DIR", "")

    for file_path in changed_files:
        # Only check .claude/hooks/*.py files
        if not file_path.startswith(HOOKS_DIR):
            continue
        if not file_path.endswith(".py"):
            continue
        # Skip test files themselves
        if "/tests/" in file_path or file_path.startswith(f"{HOOKS_DIR}/tests/"):
            continue
        # Skip __init__.py and common.py (utility files)
        filename = Path(file_path).name
        if filename in ("__init__.py", "common.py"):
            continue

        # Check for corresponding test file
        # e.g., .claude/hooks/my_hook.py -> .claude/hooks/tests/test_my_hook.py
        stem = Path(file_path).stem.replace("-", "_")
        expected_test = f"{TESTS_DIR}/test_{stem}.py"

        # Check if test file exists
        if project_root:
            full_test_path = Path(project_root) / expected_test
        else:
            full_test_path = Path(expected_test)

        if not full_test_path.exists():
            missing_tests.append((file_path, expected_test))

    return missing_tests


def format_warning_message(missing_tests: list[tuple[str, str]]) -> str:
    """Format warning message for missing tests."""
    lines = [
        "⚠️ **テストファイル不足の警告**",
        "",
        "以下のhookファイルに対応するテストが見つかりません:",
        "",
    ]

    for hook_file, expected_test in missing_tests:
        lines.append(f"  - `{hook_file}`")
        lines.append(f"    → 期待されるテスト: `{expected_test}`")

    lines.extend(
        [
            "",
            "テストを追加することで、エッジケースの見落としを防げます。",
            "（この警告はブロックしません）",
        ]
    )

    return "\n".join(lines)


def main():
    """PreToolUse hook for Bash commands."""
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        command = data.get("tool_input", {}).get("command", "")

        if is_gh_pr_create_command(command):
            changed_files = get_changed_files()
            missing_tests = get_hook_files_without_tests(changed_files)

            if missing_tests:
                result["systemMessage"] = format_warning_message(missing_tests)

    except Exception as e:
        # Don't block on errors - fail-open design
        print(f"[pr-test-coverage-check] Error: {e}", file=sys.stderr)

    log_hook_execution(
        "pr-test-coverage-check",
        result.get("decision", "approve"),
        result.get("reason"),
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
