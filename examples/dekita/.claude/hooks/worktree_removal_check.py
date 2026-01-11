#!/usr/bin/env python3
"""worktree削除前にアクティブな作業やcwd衝突を検出。

Why:
    別セッション作業中や、cwdが削除対象内にある状態でworktreeを削除すると、
    セッション破損（ENOENT）や作業消失が発生する。削除前に検出してブロックする。

What:
    - git worktree remove実行前（PreToolUse:Bash）に発火
    - コマンドからworktreeパスを抽出
    - 別セッションのマーカーファイルを確認（30分以内なら作業中）
    - マージ済みPRがあればcwd/作業チェックをスキップ
    - cwdが削除対象内ならブロック
    - 未コミット変更・最近のコミット・stashがあれば警告

State:
    - reads: .worktrees/*/.claude-session

Remarks:
    - ブロック型フック（危険な削除はブロック）
    - 別セッションチェックは--forceでもバイパス不可
    - マージ済みPRならcwdチェックをスキップ（Issue #1809）
    - SKIP_WORKTREE_CHECK=1で全チェックをバイパス可能

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#589: cwdチェック追加
    - silenvx/dekita#840: マージ済みPRチェック追加
    - silenvx/dekita#914: gh pr viewに変更（削除済みブランチ対応）
    - silenvx/dekita#990: SKIP_WORKTREE_CHECK追加
    - silenvx/dekita#994: cdパターンをcwdチェックから除外
    - silenvx/dekita#1172: hook_cwd対応
    - silenvx/dekita#1452: --force位置対応
    - silenvx/dekita#1471: パス抽出パターン改善
    - silenvx/dekita#1563: 別セッション検出追加
    - silenvx/dekita#1604: subshell/backtick除外
    - silenvx/dekita#1606: fail-openログ追加
    - silenvx/dekita#1809: マージ済みPRでcwdチェックスキップ
    - silenvx/dekita#1863: JSONマーカー対応
"""

import json
import re
import subprocess
from pathlib import Path

from lib.constants import SESSION_MARKER_FILE, TIMEOUT_MEDIUM
from lib.cwd import check_cwd_inside_path, get_effective_cwd
from lib.execution import log_hook_execution
from lib.git import check_recent_commits, check_uncommitted_changes
from lib.input_context import extract_input_context
from lib.results import check_skip_env, make_block_result, print_continue_and_log_skip
from lib.session import HookContext, create_hook_context, parse_hook_input

# 他セッションがアクティブと判断する閾値（分）
# Issue #1563: 30分以内に更新されたセッションマーカーがあれば、別セッションが作業中と判断
OTHER_SESSION_ACTIVE_THRESHOLD_MINUTES = 30


def resolve_worktree_path(worktree_arg: str, cwd: Path) -> Path | None:
    """Resolve worktree path from command argument.

    Args:
        worktree_arg: The worktree path from the command
        cwd: Current working directory for resolving relative paths

    Handles both:
    - Relative paths like ".worktrees/issue-123" or "." (resolved from cwd)
    - Absolute paths like "/path/to/.worktrees/issue-123"
    """
    worktree_path = Path(worktree_arg)

    if worktree_path.is_absolute():
        return worktree_path if worktree_path.exists() else None

    # Relative path - resolve from current working directory
    resolved = (cwd / worktree_path).resolve()
    return resolved if resolved.exists() else None


def check_cwd_inside_worktree(worktree_path: Path, command: str | None = None) -> bool:
    """Check if current working directory is inside the worktree.

    Wrapper around common.check_cwd_inside_path for backward compatibility.
    See common.check_cwd_inside_path for full documentation.

    Args:
        worktree_path: The worktree path being deleted.
        command: Optional command string to check for 'cd <path> &&' pattern

    Returns:
        True if cwd is inside the worktree (should block deletion).
    """
    return check_cwd_inside_path(worktree_path, command)


