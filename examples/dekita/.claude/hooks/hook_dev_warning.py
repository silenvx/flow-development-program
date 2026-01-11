#!/usr/bin/env python3
"""worktree内でのフック開発時に変更が反映されない問題を警告する。

Why:
    worktree内でフックを修正しても、CLAUDE_PROJECT_DIRがmainリポジトリを
    指すため、修正が反映されない。この問題を開発者に警告する。

What:
    - cwdがworktree内かどうかをチェック
    - worktree内で.claude/hooks/や.claude/scripts/に変更があれば警告
    - mainにマージするまで変更が反映されないことを通知

Remarks:
    - ブロックせず警告のみ
    - CLAUDE_PROJECT_DIRがmainを指す問題に特化

Changelog:
    - silenvx/dekita#1132: フック追加
"""

import json
import os
import subprocess

from lib.constants import TIMEOUT_LIGHT
from lib.execution import log_hook_execution
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input


def is_in_worktree() -> bool:
    """Check if cwd is inside a worktree (not main repo)."""
    cwd = os.getcwd()
    return "/.worktrees/" in cwd


def get_worktree_root() -> str | None:
    """Extract worktree root from cwd if in a worktree."""
    cwd = os.getcwd()
    if "/.worktrees/" not in cwd:
        return None
    idx = cwd.find("/.worktrees/")
    after = cwd[idx + len("/.worktrees/") :]
    worktree_name = after.split("/")[0]
    return cwd[: idx + len("/.worktrees/")] + worktree_name


def get_modified_hook_files(worktree_root: str) -> list[str]:
    """Get list of modified files under .claude/hooks/ and .claude/scripts/."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                worktree_root,
                "status",
                "--porcelain",
                ".claude/hooks/",
                ".claude/scripts/",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode != 0:
            return []
        stdout = result.stdout.strip()
        if not stdout:
            return []
        lines = stdout.split("\n")
        # git status --porcelain format: 2 status chars + 1 space = 3 chars prefix
        return [line[3:] for line in lines if line.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def main() -> None:
    """Main function."""
    # Issue #2607: Create context for session_id logging
    input_data = parse_hook_input()
    ctx = create_hook_context(input_data)

    result: dict[str, object] = {"continue": True}

    if not is_in_worktree():
        print_continue_and_log_skip("hook-dev-warning", "not in worktree", ctx=ctx)
        return

    worktree_root = get_worktree_root()
    if not worktree_root:
        print_continue_and_log_skip("hook-dev-warning", "worktree root not found", ctx=ctx)
        return

    modified_hooks = get_modified_hook_files(worktree_root)
    if not modified_hooks:
        print_continue_and_log_skip("hook-dev-warning", "no modified hooks", ctx=ctx)
        return

    # Build warning message
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    file_list = "\n".join(f"  - {f}" for f in modified_hooks[:5])
    if len(modified_hooks) > 5:
        file_list += f"\n  ... 他 {len(modified_hooks) - 5} 件"

    # Log the warning
    log_hook_execution(
        "hook-dev-warning",
        "warn",
        f"Modified hooks in worktree not active: {len(modified_hooks)} files",
        {"modified_hooks": modified_hooks, "worktree_root": worktree_root},
    )

    message = f"""⚠️ **Worktree内でフックを開発中ですが、メインリポジトリのフックが使用されます**

変更されたフックファイル:
{file_list}

CLAUDE_PROJECT_DIRがメインリポジトリを指しているため、これらの変更は反映されません:
  CLAUDE_PROJECT_DIR = {project_dir}

【対処法】
1. PRをマージして変更を本番反映
2. またはClaude Codeを以下で再起動:
   ```
   CLAUDE_PROJECT_DIR={worktree_root} claude
   ```

**影響**: merge-check等のフックが期待通りに動作しない可能性があります。
"""
    result["message"] = message
    print(json.dumps(result))


if __name__ == "__main__":
    main()
