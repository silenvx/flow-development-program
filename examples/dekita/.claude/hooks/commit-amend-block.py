#!/usr/bin/env python3
"""mainリポジトリでのgit commit --amendをブロックする。

Why:
    mainブランチの履歴を変更すると、他のworktreeやリモートと不整合が発生する。
    誤操作を防ぐため、mainリポジトリでの--amendは禁止する。

What:
    - git commit --amendコマンドを検出
    - worktree内での実行は許可
    - mainリポジトリでの実行はブロック
    - workteeへの移動手順を提示

Remarks:
    - ブロック型フック（mainリポジトリでの--amendはブロック）
    - PreToolUse:Bashで発火（git commitコマンド）
    - checkout-block.pyと同様のworktree判定ロジック

Changelog:
    - silenvx/dekita#1368: mainブランチでのgit commit --amend誤操作防止
"""

import json
import os
import re
import subprocess
import sys

from lib.constants import TIMEOUT_LIGHT
from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input
from lib.strings import split_command_chain, strip_quoted_strings

# Pattern to match git global options that can appear between 'git' and the subcommand
# See checkout-block.py for detailed explanation
GIT_GLOBAL_OPTIONS = (
    r"(?:\s+(?:-[CcOo]\s*\S+|--[\w-]+=\S+|"
    r"--[\w-]+\s+(?!commit\b)\S+|--[\w-]+|-[pPhv]|-\d+))*"
)


def is_in_worktree() -> bool:
    """Check if current directory is inside a worktree."""
    cwd = os.getcwd()
    return "/.worktrees/" in cwd or cwd.endswith("/.worktrees")


def is_main_repository() -> bool:
    """Check if current directory is in the main repository (not a worktree)."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if lines:
                first_line = lines[0]
                if first_line.startswith("worktree "):
                    main_repo_path = first_line[9:]
                    cwd = os.getcwd()
                    real_cwd = os.path.realpath(cwd)
                    real_main = os.path.realpath(main_repo_path)
                    return real_cwd == real_main or real_cwd.startswith(real_main + "/")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # git コマンドが利用できない、またはタイムアウトした場合は
        # main リポジトリかどうか判定できないため、操作を許可する（Falseを返す）
        pass
    return False


def contains_amend_flag(command: str) -> bool:
    """Check if command contains git commit --amend.

    Args:
        command: The full command string

    Returns:
        True if command is a git commit --amend, False otherwise
    """
    # Strip quoted strings to avoid false positives like: echo "git commit --amend"
    stripped_command = strip_quoted_strings(command)

    # Split command chain to avoid matching --amend in unrelated chained commands
    # e.g., "git commit -m foo && echo --amend" should not be blocked
    commands = split_command_chain(stripped_command)

    # Pattern for git commit --amend
    # Uses .*? (non-greedy) to match commit options before --amend
    # (?:\s|$) ensures --amend is a standalone option (not a prefix like --amend-message)
    # Handles:
    #   git commit --amend
    #   git commit --amend -m "message"
    #   git commit -m "message" --amend
    #   git -C path commit --amend
    pattern = re.compile(rf"git{GIT_GLOBAL_OPTIONS}\s+commit\s+.*?--amend(?:\s|$)")

    return any(pattern.search(cmd) for cmd in commands)


def main() -> None:
    """
    PreToolUse hook for Bash commands.

    Blocks git commit --amend in main repository to prevent
    accidental modification of main branch commit history.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Skip if not a git commit --amend command
        if not contains_amend_flag(command):
            result = make_approve_result("commit-amend-block")
            print(json.dumps(result))
            sys.exit(0)

        # Allow in worktrees
        if is_in_worktree():
            result = make_approve_result("commit-amend-block")
            print(json.dumps(result))
            sys.exit(0)

        # Block in main repository
        if is_main_repository():
            reason = (
                "[commit-amend-block] mainリポジトリでのgit commit --amendはブロックされました。\n\n"
                "mainブランチの履歴を変更することは危険です。\n\n"
                "【対処法】\n"
                "1. worktreeで作業してください:\n"
                "   git worktree add .worktrees/issue-XXX -b fix/issue-XXX\n"
                "   cd .worktrees/issue-XXX\n\n"
                "2. 直前のコミットを修正したい場合は、worktree内で --amend を実行してください。\n\n"
                "💡 ブロック後も作業を継続してください。\n"
                "代替アクションのツール呼び出しを行い、テキストのみの応答で終わらないでください。"
            )
            log_hook_execution("commit-amend-block", "block", "git commit --amend in main repo")
            result = make_block_result("commit-amend-block", reason)
            print(json.dumps(result))
            sys.exit(0)

        # Not in main repository, approve
        result = make_approve_result("commit-amend-block")

    except Exception as e:
        print(f"[commit-amend-block] Hook error: {e}", file=sys.stderr)
        result = make_approve_result("commit-amend-block", f"Hook error: {e}")

    log_hook_execution("commit-amend-block", result.get("decision", "approve"))
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
