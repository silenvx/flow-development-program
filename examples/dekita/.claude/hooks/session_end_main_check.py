#!/usr/bin/env python3
# - 責務: セッション終了時にmainブランチが最新か確認し、遅れていれば自動pull
# - 重複なし: pr-merge-pull-reminderは自動pull、これは最終確認+自動pull
# - 非ブロック型: 警告と自動pullのみ（ブロックしない）
# Issue #1103: 遅れている場合は自動でpullするよう変更
"""
Stop hook to verify main branch is up-to-date at session end.

At session end:
1. Checks if main branch is behind origin/main
2. If behind, automatically pulls origin/main
3. Reports the result
"""

import json
import subprocess
import sys
from pathlib import Path

from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.repo import get_repo_root
from lib.results import print_approve_and_log_skip
from lib.session import create_hook_context, parse_hook_input


def is_main_behind(repo_root: Path) -> tuple[bool, int, str | None]:
    """Check if local main branch is behind origin/main.

    Returns:
        Tuple of (is_behind, commit_count, error_message)
        - is_behind: True if main is behind origin/main
        - commit_count: Number of commits behind
        - error_message: Error message if check failed, None otherwise
    """
    try:
        # Fetch origin/main (network operation, use longer timeout)
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )

        if fetch_result.returncode != 0:
            return False, 0, f"git fetch failed: {fetch_result.stderr.strip()}"

        # Check if local main is behind origin/main
        # Use 'main..origin/main' instead of 'HEAD..origin/main'
        # to correctly check the main branch regardless of current branch
        result = subprocess.run(
            ["git", "rev-list", "main..origin/main", "--count"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )

        if result.returncode == 0:
            count = int(result.stdout.strip())
            return count > 0, count, None
        return False, 0, f"git rev-list failed: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, 0, "git command timed out"
    except FileNotFoundError:
        return False, 0, "git command not found"
    except ValueError as e:
        return False, 0, f"parse error: {e}"


def get_current_branch(repo_root: Path) -> str | None:
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


def update_main_ref(repo_root: Path) -> tuple[bool, str]:
    """Update local main branch ref to match origin/main.

    Uses 'git fetch origin main:main' to update the local main ref
    without affecting the current checked-out branch.

    Returns:
        Tuple of (success, message)
    """
    try:
        result = subprocess.run(
            ["git", "fetch", "origin", "main:main"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return True, "main updated to origin/main"
        # Handle case where main is currently checked out
        if "refusing to fetch into branch" in result.stderr:
            # Fall back to git pull when on main branch
            pull_result = subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=TIMEOUT_MEDIUM,
            )
            if pull_result.returncode == 0:
                return True, pull_result.stdout.strip()
            return False, pull_result.stderr.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "git fetch timed out"
    except FileNotFoundError:
        return False, "git command not found"


def main():
    """Stop hook to check main branch status."""
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)

        # Prevent infinite loops
        if input_data.get("stop_hook_active"):
            print_approve_and_log_skip("session-end-main-check", "stop_hook_active", ctx=ctx)
            return

        repo_root = get_repo_root()
        if not repo_root:
            print_approve_and_log_skip("session-end-main-check", "repo root not found", ctx=ctx)
            return

        # Check if main is behind
        is_behind, count, error = is_main_behind(repo_root)

        if error:
            # Log error but don't block - just warn
            log_hook_execution(
                "session-end-main-check",
                "approve",
                f"check failed: {error}",
            )
            print(json.dumps(result))
            return

        if is_behind:
            current_branch = get_current_branch(repo_root)

            # 自動でmain refを更新
            pull_success, pull_message = update_main_ref(repo_root)

            if pull_success:
                message = (
                    f"[session-end-main-check] ✅ mainブランチを自動更新しました ({count}コミット)"
                )
                log_hook_execution(
                    "session-end-main-check",
                    "approve",
                    f"auto-pulled {count} commits",
                    {
                        "behind_count": count,
                        "current_branch": current_branch,
                        "pull_success": True,
                    },
                )
            else:
                message = (
                    f"[session-end-main-check] ⚠️ mainブランチの自動更新に失敗しました\n"
                    f"エラー: {pull_message}\n"
                    f"手動で実行してください:\n"
                    f"  cd {repo_root}\n"
                    f"  git pull origin main"
                )
                log_hook_execution(
                    "session-end-main-check",
                    "approve",
                    f"auto-pull failed: {pull_message}",
                    {
                        "behind_count": count,
                        "current_branch": current_branch,
                        "pull_success": False,
                        "error": pull_message,
                    },
                )

            if current_branch != "main":
                message += f"\n(現在のブランチ: {current_branch})"

            result["systemMessage"] = message
        else:
            log_hook_execution(
                "session-end-main-check",
                "approve",
                "main is up-to-date",
            )

    except Exception as e:
        print(f"[session-end-main-check] Error: {e}", file=sys.stderr)
        log_hook_execution("session-end-main-check", "approve", f"Error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
