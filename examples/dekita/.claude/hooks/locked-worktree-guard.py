#!/usr/bin/env python3
"""他セッションが所有するPRとworktreeへの操作をブロックする。

Why:
    ロックされたworktreeは別のClaude Codeセッションが作業中であることを示す。
    競合するPR操作やworktree削除をブロックし、セッション間の干渉を防止する。

What:
    - gh pr merge/checkout/close等の変更操作を検出
    - git worktree removeコマンドを検出
    - rm コマンドによるworktree削除を検出
    - ロック中worktreeの所有PRへの操作をブロック
    - CWD内のworktree削除をブロック
    - 非ロック中でもアクティブな作業があれば警告

Remarks:
    - 読み取り専用コマンド（gh pr view等）は許可
    - guard_rules.py、command_parser.py、worktree_manager.pyに分割
    - FORCE_RM_ORPHAN=1で孤立worktree削除チェックをスキップ可能

Changelog:
    - silenvx/dekita#289: rm -rfによるworktree削除ブロック追加
    - silenvx/dekita#317: hook_cwdによる正確なworktree検出
    - silenvx/dekita#528: 非ロックworktreeのアクティブ作業警告追加
    - silenvx/dekita#608: ci-monitor.pyコマンドの検出追加
    - silenvx/dekita#649: 自己ブランチ削除チェック追加
    - silenvx/dekita#795: 孤立worktreeディレクトリ削除ブロック追加
    - silenvx/dekita#1400: 自セッションworktreeのスキップ追加
    - silenvx/dekita#2496: session_idによる自セッション検出追加
    - silenvx/dekita#2618: FORCE_RM_ORPHANインライン指定対応
"""

import json
import os
import sys
from pathlib import Path

from command_parser import (
    is_ci_monitor_command,
    is_gh_pr_command,
    is_modifying_command,
    is_worktree_remove_command,
)
from guard_rules import (
    check_rm_orphan_worktree,
    check_rm_worktree,
    check_self_branch_deletion,
    check_worktree_remove,
)
from lib.execution import log_hook_execution
from lib.github import extract_pr_number
from lib.results import make_block_result
from lib.session import parse_hook_input
from lib.strings import extract_inline_skip_env, is_skip_env_enabled
from worktree_manager import (
    check_active_work_signs,
    get_branch_for_pr,
    get_locked_worktrees,
    get_pr_for_branch,
    get_worktree_for_branch,
    is_cwd_inside_worktree,
    is_self_session_worktree,
)

FORCE_RM_ORPHAN_ENV = "FORCE_RM_ORPHAN"


def has_force_rm_orphan_env(command: str) -> bool:
    """Check if FORCE_RM_ORPHAN environment variable is set with truthy value.

    Supports both:
    - Exported: export FORCE_RM_ORPHAN=1 && rm -rf ...
    - Inline: FORCE_RM_ORPHAN=1 rm -rf ... (including FORCE_RM_ORPHAN="1")

    Only "1", "true", "True" are considered truthy (Issue #956).

    Args:
        command: The command string to check for inline env var.

    Returns:
        True if FORCE_RM_ORPHAN is set with truthy value, False otherwise.
    """
    # Check exported environment variable with value validation
    if is_skip_env_enabled(os.environ.get(FORCE_RM_ORPHAN_ENV)):
        return True
    # Check inline environment variable in command (handles quoted values)
    inline_value = extract_inline_skip_env(command, FORCE_RM_ORPHAN_ENV)
    if is_skip_env_enabled(inline_value):
        return True
    return False


