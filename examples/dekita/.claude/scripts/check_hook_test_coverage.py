#!/usr/bin/env python3
"""フックのテストカバレッジをチェックする。

Why:
    PRで追加・変更されたフックにテストがあるか確認し、
    テスト不足を検出するため。

What:
    - get_changed_files(): PR内の変更ファイルを取得
    - get_hook_files(): フックファイル一覧を取得
    - check_test_coverage(): テストファイルの存在を確認

Remarks:
    - 新規フック: テストファイル必須（なければCI失敗）
    - 既存フック（テストなし）: 警告のみ

Changelog:
    - silenvx/dekita#1300: テストカバレッジチェック機能を追加
"""

import os
import subprocess
import sys
from pathlib import Path


def get_changed_files() -> list[str] | None:
    """Get files changed in this PR compared to base branch.

    Returns:
        List of changed file paths, or None if git diff failed.
        When None is returned, caller should treat all hooks as changed.
    """
    base_ref = os.environ.get("GITHUB_BASE_REF", "main")

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"origin/{base_ref}...HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
    except subprocess.CalledProcessError as e:
        # Return None to indicate failure - caller should treat all hooks as changed
        print(f"⚠️  git diff failed: {e}", file=sys.stderr)
        return None


def get_hook_files() -> list[Path]:
    """Get all Python hook files (excluding common.py and __init__.py)."""
    hooks_dir = Path(".claude/hooks")
    excluded = {"common.py", "__init__.py"}

    return [
        f
        for f in hooks_dir.glob("*.py")
        if f.name not in excluded and not f.name.startswith("test_")
    ]


def get_test_file_for_hook(hook_file: Path) -> Path:
    """Get the expected test file path for a hook."""
    # Convert hyphens to underscores for test file name
    hook_name = hook_file.stem.replace("-", "_")
    return Path(f".claude/hooks/tests/test_{hook_name}.py")


def has_test_files_for_hook(hook_file: Path) -> bool:
    """Check if test files exist for a hook.

    Supports both single test file (test_{hook_name}.py) and
    split test files (test_{hook_name}_*.py).
    """
    hook_name = hook_file.stem.replace("-", "_")
    tests_dir = Path(".claude/hooks/tests")

    # Check for exact match
    exact_test = tests_dir / f"test_{hook_name}.py"
    if exact_test.exists():
        return True

    # Check for split test files (test_{hook_name}_*.py)
    pattern = f"test_{hook_name}_*.py"
    return bool(list(tests_dir.glob(pattern)))


def main():
    changed_files = get_changed_files()
    hook_files = get_hook_files()

    # If git diff failed, treat all hooks as changed (safer behavior)
    diff_available = changed_files is not None

    # Categorize hooks
    new_hooks_without_tests = []
    existing_hooks_without_tests = []

    for hook in hook_files:
        has_test = has_test_files_for_hook(hook)

        if not has_test:
            # Check if this hook was added/modified in this PR
            hook_path = str(hook)
            # If diff failed, treat all hooks without tests as "changed" (fail CI)
            is_changed = not diff_available or hook_path in changed_files

            if is_changed:
                new_hooks_without_tests.append(hook)
            else:
                existing_hooks_without_tests.append(hook)

    # Report
    exit_code = 0

    if new_hooks_without_tests:
        print("❌ 新規/変更されたフックにテストがありません:")
        for hook in new_hooks_without_tests:
            test_file = get_test_file_for_hook(hook)
            print(f"   {hook.name} -> {test_file} を作成してください")
        exit_code = 1

    if existing_hooks_without_tests:
        print("⚠️  既存フックにテストがありません（警告のみ）:")
        for hook in existing_hooks_without_tests:
            print(f"   {hook.name}")

    if exit_code == 0:
        tested_count = len(hook_files) - len(existing_hooks_without_tests)
        total_count = len(hook_files)
        print(f"✅ フックテストカバレッジ: {tested_count}/{total_count} フック")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
