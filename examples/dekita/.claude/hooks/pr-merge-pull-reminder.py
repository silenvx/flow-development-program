#!/usr/bin/env python3
# - 責務: PRマージ後にmainブランチを自動pull
# - 重複なし: worktree-auto-cleanupはworktree削除、本フックはmain pull
# - 自動化型: マージ成功後に即座にmainを最新化
"""
PostToolUse hook to auto-pull main after PR merge.

When `gh pr merge` succeeds, this hook:
1. Detects if the current directory is the main repository
2. Pulls the main branch to sync with remote

This prevents working with stale main branch after merging PRs.
"""

import os
import subprocess
from pathlib import Path

from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.hook_input import get_exit_code, get_tool_result
from lib.repo import get_repo_root, is_merge_success
from lib.session import parse_hook_input


def _get_current_branch(repo_root: Path) -> str | None:
    """Get the current branch name."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def is_in_worktree() -> bool:
    """Check if current directory is in a worktree."""
    cwd = os.getcwd()
    return "/.worktrees/" in cwd or cwd.endswith("/.worktrees")


def is_pr_merge_command(tool_input: str) -> bool:
    """Check if the command is a PR merge command."""
    return "gh pr merge" in tool_input


def _check_merge_success(tool_output: str, exit_code: int, command: str = "") -> bool:
    """Check if the merge was successful.

    Wrapper around common.is_merge_success for backward compatibility.
    """
    return is_merge_success(exit_code, tool_output, command)


def pull_main(repo_root: Path) -> tuple[bool, str]:
    """Pull main branch in the repository root."""
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Timeout while pulling main"
    except FileNotFoundError:
        return False, "git command not found"


def main() -> None:
    """Main hook logic."""
    input_data = parse_hook_input()
    if not input_data:
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name != "Bash":
        return

    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")

    if not is_pr_merge_command(command):
        return

    tool_output = input_data.get("tool_output", "")
    # Issue #2203: Use get_exit_code() for consistent default value
    # Use get_tool_result() to handle both tool_result and tool_response
    tool_result = get_tool_result(input_data) or {}
    exit_code = get_exit_code(tool_result)

    if not _check_merge_success(tool_output, exit_code, command):
        return

    repo_root = get_repo_root()
    if not repo_root:
        return

    # Even if in worktree, pull main in the main repository
    # Issue #727: リマインダーだけでは忘れられるため、自動実行する
    if is_in_worktree():
        # Check if main repo is on main branch before pulling
        main_repo_branch = _get_current_branch(repo_root)
        if main_repo_branch != "main":
            # Main repo is not on main branch - show reminder instead
            print(
                f"[pr-merge-pull-reminder] PRがマージされました。\n"
                f"メインリポジトリが{main_repo_branch}ブランチのため自動pullをスキップ。\n"
                f"手動でpullしてください:\n"
                f"  cd {repo_root}\n"
                f"  git checkout main\n"
                f"  git pull origin main"
            )
            log_hook_execution(
                "pr-merge-pull-reminder",
                "approve",
                f"skipped: main repo on {main_repo_branch}, not main",
            )
            return

        # Pull main in the main repo (not the worktree)
        success, output = pull_main(repo_root)
        if success:
            print(f"[pr-merge-pull-reminder] メインリポジトリでmainを自動pullしました: {output}")
            log_hook_execution(
                "pr-merge-pull-reminder",
                "approve",
                f"auto_pull from worktree: {output}",
            )
        else:
            print(f"[pr-merge-pull-reminder] main pullに失敗しました: {output}")
            log_hook_execution(
                "pr-merge-pull-reminder",
                "approve",
                f"auto_pull failed from worktree: {output}",
            )
        return

    # Auto-pull main (only if on main branch)
    current_branch = _get_current_branch(repo_root)
    if current_branch != "main":
        # Not on main branch - show reminder instead of pulling
        # (pulling origin main into a feature branch would cause issues)
        print(
            "[pr-merge-pull-reminder] PRがマージされました。"
            "mainブランチに切り替えてpullしてください: git checkout main && git pull origin main"
        )
        log_hook_execution(
            "pr-merge-pull-reminder",
            "approve",
            f"reminder: not on main (current: {current_branch})",
        )
        return

    success, output = pull_main(repo_root)
    if success:
        print(f"[pr-merge-pull-reminder] mainブランチを自動pullしました: {output}")
        log_hook_execution(
            "pr-merge-pull-reminder",
            "approve",
            f"auto_pull on main: {output}",
        )
    else:
        print(f"[pr-merge-pull-reminder] main pullに失敗しました: {output}")
        log_hook_execution(
            "pr-merge-pull-reminder",
            "approve",
            f"auto_pull failed on main: {output}",
        )


if __name__ == "__main__":
    main()
