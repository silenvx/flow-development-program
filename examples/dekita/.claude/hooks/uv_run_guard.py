#!/usr/bin/env python3
"""worktree内でのuv run使用を防止。

Why:
    worktreeにはpyproject.tomlがシンボリックリンクされないため、
    uv runはエラーになる。事前にブロックして代替コマンドを提示する。

What:
    - Bashコマンド実行前（PreToolUse:Bash）に発火
    - cwdが.worktrees/内かを確認
    - uv runコマンドを検出したらブロック
    - 代替としてuvxコマンドを提案

Remarks:
    - ブロック型フック（worktree内のuv runはブロック）
    - ツール名を抽出してuvx推奨コマンドを提示

Changelog:
    - silenvx/dekita#2145: フック追加
"""

import json
import os
import re

from lib.session import parse_hook_input

# Pattern to detect 'uv run' commands
UV_RUN_PATTERN = re.compile(r"\buv\s+run\b")


def is_in_worktree() -> bool:
    """Check if current directory is inside a worktree."""
    cwd = os.getcwd()
    # Worktrees are typically in .worktrees/ directory
    return "/.worktrees/" in cwd or "\\.worktrees\\" in cwd


def extract_tool_from_uv_run(command: str) -> str | None:
    """Extract the tool name from 'uv run <tool>' command.

    Examples:
        'uv run ruff check .' -> 'ruff'
        'uv run python -m pytest' -> 'python'
        'uv run --with foo bar' -> 'bar'
    """
    # Remove 'uv run' prefix
    remaining = re.sub(r"^\s*uv\s+run\s+", "", command)

    # Options that take a value (the next element is the value)
    value_options = {"--with", "--python"}

    parts = remaining.split()
    tool_name = None
    i = 0
    while i < len(parts):
        part = parts[i]

        # Handle options
        if part.startswith("-"):
            # --opt=value format: skip this element only
            if "=" in part:
                i += 1
                continue

            # Known options that take a value: skip option and its value
            if part in value_options:
                # Skip option and value (if value exists)
                i += 2 if i + 1 < len(parts) else len(parts)
                continue

            # Other options: skip option only
            i += 1
            continue

        # First non-option is the tool name
        tool_name = part
        break

    return tool_name


def main() -> None:
    """Main entry point."""
    input_data = parse_hook_input()
    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")

    # Only check if in worktree and command contains 'uv run'
    if is_in_worktree() and UV_RUN_PATTERN.search(command):
        tool_name = extract_tool_from_uv_run(command)

        suggestion = ""
        if tool_name:
            suggestion = f"\n\n推奨コマンド: `uvx {tool_name} ...`"

        result = {
            "allow": False,
            "reason": (
                "# uv run はworktree内で使用できません\n\n"
                "worktreeにはpyproject.tomlがシンボリックリンクされていないため、"
                "`uv run` はエラーになります。\n\n"
                "代わりに `uvx` を使用してください。"
                f"{suggestion}"
            ),
        }
        print(json.dumps(result))
        return

    print(json.dumps({"allow": True}))


if __name__ == "__main__":
    main()