def check_other_session_active(
    worktree_path: Path, ctx: HookContext
) -> tuple[bool, str | None, float | None]:
    """Check if another session is actively working in the worktree.

    Issue #1563: Detect when another session has the worktree as its cwd.

    Args:
        worktree_path: The worktree path to check.
        ctx: HookContext for session information.

    Returns:
        A tuple of (has_other_session, other_session_id, minutes_ago):
        - (True, session_id, minutes) if another session is active
        - (False, None, None) if no other session is active or current session owns it

    Implementation notes:
    - Reads .claude-session marker file in the worktree
    - Checks if marker was updated within OTHER_SESSION_ACTIVE_THRESHOLD_MINUTES
    - Compares with current session ID to allow self-cleanup
    """
    from datetime import UTC, datetime

    marker_path = worktree_path / SESSION_MARKER_FILE
    if not marker_path.exists():
        return False, None, None

    try:
        # Check marker file modification time
        mtime = datetime.fromtimestamp(marker_path.stat().st_mtime, tz=UTC)
        now = datetime.now(UTC)
        age_minutes = (now - mtime).total_seconds() / 60

        # If marker is too old, consider it stale
        if age_minutes > OTHER_SESSION_ACTIVE_THRESHOLD_MINUTES:
            return False, None, None

        # Read session ID from marker
        # Issue #1863: Support both JSON format (new) and plain text (old)
        marker_content = marker_path.read_text().strip()
        if not marker_content:
            return False, None, None

        # Try to parse as JSON first (new format from worktree-creation-marker.py)
        if marker_content.startswith("{"):
            try:
                marker_data = json.loads(marker_content)
                marker_session_id = marker_data.get("session_id", "")
            except json.JSONDecodeError:
                # Invalid JSON, treat as plain text
                marker_session_id = marker_content
        else:
            # Plain text format (old format from session-marker-updater.py)
            marker_session_id = marker_content

        if not marker_session_id:
            return False, None, None

        # Get current session ID
        current_session_id = ctx.get_session_id()

        # If it's our own session, allow cleanup
        if marker_session_id == current_session_id:
            return False, None, None

        # Another session is active in this worktree
        return True, marker_session_id, age_minutes

    except (OSError, ValueError):
        # Fail-open: if we can't read the marker, don't block
        return False, None, None


def check_stashed_changes(worktree_path: Path) -> tuple[bool, int]:
    """Check for stashed changes.

    Returns (has_stashes, stash_count).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "stash", "list"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode != 0:
            return False, 0

        lines = [line for line in result.stdout.strip().split("\n") if line]
        return len(lines) > 0, len(lines)

    except (subprocess.TimeoutExpired, OSError):
        # Fail-close: タイムアウト時は安全側に倒す
        return True, -1  # -1 は確認タイムアウトを示す


def extract_git_c_path(command: str) -> str | None:
    """Extract the -C path from git command if present.

    Returns the path specified after -C option, or None if not present.
    """
    match = re.search(r"git\s+-C\s+(\S+)", command)
    return match.group(1) if match else None


def extract_worktree_path_from_command(command: str) -> str | None:
    """Extract worktree path from git worktree remove command.

    Handles:
    - git worktree remove <path>
    - git worktree remove -f <path>
    - git worktree remove --force <path>
    - git -C <repo> worktree remove <path>
    """
    # Match various forms of git worktree remove command
    # Note: May false-positive on `echo "git worktree remove path"` but this is rare
    # Issue #1471: Exclude quotes from path capture to handle bash -c 'cmd' pattern
    # Issue #1604: Exclude parentheses to handle subshell pattern (cd && git worktree remove)
    # Issue #1608: Exclude backticks to handle `...` command substitution
    patterns = [
        # git worktree remove [options] <path>
        r"git\s+(?:-C\s+\S+\s+)?worktree\s+remove\s+(?:-f\s+|--force\s+)?([^\s;|&'\"()`]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            return match.group(1)

    return None


def has_force_flag(command: str) -> bool:
    """Check if command includes force flag (-f or --force).

    This allows users to intentionally bypass the safety check.
    Checks for -f or --force as standalone arguments in either position:
    - git worktree remove --force path
    - git worktree remove path --force

    Issue #1452: Support --force flag after path argument.
    """
    # Pattern 1: flag before path (worktree remove --force path)
    if re.search(r"worktree\s+remove\s+(?:-f|--force)\s+", command):
        return True
    # Pattern 2: flag after path (worktree remove path --force)
    # Match: worktree remove <path> -f or --force at end or followed by whitespace
    if re.search(r"worktree\s+remove\s+\S+\s+(?:-f|--force)(?:\s|$)", command):
        return True
    return False


def get_worktree_branch(worktree_path: Path) -> str | None:
    """Get the branch name of the worktree.

    Returns the branch name or None if not found.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch if branch and branch != "HEAD" else None
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None


