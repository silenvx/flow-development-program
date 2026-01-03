#!/usr/bin/env python3
"""Pythonの関数シグネチャ変更時にテスト更新漏れを検出。

Why:
    関数の引数や戻り値の型を変更した場合、対応するテストも更新する必要がある。
    テスト更新漏れがあると、CI通過後に実際の動作で問題が発生する。

What:
    - git diff でPython関数シグネチャ（引数、戻り値）の変更を検出
    - 対応するテストファイル（test_xxx.py）がコミットに含まれているか確認
    - テストファイル更新がない場合に警告を表示
    - .claude/hooks/ と .claude/scripts/ 配下のファイルを対象

Remarks:
    - 非ブロック型（警告のみ、pushは許可）
    - pre-pushフックとして使用可能
    - ファイル名のハイフンはアンダースコアに変換してテストファイル名を推定

Changelog:
    - silenvx/dekita#1108: フック追加（Issue #1102の再発防止）
"""

import re
import subprocess
import sys
from pathlib import Path


def get_modified_python_files() -> list[str]:
    """Get list of Python files modified in this push."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        files = result.stdout.strip().split("\n")
        return [f for f in files if f.endswith(".py") and f]
    except subprocess.CalledProcessError:
        return []


def get_diff_for_file(filepath: str) -> str:
    """Get the diff for a specific file."""
    try:
        result = subprocess.run(
            ["git", "diff", "origin/main...HEAD", "--", filepath],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return ""


def extract_signature_changes(diff: str) -> list[dict]:
    """Extract function signature changes from a diff.

    Returns list of dicts with:
    - function_name: name of the changed function
    - change_type: 'args' or 'return' or 'both'
    - old_args: argument list before the change
    - new_args: argument list after the change
    - old_return: return type before the change (or None)
    - new_return: return type after the change (or None)
    """
    changes = []

    # Pattern for function definition lines (added or removed)
    # Matches: def function_name(args) -> return_type:
    func_pattern = re.compile(r"^[-+]\s*def\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*([^:]+))?\s*:")

    lines = diff.split("\n")

    # Track old and new signatures for comparison
    old_sigs: dict[str, tuple[str, str | None]] = {}  # name -> (args, return_type)
    new_sigs: dict[str, tuple[str, str | None]] = {}

    for line in lines:
        match = func_pattern.match(line)
        if match:
            prefix = line[0]
            func_name = match.group(1)
            args = match.group(2).strip()
            return_type = match.group(3).strip() if match.group(3) else None

            if prefix == "-":
                old_sigs[func_name] = (args, return_type)
            elif prefix == "+":
                new_sigs[func_name] = (args, return_type)

    # Find functions with signature changes
    for func_name in set(old_sigs.keys()) & set(new_sigs.keys()):
        old_args, old_return = old_sigs[func_name]
        new_args, new_return = new_sigs[func_name]

        change_type = None
        if old_args != new_args and old_return != new_return:
            change_type = "both"
        elif old_args != new_args:
            change_type = "args"
        elif old_return != new_return:
            change_type = "return"

        if change_type:
            changes.append(
                {
                    "function_name": func_name,
                    "change_type": change_type,
                    "old_args": old_args,
                    "new_args": new_args,
                    "old_return": old_return,
                    "new_return": new_return,
                }
            )

    return changes


def find_test_file(source_file: str) -> str | None:
    """Find the corresponding test file for a source file.

    Maps:
    - .claude/hooks/foo.py -> .claude/hooks/tests/test_foo.py
    - .claude/hooks/foo-bar.py -> .claude/hooks/tests/test_foo_bar.py
    - .claude/scripts/foo.py -> .claude/scripts/tests/test_foo.py
    """
    path = Path(source_file)

    # Skip if already a test file
    if path.name.startswith("test_"):
        return None

    # Normalize filename: convert hyphens to underscores for test file naming
    # Hook files like "active-worktree-check.py" have tests named "test_active_worktree_check.py"
    normalized_name = path.name.replace("-", "_")

    # Determine test file location
    if ".claude/hooks" in source_file:
        test_file = f".claude/hooks/tests/test_{normalized_name}"
    elif ".claude/scripts" in source_file:
        test_file = f".claude/scripts/tests/test_{normalized_name}"
    else:
        # For other files, assume tests/ directory at same level
        test_file = str(path.parent / "tests" / f"test_{normalized_name}")

    return test_file


def main() -> int:
    """Main entry point."""
    modified_files = get_modified_python_files()

    if not modified_files:
        return 0

    # Filter to only .claude/ files (hooks and scripts)
    claude_files = [f for f in modified_files if f.startswith(".claude/")]

    if not claude_files:
        return 0

    warnings: list[str] = []

    for filepath in claude_files:
        # Skip test files themselves (only check filename, not path)
        path = Path(filepath)
        if path.name.startswith("test_") or "/tests/" in filepath:
            continue

        diff = get_diff_for_file(filepath)
        changes = extract_signature_changes(diff)

        if not changes:
            continue

        test_file = find_test_file(filepath)
        if not test_file:
            continue

        # Check if test file is also modified
        if test_file not in modified_files:
            for change in changes:
                func_name = change["function_name"]
                change_type = change["change_type"]

                if change_type == "return":
                    detail = f"  戻り値: {change['old_return']} → {change['new_return']}"
                elif change_type == "args":
                    detail = f"  引数: {change['old_args']} → {change['new_args']}"
                else:
                    detail = (
                        f"  引数: {change['old_args']} → {change['new_args']}\n"
                        f"  戻り値: {change['old_return']} → {change['new_return']}"
                    )

                warnings.append(
                    f"⚠️  関数シグネチャ変更を検出:\n"
                    f"  ファイル: {filepath}\n"
                    f"  関数: {func_name}()\n"
                    f"{detail}\n"
                    f"  テストファイル: {test_file}\n"
                    f"  → テストファイルが更新されていません！"
                )

    if warnings:
        print("\n" + "=" * 60)
        print("🔍 関数シグネチャ変更チェック (Issue #1108)")
        print("=" * 60)
        for warning in warnings:
            print(f"\n{warning}")
        print("\n" + "-" * 60)
        print("💡 対処方法:")
        print("  1. テストファイルを確認し、シグネチャ変更に対応する更新を行う")
        print("  2. テストが既に正しい場合は、このまま続行しても問題ありません")
        print("=" * 60 + "\n")

        # Warning only, don't block
        # Return 0 to allow push to continue
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
