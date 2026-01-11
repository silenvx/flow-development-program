#!/usr/bin/env python3
"""PRマージ成功後にworktreeを自動削除。

Why:
    PRマージ後にworktreeが残るとディスク容量を消費し、管理が煩雑になる。
    マージ成功時に自動削除することで、worktree蓄積を防ぐ。

What:
    - gh pr merge成功後（PostToolUse:Bash）に発火
    - コマンドまたは出力からPR番号を抽出
    - 対応するブランチのworktreeを検索
    - ロック解除後にworktreeを削除

Remarks:
    - 自動化型フック（マージ成功後に即座に実行）
    - cwdがworktree内の場合はスキップ（削除不可）
    - merged-worktree-checkはセッション開始時、本フックはマージ直後

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#778: コマンド出力からPR番号抽出追加
    - silenvx/dekita#803: cwd確認追加
    - silenvx/dekita#1470: exit_codeデフォルト値修正
    - silenvx/dekita#2607: HookContextパターン移行
"""

import json
import re
import subprocess
from pathlib import Path

from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.cwd import check_cwd_inside_path
from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.repo import get_repo_root
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input


def get_pr_branch(pr_number: int, repo_root: Path) -> str | None:
    """Get the head branch name for a PR."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "headRefName", "-q", ".headRefName"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
            cwd=repo_root,  # Run from repo root for gh to find the repo
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        # gh unavailable or timed out - treat as no branch info
        pass
    return None


def find_worktree_by_branch(repo_root: Path, branch: str) -> Path | None:
    """Find worktree path by branch name."""
    worktrees_dir = repo_root / ".worktrees"
    if not worktrees_dir.exists():
        return None

    for item in worktrees_dir.iterdir():
        if not item.is_dir():
            continue

        try:
            result = subprocess.run(
                ["git", "-C", str(item), "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_LIGHT,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip() == branch:
                return item
        except (subprocess.TimeoutExpired, OSError):
            continue

    return None


def remove_worktree(repo_root: Path, worktree_path: Path) -> tuple[bool, str]:
    """Remove a worktree (unlock first if needed).

    Returns (success, message).
    """
    worktree_name = worktree_path.name
    rel_path = f".worktrees/{worktree_name}"

    try:
        # Try to unlock first (ignore errors if not locked)
        subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "unlock", rel_path],
            capture_output=True,
            timeout=TIMEOUT_LIGHT,
            check=False,
        )

        # Remove the worktree
        result = subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "remove", rel_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode == 0:
            return True, f"✅ worktree '{worktree_name}' を削除しました"
        else:
            # Try force remove
            result = subprocess.run(
                ["git", "-C", str(repo_root), "worktree", "remove", "-f", rel_path],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_MEDIUM,
                check=False,
            )
            if result.returncode == 0:
                return True, f"✅ worktree '{worktree_name}' を強制削除しました"
            return False, f"⚠️ worktree削除失敗: {result.stderr.strip()}"

    except subprocess.TimeoutExpired:
        return False, "⚠️ worktree削除がタイムアウトしました"
    except OSError as e:
        return False, f"⚠️ worktree削除エラー: {e}"


def extract_pr_number_from_output(stdout: str) -> int | None:
    """Extract PR number from gh pr merge output.

    Issue #778: When `gh pr merge` succeeds, it outputs:
      ✓ Merged pull request #123 (Title)
    or similar. Parse the PR number from this output.

    This is more reliable than using cwd-dependent gh commands
    since hooks run from CLAUDE_PROJECT_DIR, not the worktree.
    """
    # Match patterns like:
    # ✓ Merged pull request #123
    # ✓ Squashed and merged pull request #123
    # ✓ Rebased and merged pull request #123
    match = re.search(r"(?:Merged|merged)\s+pull\s+request\s+#(\d+)", stdout)
    if match:
        return int(match.group(1))
    return None


def main():
    """PostToolUse hook for Bash commands.

    Auto-cleanup worktrees after successful PR merge.

    Issue #778: Handles both `gh pr merge 123` and `gh pr merge --squash` (no PR number).
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_input = input_data.get("tool_input", {})
        tool_result = get_tool_result(input_data) or {}

        command = tool_input.get("command", "")
        # Default to 0 (success) if exit_code not provided
        # Issue #1470: Previous default of -1 caused cleanup to be skipped for successful commands
        exit_code = tool_result.get("exit_code", tool_result.get("exitCode", 0))

        # Check if this is a real gh pr merge command (with or without PR number)
        # Use regex to avoid false positives like `echo "gh pr merge"`
        # Pattern matches: `gh pr merge`, `cd dir && gh pr merge`, `false || gh pr merge`, etc.
        # But NOT: `echo "gh pr merge"`, `# gh pr merge`, etc.
        if not re.search(r"(?:^|&&\s*|;\s*|\|\|\s*)gh pr merge\b", command) or exit_code != 0:
            print_continue_and_log_skip(
                "worktree-auto-cleanup", "not a gh pr merge or failed", ctx=ctx
            )
            return

        # Get repository root first (needed for PR lookup)
        repo_root = get_repo_root()
        if not repo_root:
            print_continue_and_log_skip("worktree-auto-cleanup", "repo root not found", ctx=ctx)
            return

        # Try to extract PR number from command or output
        # Method 1: From command args (e.g., `gh pr merge 123`, `gh pr merge #123`)
        match = re.search(r"gh pr merge\s+(?:--?\S+\s+)*#?(\d+)", command)
        if match:
            pr_number = int(match.group(1))
        else:
            # Method 2: From merge output (e.g., "✓ Merged pull request #123")
            # This handles `gh pr merge --squash` which merges current branch's PR
            stdout = tool_result.get("stdout", "")
            pr_number = extract_pr_number_from_output(stdout)
            if not pr_number:
                print_continue_and_log_skip("worktree-auto-cleanup", "PR number not found", ctx=ctx)
                return

        # Get PR branch name
        branch = get_pr_branch(pr_number, repo_root)
        if not branch:
            print_continue_and_log_skip(
                "worktree-auto-cleanup", f"branch not found for PR#{pr_number}", ctx=ctx
            )
            return

        # Find corresponding worktree
        worktree_path = find_worktree_by_branch(repo_root, branch)
        if not worktree_path:
            # No worktree found - that's fine, maybe it was created differently
            print_continue_and_log_skip(
                "worktree-auto-cleanup", f"worktree not found for branch {branch}", ctx=ctx
            )
            return

        # Issue #803: Check if cwd is inside the worktree before attempting deletion
        # subprocess calls bypass PreToolUse hooks, so we must check here
        # Pass command to detect cd-prefixed patterns like "cd <worktree> && gh pr merge"
        if check_cwd_inside_path(worktree_path, command):
            worktree_name = worktree_path.name
            result["systemMessage"] = (
                f"⚠️ worktree '{worktree_name}' の自動削除をスキップしました。\n\n"
                f"現在の作業ディレクトリ (cwd) が削除対象のworktree内にあります。\n"
                f"手動で削除するには:\n"
                f"1. cd {repo_root}\n"
                f"2. git worktree remove .worktrees/{worktree_name}"
            )
            log_hook_execution(
                "worktree-auto-cleanup",
                "approve",
                f"cwdがworktree内のため削除スキップ: {worktree_name}",
            )
            print(json.dumps(result))
            return

        # Remove the worktree
        success, message = remove_worktree(repo_root, worktree_path)
        result["systemMessage"] = message

    except Exception as e:
        # Don't block on errors
        result["systemMessage"] = f"⚠️ worktree自動削除中にエラー: {e}"

    log_hook_execution(
        "worktree-auto-cleanup",
        "approve",
        result.get("systemMessage"),
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