def check_pr_merged_for_branch(branch_name: str, worktree_path: Path) -> tuple[bool, int | None]:
    """Check if there's a merged PR for the given branch.

    Args:
        branch_name: The branch name to check for merged PRs.
        worktree_path: The worktree path to run gh command in (for repo context).

    Returns:
        A tuple of (is_merged, pr_number):
        - (True, pr_number) if a merged PR exists
        - (False, None) otherwise

    Note:
        This function depends on the `gh` CLI being installed and authenticated.
        If `gh` is unavailable, times out, or returns invalid JSON, this function
        returns (False, None) to fail-open and allow manual checks.

    Implementation note (Issue #914):
        `gh pr list --head <branch> --state merged` fails when the remote branch
        has been deleted after merge. Instead, we use `gh pr view <branch>` which
        queries by branch name in the PR database and works even after branch deletion.
    """
    try:
        # Use gh pr view to find PR for this branch (works even if remote branch deleted)
        # This returns the PR associated with the branch, regardless of state
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                branch_name,
                "--json",
                "number,mergedAt",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
            cwd=str(worktree_path),
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get("mergedAt"):  # mergedAt is set when PR is merged
                pr_number = data.get("number")
                return True, pr_number
        return False, None
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return False, None


def main():
    """PreToolUse hook for Bash commands.

    Detect active work before worktree removal to prevent session conflicts.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)
        input_context = extract_input_context(input_data)
        tool_input = input_data.get("tool_input", {})

        command = tool_input.get("command", "")
        # Issue #1172: Get cwd from hook input (Claude Code provides session's actual cwd)
        hook_cwd = input_data.get("cwd")

        # Only check git worktree remove commands
        if "git" not in command or "worktree" not in command or "remove" not in command:
            print(json.dumps(result))
            return

        # Issue #990: SKIP_WORKTREE_CHECK environment variable support
        # Allows bypassing all checks including cwd check for recovery scenarios
        # Issue #1260: Use check_skip_env for centralized logging
        # Pass input_context for consistent debugging (same as branch_rename_guard)
        if check_skip_env("worktree-removal-check", "SKIP_WORKTREE_CHECK", input_context):
            print(json.dumps(result))
            return

        # Check for force flag (will bypass active work checks but NOT cwd check)
        force_flag_present = has_force_flag(command)

        # Extract worktree path from command
        worktree_arg = extract_worktree_path_from_command(command)
        if not worktree_arg:
            # Issue #1606: Log fail-open for debugging (was cause of Issue #1604 bypass)
            print_continue_and_log_skip(
                "worktree-removal-check",
                f"worktree path抽出失敗 (fail-open): {command[:100]}",
                ctx=ctx,
            )
            return

        # Determine the working directory for path resolution
        # Use get_effective_cwd() to resolve relative paths like "."
        # Note: We pass command here for path resolution (git -C, relative paths)
        # but NOT to check_cwd_inside_worktree (Issue #994)
        git_c_path = extract_git_c_path(command)
        if git_c_path:
            cwd = Path(git_c_path)
            if not cwd.is_absolute():
                # Resolve relative -C path from effective current directory
                # Issue #1172: Pass hook_cwd for proper session cwd detection
                cwd = get_effective_cwd(command, hook_cwd) / cwd
            cwd = cwd.resolve()
            if not cwd.exists():
                # -C path doesn't exist - let git handle the error
                # Issue #1606: Log fail-open for debugging
                print_continue_and_log_skip(
                    "worktree-removal-check",
                    f"-C path存在しない (fail-open): {git_c_path}",
                    ctx=ctx,
                )
                return
        else:
            # Use effective current working directory for relative path resolution
            # Pass command to handle 'cd <path> &&' pattern for path resolution
            # Issue #1172: Pass hook_cwd for proper session cwd detection
            cwd = get_effective_cwd(command, hook_cwd)

        # Resolve worktree path from the determined working directory
        worktree_path = resolve_worktree_path(worktree_arg, cwd)
        if not worktree_path:
            # Path doesn't exist - let git handle the error
            # Issue #1606: Log fail-open for debugging (was related to Issue #1604 bypass)
            print_continue_and_log_skip(
                "worktree-removal-check",
                f"worktree path解決失敗 (fail-open): arg={worktree_arg}, cwd={cwd}",
                ctx=ctx,
            )
            return

        # Issue #1809: Check if PR is merged BEFORE cwd check
        # If PR is merged, worktree deletion is safe regardless of cwd location
        # This allows cleanup even when session cwd is inside the worktree
        # Note: We still need to check for other active sessions (Issue #1563)
        branch_name = get_worktree_branch(worktree_path)
        pr_is_merged = False
        merged_pr_number = None
        if branch_name:
            pr_is_merged, merged_pr_number = check_pr_merged_for_branch(branch_name, worktree_path)

        # Issue #1563: Check if another session is actively working in this worktree
        # This check is NOT bypassed by --force OR merged PR because it would break another session
        has_other_session, other_sid, minutes_ago = check_other_session_active(worktree_path, ctx)
        if has_other_session:
            worktree_name = worktree_path.name
            short_sid = other_sid[:8] if other_sid else "unknown"
            reason = (
                f"🚫 worktree '{worktree_name}' の削除をブロックしました。\n\n"
                f"別セッション ({short_sid}...) がこのworktree内で作業中です。\n"
                f"（{minutes_ago:.0f}分前に更新）\n\n"
                f"対処方法:\n"
                f"1. 該当セッションが終了するまで待つ\n"
                f"2. または環境変数 SKIP_WORKTREE_CHECK=1 を設定して強制削除\n\n"
                f"⚠️ このチェックは --force やPRマージ済みでもバイパスできません。\n"
                f"   他セッションのcwdが消失すると、そのセッションが破損します。"
            )
            result = make_block_result("worktree-removal-check", reason, ctx)
            log_hook_execution(
                "worktree-removal-check",
                "block",
                f"他セッション作業中: {worktree_name} (session {short_sid})",
            )
            print(json.dumps(result))
            return

        # If PR is merged, skip cwd check and other active work checks
        # (other session check was already done above)
        if pr_is_merged:
            log_hook_execution(
                "worktree-removal-check",
                "approve",
                f"マージ済みPR #{merged_pr_number} 検出: {branch_name} - cwd/アクティブ作業チェックをスキップ",
            )
            print(json.dumps(result))
            return

        # Critical check: Is cwd inside the worktree being deleted?
        # This check is NOT bypassed by --force because it would break the session
        # Issue #994: Do NOT pass command here - 'cd <path> &&' in a Bash command
        # does NOT change the session's actual cwd (it runs in a subshell).
        # Trusting the cd pattern caused session corruption when worktree was deleted.
        # Issue #1172: Use hook_cwd directly to detect session's actual cwd
        # get_effective_cwd(None, hook_cwd) uses hook_cwd as base, ignoring cd patterns
        # P2 fix: Fail-closed - if cwd detection fails, block deletion to be safe
        try:
            session_cwd = get_effective_cwd(None, hook_cwd)
            target_resolved = worktree_path.resolve()
            cwd_inside_worktree = (
                session_cwd == target_resolved or target_resolved in session_cwd.parents
            )
        except OSError:
            # Fail-closed: if we can't determine cwd, block deletion to be safe
            # This could happen if cwd was already deleted or is inaccessible
            cwd_inside_worktree = True
        if cwd_inside_worktree:
            worktree_name = worktree_path.name
            # Issue #1809: Provide actionable guidance
            # Option 1: Use cd && git in same command (runs in subshell)
            # Option 2: Manual execution in new terminal
            reason = (
                f"🚫 worktree '{worktree_name}' の削除をブロックしました。\n\n"
                f"現在の作業ディレクトリ (cwd) が削除対象のworktree内にあります。\n"
                f"削除するとセッションの全Bashコマンドが失敗します。\n\n"
                f"対処方法（いずれか1つを選択）:\n\n"
                f"【方法1】PRがマージ済みの場合:\n"
                f"  PRがマージされていれば、このチェックは自動的にスキップされます。\n"
                f"  まずPRをマージしてから再度削除を試してください。\n\n"
                f"【方法2】新しいターミナルで手動削除:\n"
                f"  別のターミナルを開いて以下を実行:\n"
                f"  git worktree remove {worktree_path}\n\n"
                f"【方法3】環境変数でバイパス:\n"
                f"  SKIP_WORKTREE_CHECK=1 git worktree remove {worktree_path}\n\n"
                f"⚠️ このチェックは --force でもバイパスできません。"
            )
            result = make_block_result("worktree-removal-check", reason, ctx)
            log_hook_execution(
                "worktree-removal-check", "block", f"cwdがworktree内: {worktree_name}"
            )
            print(json.dumps(result))
            return

        # Note: Other session check was moved earlier (before merged PR check)
        # to ensure it's never bypassed (Issue #1563)

        # Skip active work checks if force flag is present
        if force_flag_present:
            log_hook_execution(
                "worktree-removal-check",
                "approve",
                "force flagあり: アクティブ作業チェックをスキップ",
            )
            print(json.dumps(result))
            return

        # Note: Merged PR check was moved earlier (Issue #1809)
        # to allow worktree deletion even when cwd is inside the worktree

        # Check for signs of active work
        issues: list[str] = []

        has_recent, recent_info = check_recent_commits(worktree_path)
        if has_recent:
            issues.append(f"最新コミット（1時間以内）: {recent_info}")

        has_changes, change_count = check_uncommitted_changes(worktree_path)
        if has_changes:
            if change_count < 0:  # タイムアウトの場合
                issues.append("未コミット変更: (確認タイムアウト)")
            else:
                issues.append(f"未コミット変更: {change_count}件")

        has_stashes, stash_count = check_stashed_changes(worktree_path)
        if has_stashes:
            if stash_count < 0:  # タイムアウトの場合
                issues.append("stash: (確認タイムアウト)")
            else:
                issues.append(f"stash: {stash_count}件")

        if issues:
            worktree_name = worktree_path.name
            issues_text = "\n".join(f"  - {issue}" for issue in issues)
            reason = (
                f"⚠️ worktree '{worktree_name}' にアクティブな作業が検出されました:\n"
                f"{issues_text}\n\n"
                f"別セッションが作業中の可能性があります。\n"
                f"削除する場合は --force オプションを使用するか、\n"
                f"先に作業状態を確認してください。"
            )
            result = make_block_result("worktree-removal-check", reason, ctx)
            log_hook_execution("worktree-removal-check", "block", reason)
        else:
            log_hook_execution(
                "worktree-removal-check", "approve", f"worktree削除を許可: {worktree_path.name}"
            )

    except Exception as e:
        # Don't block on errors - log and continue
        log_hook_execution("worktree-removal-check", "error", f"フックエラー: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
