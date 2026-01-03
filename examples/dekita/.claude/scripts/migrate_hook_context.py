#!/usr/bin/env python3
"""フックをHookContextパターンに移行する。

Why:
    グローバル状態（get_claude_session_id等）を廃止し、
    依存性注入パターン（HookContext）に統一するため。

What:
    - is_simple_migration(): 単純移行か判定
    - migrate_file(): ファイルを移行
    - migrate_all(): 全フックを移行

Remarks:
    - 旧: get_claude_session_id() → 新: ctx.get_session_id()
    - --dry-run でプレビュー
    - ヘルパー関数内での使用は手動移行が必要

Changelog:
    - silenvx/dekita#2413: HookContext移行スクリプトを追加
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def is_simple_migration(content: str) -> bool:
    """Check if the hook is a simple case (get_claude_session_id only in main scope).

    Returns False if get_claude_session_id is used in helper functions,
    which requires manual migration (passing ctx as parameter).
    """
    # Count occurrences of get_claude_session_id()
    matches = list(re.finditer(r"get_claude_session_id\s*\(\s*\)", content))
    if not matches:
        return True  # No usage, nothing to migrate

    # Check if any occurrence is inside a def block (not main or nested in main)
    lines = content.split("\n")
    line_numbers_with_call = set()

    for match in matches:
        # Find line number
        line_num = content[: match.start()].count("\n") + 1
        line_numbers_with_call.add(line_num)

    # Track function depth
    function_stack = []
    indent_stack = []

    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Pop functions that ended (based on indentation)
        while indent_stack and indent <= indent_stack[-1]:
            indent_stack.pop()
            if function_stack:
                function_stack.pop()

        # Check if this is a function definition
        if stripped.startswith("def "):
            func_name_match = re.match(r"def\s+(\w+)\s*\(", stripped)
            if func_name_match:
                func_name = func_name_match.group(1)
                function_stack.append(func_name)
                indent_stack.append(indent)

        # Check if get_claude_session_id is on this line
        if i in line_numbers_with_call:
            # Check if it's in a helper function (not main)
            if function_stack and function_stack[-1] != "main":
                return False  # Helper function usage, needs manual migration

    return True


def migrate_file(file_path: Path, dry_run: bool = True) -> tuple[bool, str]:
    """Migrate a single hook file to HookContext pattern.

    Args:
        file_path: Path to the hook file
        dry_run: If True, don't write changes

    Returns:
        Tuple of (success, message)
    """
    content = file_path.read_text()
    original = content

    # Check if already migrated
    if "create_hook_context" in content:
        return False, "Already migrated"

    # Check if uses get_claude_session_id
    if "get_claude_session_id" not in content:
        return False, "Does not use get_claude_session_id"

    # Check if this is a simple migration case
    if not is_simple_migration(content):
        return False, "Helper function uses get_claude_session_id (manual migration needed)"

    # Check if uses parse_hook_input
    if "parse_hook_input" not in content:
        return False, "Does not use parse_hook_input"

    # 1. Update import: remove get_claude_session_id, add create_hook_context
    # Handle various import patterns

    # Pattern: from lib.session import get_claude_session_id, other_things
    content = re.sub(
        r"from lib\.session import ([^)]+?)get_claude_session_id,?\s*",
        r"from lib.session import \1",
        content,
    )

    # Clean up trailing comma if get_claude_session_id was last
    # Use [^\n]+ to not match across lines
    content = re.sub(r"(from lib\.session import [^\n]+?),\s*\n", r"\1\n", content)

    # Add create_hook_context to imports
    if "create_hook_context" not in content:
        # Find parse_hook_input in import and add create_hook_context
        content = re.sub(
            r"(from lib\.session import\s*)([^(\n]+?)(parse_hook_input)",
            r"\1\2create_hook_context, \3",
            content,
        )

    # Clean up any double commas or trailing commas before )
    content = re.sub(r",\s*,", ",", content)
    content = re.sub(r",\s*\)", ")", content)

    # 2. Find parse_hook_input() assignment and add ctx = create_hook_context()
    # Pattern: data = parse_hook_input() or similar variable names
    parse_hook_pattern = r"(\s*)(\w+)\s*=\s*parse_hook_input\(\)"
    match = re.search(parse_hook_pattern, content)

    if match:
        indent = match.group(1)
        var_name = match.group(2)
        # Add ctx = create_hook_context(data) after the parse_hook_input line
        old_line = match.group(0)
        new_line = f"{old_line}\n{indent}ctx = create_hook_context({var_name})"
        content = content.replace(old_line, new_line, 1)

    # 3. Replace get_claude_session_id() with ctx.get_session_id()
    content = re.sub(r"get_claude_session_id\s*\(\s*\)", "ctx.get_session_id()", content)

    # 4. Update make_block_result calls to include ctx
    # Pattern: make_block_result("hook_name", "reason")
    # Add ctx= as third argument if not present
    if "make_block_result" in content:
        # First check if ctx is already passed
        if "make_block_result(" in content and ", ctx)" not in content and ", ctx=" not in content:
            content = re.sub(
                r"make_block_result\(([^,]+),\s*([^)]+)\)",
                r"make_block_result(\1, \2, ctx)",
                content,
            )

    if content == original:
        return False, "No changes needed"

    if not dry_run:
        file_path.write_text(content)

    return True, "Migrated successfully"


def find_hook_files(hooks_dir: Path) -> list[Path]:
    """Find all hook Python files."""
    files = []
    for f in hooks_dir.glob("*.py"):
        # Skip test files, __init__, and lib modules
        if f.name.startswith("test_"):
            continue
        if f.name == "__init__.py":
            continue
        if f.name in ("common.py", "shell_tokenizer.py", "command_parser.py"):
            continue
        files.append(f)
    return sorted(files)


def main():
    parser = argparse.ArgumentParser(description="Migrate hooks to HookContext pattern")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--file", type=str, help="Migrate a specific file")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show all files, not just migrated"
    )
    args = parser.parse_args()

    # Find hooks directory
    script_dir = Path(__file__).parent
    hooks_dir = script_dir.parent / "hooks"

    if not hooks_dir.exists():
        print(f"Hooks directory not found: {hooks_dir}")
        return 1

    if args.file:
        files = [hooks_dir / args.file]
        if not files[0].exists():
            print(f"File not found: {files[0]}")
            return 1
    else:
        files = find_hook_files(hooks_dir)

    migrated = 0
    skipped = 0
    manual = 0

    for f in files:
        success, message = migrate_file(f, dry_run=args.dry_run)
        if success:
            migrated += 1
            prefix = "[DRY-RUN] " if args.dry_run else ""
            print(f"{prefix}✅ {f.name}: {message}")
        else:
            if "manual migration" in message.lower():
                manual += 1
                print(f"⚠️  {f.name}: {message}")
            else:
                skipped += 1
                if args.verbose:
                    print(f"⏭️  {f.name}: {message}")

    print()
    print(f"Summary: {migrated} migrated, {manual} need manual migration, {skipped} skipped")

    return 0


if __name__ == "__main__":
    exit(main())