def main():
    """
    PreToolUse hook for Bash commands.

    Blocks:
    1. gh pr commands that would modify PRs owned by locked worktrees
    2. ci-monitor.py commands that would operate on PRs owned by locked worktrees (Issue #608)
    3. git worktree remove commands targeting locked worktrees
    4. rm commands that would delete worktrees while CWD is inside (Issue #289)
    5. gh pr merge --delete-branch when it would delete current worktree's branch (Issue #649)
    6. rm commands targeting orphan worktree directories (Issue #795)

    Environment variables:
    - FORCE_RM_ORPHAN=1: Skip orphan worktree deletion check (use with caution)
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Check for FORCE_RM_ORPHAN bypass (Issue #795, Issue #2618)
        force_rm_orphan = has_force_rm_orphan_env(command)

        # Get cwd from hook input to correctly identify current worktree
        # This fixes Issue #317: hooks run in main repo, not worktree
        hook_cwd = data.get("cwd")

        # Check for worktree remove commands
        # NOTE: Do not early-return on approval to allow gh pr checks for mixed commands
        if is_worktree_remove_command(command):
            block_result = check_worktree_remove(command, hook_cwd)
            if block_result:
                log_hook_execution(
                    "locked-worktree-guard",
                    "block",
                    reason="worktree remove blocked",
                    details={"command": command},
                )
                print(json.dumps(block_result))
                sys.exit(0)
            # If worktree removal is allowed, continue to check for gh pr commands
            # in case this is a mixed command like "git worktree remove foo && gh pr merge 123"

        # Check for rm commands targeting worktrees (Issue #289)
        # This prevents shell corruption when deleting worktree while CWD is inside
        block_result = check_rm_worktree(command, hook_cwd)
        if block_result:
            log_hook_execution(
                "locked-worktree-guard",
                "block",
                reason="rm worktree blocked",
                details={"command": command},
            )
            print(json.dumps(block_result))
            sys.exit(0)

        # Check for rm commands targeting orphan worktree directories (Issue #795)
        # This prevents accidental deletion of worktrees not registered with git
        if not force_rm_orphan:
            block_result = check_rm_orphan_worktree(command, hook_cwd)
            if block_result:
                log_hook_execution(
                    "locked-worktree-guard",
                    "block",
                    reason="rm orphan worktree blocked",
                    details={"command": command},
                )
                print(json.dumps(block_result))
                sys.exit(0)

        # Get cwd and locked worktrees once for all checks
        # Issue #806: Use is_cwd_inside_worktree() for more robust self-worktree detection
        # (avoids path resolution issues with current_worktree == worktree_path comparison)
        cwd = Path(hook_cwd) if hook_cwd else None
        locked_worktrees = get_locked_worktrees()
        # Issue #2496: Get session_id for self-session worktree detection
        session_id = data.get("session_id")

        # Check for ci-monitor.py commands (Issue #608)
        # ci-monitor.py internally calls gh pr commands, so we need to intercept it
        # NOTE: Do not early-return on approval to allow gh pr checks for mixed commands
        # (e.g., "python ci-monitor.py 123 && gh pr merge 456")
        is_ci_monitor, ci_pr_numbers = is_ci_monitor_command(command)
        if is_ci_monitor and ci_pr_numbers:
            for worktree_path, branch in locked_worktrees:
                # Skip current worktree (we own it)
                # Issue #806: Use is_cwd_inside_worktree for robust detection
                # Issue #1400: Also skip if current session created the worktree
                if is_cwd_inside_worktree(worktree_path, cwd) or is_self_session_worktree(
                    worktree_path, session_id
                ):
                    continue

                # Get PR for this branch
                branch_pr = get_pr_for_branch(branch)
                # Check if any of the ci-monitor PR numbers matches the locked branch
                for ci_pr_number in ci_pr_numbers:
                    if branch_pr and branch_pr == ci_pr_number:
                        reason = (
                            f"PR #{ci_pr_number} は別のセッションが処理中です。\n\n"
                            f"ロック中のworktree: {worktree_path}\n"
                            f"ブランチ: {branch}\n\n"
                            "ci-monitor.py は内部で gh pr コマンドを実行するため、\n"
                            "ロック中のworktreeのPRに対する操作はブロックされます。\n\n"
                            "このPRを監視する必要がある場合は:\n"
                            "1. 該当セッションの完了を待つ\n"
                            f"2. または git worktree unlock でロック解除（他セッションに影響あり）"
                        )
                        result = make_block_result("locked-worktree-guard", reason)
                        log_hook_execution(
                            "locked-worktree-guard",
                            "block",
                            reason=f"ci-monitor.py for PR #{ci_pr_number} owned by locked worktree",
                            details={"worktree": str(worktree_path), "branch": branch},
                        )
                        print(json.dumps(result))
                        sys.exit(0)
            # ci-monitor.py command but no PR in locked worktree
            # Continue to check for gh pr commands in case of mixed commands

        # Check for self-branch deletion via gh pr merge --delete-branch (Issue #649)
        # This MUST be checked before other gh pr checks because it's a self-inflicted issue
        # (not about locked worktrees or other sessions)
        block_result = check_self_branch_deletion(command, hook_cwd)
        if block_result:
            log_hook_execution(
                "locked-worktree-guard",
                "block",
                reason="self-branch deletion blocked",
                details={"command": command},
            )
            print(json.dumps(block_result))
            sys.exit(0)

        # Only check gh pr commands (handles global flags before 'pr')
        if not is_gh_pr_command(command):
            result = {"decision": "approve"}
            log_hook_execution(
                "locked-worktree-guard", "approve", details={"reason": "not relevant command"}
            )
            print(json.dumps(result))
            sys.exit(0)

        # Only check modifying commands
        if not is_modifying_command(command):
            result = {"decision": "approve"}
            log_hook_execution(
                "locked-worktree-guard", "approve", details={"reason": "read-only command"}
            )
            print(json.dumps(result))
            sys.exit(0)

        # Extract PR number from command
        pr_number = extract_pr_number(command)
        if not pr_number:
            result = {"decision": "approve"}
            log_hook_execution(
                "locked-worktree-guard", "approve", details={"reason": "no PR number found"}
            )
            print(json.dumps(result))
            sys.exit(0)

        # Check if the PR belongs to any locked worktree (excluding current)
        # (cwd and locked_worktrees already obtained above)
        for worktree_path, branch in locked_worktrees:
            # Skip current worktree (we own it)
            # Issue #806: Use is_cwd_inside_worktree for robust detection
            # Issue #1400: Also skip if current session created the worktree
            if is_cwd_inside_worktree(worktree_path, cwd) or is_self_session_worktree(
                worktree_path, session_id
            ):
                continue

            # Get PR for this branch
            branch_pr = get_pr_for_branch(branch)
            if branch_pr and branch_pr == pr_number:
                reason = (
                    f"PR #{pr_number} は別のセッションが処理中です。\n\n"
                    f"ロック中のworktree: {worktree_path}\n"
                    f"ブランチ: {branch}\n\n"
                    "このPRを操作する必要がある場合は:\n"
                    "1. 該当セッションの完了を待つ\n"
                    f"2. または git worktree unlock でロック解除（他セッションに影響あり）"
                )
                result = make_block_result("locked-worktree-guard", reason)
                log_hook_execution(
                    "locked-worktree-guard",
                    "block",
                    reason=f"PR #{pr_number} owned by locked worktree",
                    details={"worktree": str(worktree_path), "branch": branch},
                )
                print(json.dumps(result))
                sys.exit(0)

        # Check for active work signs in non-locked worktrees (Issue #528)
        # This provides a warning (not block) when another session might be working
        pr_branch = get_branch_for_pr(pr_number)
        if pr_branch:
            worktree_for_pr = get_worktree_for_branch(pr_branch)
            if worktree_for_pr:
                # Skip if this is our own worktree
                # Issue #806: Use is_cwd_inside_worktree for robust detection
                if not is_cwd_inside_worktree(worktree_for_pr, cwd):
                    active_signs = check_active_work_signs(worktree_for_pr)
                    if active_signs:
                        signs_text = "\n".join(f"  - {s}" for s in active_signs)
                        result = {
                            "decision": "approve",
                            "systemMessage": (
                                f"⚠️ このPRは別セッションが作業中の可能性があります。\n\n"
                                f"検出された状態:\n"
                                f"  - worktree: {worktree_for_pr}\n"
                                f"{signs_text}\n\n"
                                f"続行する場合は、元のセッションとの競合に注意してください。"
                            ),
                        }
                        log_hook_execution(
                            "locked-worktree-guard",
                            "approve",
                            reason=f"PR #{pr_number} has active work signs (warning)",
                            details={
                                "worktree": str(worktree_for_pr),
                                "branch": pr_branch,
                                "signs": active_signs,
                            },
                        )
                        print(json.dumps(result))
                        sys.exit(0)

        # All checks passed
        result = {"decision": "approve"}
        log_hook_execution(
            "locked-worktree-guard",
            "approve",
            details={"pr": pr_number, "reason": "no conflict with locked worktrees"},
        )

    except Exception as e:
        # On error, approve to avoid blocking (fail open)
        print(f"[locked-worktree-guard] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve"}
        log_hook_execution("locked-worktree-guard", "approve", reason=f"Hook error: {e}")

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
