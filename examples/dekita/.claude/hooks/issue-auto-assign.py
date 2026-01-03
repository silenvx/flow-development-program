#!/usr/bin/env python3
"""worktree作成時にブランチ名からIssue番号を抽出し自動アサイン・競合チェック。

Why:
    複数セッションが同じIssueに着手すると作業の重複・競合が発生する。
    worktree作成時点でIssueの状態を確認し、競合を事前に防止する。

What:
    - git worktree addコマンドからブランチ名/パスを解析
    - ブランチ名からIssue番号を抽出（issue-123, fix/123-desc等）
    - 以下をブロック: クローズ済み、重複worktree、リモートブランチ存在、
      オープンPR存在、他者アサイン済み
    - 未アサインなら自動で@meにアサイン
    - 最近マージされたPRがあれば警告

Remarks:
    - ブロック型フック（競合防止のため厳格）
    - 自分のみアサイン済みは許可（作業継続）
    - worktree-creation-markerはセッション追跡、本フックは競合防止

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1453: 最近マージされたPR警告を追加
"""

import json
import re
import subprocess
import sys

from lib.constants import TIMEOUT_HEAVY, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input


def extract_issue_number(branch_name: str) -> int | None:
    """Extract issue number from branch name.

    Patterns:
    - #123
    - issue-123, issue_123
    - /123- or /123_ (after slash, like fix/123-description)
    - -123- or _123_ (embedded, like feature-123-name)
    - -123 or _123 (at end, like feature-123)

    Note: Patterns like `feature-v2-name` may match `2` unintentionally.
    This is acceptable as worktree branches typically use issue numbers explicitly.
    """
    patterns = [
        r"#(\d+)",  # #123
        r"issue[_-](\d+)",  # issue-123, issue_123
        r"/(\d+)[-_]",  # /123-description
        r"[-_](\d+)[-_]",  # feature-123-name
        r"[-_](\d+)$",  # feature-123 (at end)
    ]

    for pattern in patterns:
        match = re.search(pattern, branch_name, re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None


def get_existing_worktree_branches() -> list[tuple[str, str]]:
    """Get list of existing worktree branches.

    Returns:
        List of (worktree_path, branch_name) tuples.
        Only includes worktrees with branches (excludes detached HEAD).
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        worktrees: list[tuple[str, str]] = []
        current_path = None
        current_branch = None

        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                # Save previous worktree if it had a branch
                if current_path and current_branch:
                    worktrees.append((current_path, current_branch))

                current_path = line[9:]  # Remove "worktree " prefix
                current_branch = None  # Reset for new entry

            elif line.startswith("branch refs/heads/"):
                current_branch = line[18:]  # Remove "branch refs/heads/" prefix

        # Don't forget the last worktree
        if current_path and current_branch:
            worktrees.append((current_path, current_branch))

        return worktrees

    except Exception:
        # Fail open: return empty list on error to avoid blocking
        return []


def find_duplicate_issue_worktree(
    issue_number: int, new_branch: str | None, new_path: str | None
) -> tuple[str, str] | None:
    """Check if another worktree already exists for the same issue.

    Args:
        issue_number: The issue number to check.
        new_branch: The new branch name (to exclude from check).
        new_path: The new worktree path (to exclude from check).

    Returns:
        Tuple of (worktree_path, branch_name) if duplicate found, None otherwise.
    """
    worktrees = get_existing_worktree_branches()

    for path, branch in worktrees:
        # Skip if same branch name or path
        if branch == new_branch:
            continue
        if new_path and path.endswith(new_path.lstrip(".")):
            continue

        # Check if this worktree's branch references the same issue
        existing_issue = extract_issue_number(branch)
        # Also check path if branch didn't have issue number
        if not existing_issue:
            existing_issue = extract_issue_from_path(path)
        if existing_issue == issue_number:
            return (path, branch)

    return None


def find_remote_branch_for_issue(issue_number: int, new_branch: str | None) -> str | None:
    """Check if a remote branch already exists for the same issue.

    Args:
        issue_number: The issue number to check.
        new_branch: The new branch name (to exclude from check).

    Returns:
        Remote branch name if found, None otherwise.
    """
    try:
        # Fetch latest remote branches (quiet mode, prune deleted, origin only)
        subprocess.run(
            ["git", "fetch", "--quiet", "--prune", "origin"],
            capture_output=True,
            timeout=TIMEOUT_HEAVY,
        )

        # Get all remote branches
        result = subprocess.run(
            ["git", "branch", "-r"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None

        for line in result.stdout.strip().split("\n"):
            branch = line.strip()
            if not branch or "->" in branch:  # Skip HEAD pointer
                continue

            # Remove remote prefix for comparison (origin/, upstream/, fork/, etc.)
            local_name = branch.split("/", 1)[1] if "/" in branch else branch
            if local_name == new_branch:
                continue

            # Check if this branch references the same issue
            existing_issue = extract_issue_number(branch)
            if existing_issue == issue_number:
                return branch

    except (subprocess.TimeoutExpired, OSError):
        # Fail open: return None on error to avoid blocking
        pass
    return None


def find_open_pr_for_issue(issue_number: int) -> dict | None:
    """Check if an open PR already exists that references this issue.

    Args:
        issue_number: The issue number to check.

    Returns:
        Dict with 'number', 'title', 'url' if found, None otherwise.
    """
    try:
        # Search for open PRs that reference this issue
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,url,body,headRefName",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None

        prs = json.loads(result.stdout)
        for pr in prs:
            # Check if PR body contains "Closes #N" or "Fixes #N"
            body = pr.get("body", "") or ""
            if re.search(rf"(?:closes|fixes|resolves)\s*#?{issue_number}\b", body, re.IGNORECASE):
                return {"number": pr["number"], "title": pr["title"], "url": pr["url"]}

            # Also check branch name
            branch = pr.get("headRefName", "")
            branch_issue = extract_issue_number(branch)
            if branch_issue == issue_number:
                return {"number": pr["number"], "title": pr["title"], "url": pr["url"]}

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        # Fail open: return None on error to avoid blocking
        pass
    return None


def find_recently_merged_pr_for_issue(issue_number: int, hours: int = 24) -> dict | None:
    """Check if a PR referencing this issue was merged recently.

    Issue #1453: Detect recently merged PRs to warn about potential duplicate work.

    Args:
        issue_number: The issue number to check.
        hours: Time threshold in hours (default: 24).

    Returns:
        Dict with 'number', 'title', 'url', 'mergedAt' if found, None otherwise.
    """
    from datetime import UTC, datetime, timedelta

    try:
        # Search for merged PRs that reference this issue
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "merged",
                "--json",
                "number,title,url,body,headRefName,mergedAt",
                "--limit",
                "50",  # Limit to recent PRs for performance
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None

        prs = json.loads(result.stdout)
        threshold = datetime.now(UTC) - timedelta(hours=hours)

        for pr in prs:
            # Parse merge time
            merged_at_str = pr.get("mergedAt", "")
            if not merged_at_str:
                continue

            # Parse ISO format timestamp (use replace for codebase consistency)
            try:
                merged_at = datetime.fromisoformat(merged_at_str.replace("Z", "+00:00"))
            except ValueError:
                continue

            # Skip if merged before threshold
            if merged_at < threshold:
                continue

            # Check if PR body contains "Closes #N" or "Fixes #N"
            body = pr.get("body", "") or ""
            if re.search(rf"(?:closes|fixes|resolves)\s*#?{issue_number}\b", body, re.IGNORECASE):
                return {
                    "number": pr["number"],
                    "title": pr["title"],
                    "url": pr["url"],
                    "mergedAt": pr["mergedAt"],
                }

            # Also check branch name
            branch = pr.get("headRefName", "")
            branch_issue = extract_issue_number(branch)
            if branch_issue == issue_number:
                return {
                    "number": pr["number"],
                    "title": pr["title"],
                    "url": pr["url"],
                    "mergedAt": pr["mergedAt"],
                }

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        # Fail open: return None on error to avoid blocking
        pass
    return None


def get_issue_info(issue_number: int) -> dict | None:
    """Get issue state and assignees.

    Returns:
        Dict with 'state' and 'assignees' keys, or None on error.
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--json", "state,assignees"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        # Fail silently: gh command unavailable, timeout, or invalid JSON response
        pass
    return None


def get_current_user() -> str | None:
    """Get the current GitHub user login.

    Returns:
        The current user's login name, or None on error.
    """
    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Fail silently: gh command unavailable or timeout
        pass
    return None


def assign_issue(issue_number: int) -> bool:
    """Assign the issue to the current user."""
    try:
        result = subprocess.run(
            ["gh", "issue", "edit", str(issue_number), "--add-assignee", "@me"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Fail silently: gh command unavailable or timeout
        # Return False to show warning message with manual command
        return False


def parse_worktree_add_command(command: str) -> tuple[str | None, str | None]:
    """Parse git worktree add command and extract branch name and path.

    Returns a tuple of (branch_name, worktree_path).
    Either or both may be None if not found.

    Supported patterns:
    - git worktree add --lock .worktrees/name -b branch-name
    - git worktree add .worktrees/name branch-name
    - git worktree add .worktrees/issue-123 (path only)

    Note: Combined short options like `-bf branch` are not supported as git's
    `-b` option requires an argument, making combined forms uncommon.
    """
    if "git worktree add" not in command:
        return None, None

    branch_name = None
    worktree_path = None

    # Look for -b <branch> pattern
    branch_match = re.search(r"-b\s+([^\s]+)", command)
    if branch_match:
        branch_name = branch_match.group(1)

    # Parse command parts to find positional arguments
    # git worktree add [options] <path> [<branch>]
    parts = command.split()

    # Find position of 'add' to start looking for positional args
    try:
        add_idx = parts.index("add")
    except ValueError:
        return branch_name, worktree_path

    # Collect positional arguments (non-option arguments after 'add')
    positional_args = []
    skip_next = False
    for part in parts[add_idx + 1 :]:
        if skip_next:
            skip_next = False
            continue
        if part.startswith("-"):
            # Skip options that take an argument
            if part in ("-b", "--reason"):
                skip_next = True
            # --lock is a flag without argument, just skip it
            continue
        positional_args.append(part)

    # First positional arg is always the path
    if len(positional_args) >= 1:
        worktree_path = positional_args[0]

    # If we have 2 positional args and no -b branch, the second is the branch name
    if len(positional_args) >= 2 and not branch_name:
        branch_name = positional_args[1]

    return branch_name, worktree_path


def extract_issue_from_path(path: str | None) -> int | None:
    """Extract issue number from worktree path.

    E.g., ".worktrees/issue-454" -> 454
    """
    if not path:
        return None

    # Extract the worktree name from path
    for prefix in [".worktrees/", "worktrees/"]:
        if prefix in path:
            worktree_name = path.split(prefix)[-1]
            return extract_issue_number(worktree_name)

    # Try the path directly
    return extract_issue_number(path)


def main():
    """PreToolUse hook for Bash commands.

    Detects git worktree add and auto-assigns related issues.
    """
    result = {"decision": "approve"}

    try:
        # Read input from stdin
        input_data = parse_hook_input()
        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only process git worktree add commands
        if "git worktree add" not in command:
            pass  # Early return case - still log at end
        else:
            # Extract branch name and path
            branch_name, worktree_path = parse_worktree_add_command(command)

            # Try to extract issue number from branch name first, then from path
            issue_number = None
            if branch_name:
                issue_number = extract_issue_number(branch_name)
            if not issue_number:
                issue_number = extract_issue_from_path(worktree_path)

            if not issue_number:
                pass  # No issue number - still log at end
            else:
                # First, check issue state (must be done before other checks)
                issue_info = get_issue_info(issue_number)
                if issue_info and issue_info.get("state") == "CLOSED":
                    reason = (
                        f"🚫 Issue #{issue_number} は既にクローズされています。\n"
                        f"オープンなIssueを選択してください。\n"
                        f"確認: `gh issue view {issue_number}`"
                    )
                    result = make_block_result("issue-auto-assign", reason)
                else:
                    # Check if another worktree already exists for this issue (BLOCK)
                    duplicate = find_duplicate_issue_worktree(
                        issue_number, branch_name, worktree_path
                    )
                    if duplicate:
                        dup_path, dup_branch = duplicate
                        reason = (
                            f"🚫 Issue #{issue_number} は既に別のworktreeで作業中です！\n"
                            f"   既存worktree: {dup_path}\n"
                            f"   ブランチ: {dup_branch}\n\n"
                            f"別のIssueを選択するか、既存worktreeで作業を続けてください。"
                        )
                        result = make_block_result("issue-auto-assign", reason)
                    else:
                        # Check if a remote branch already exists for this issue (BLOCK)
                        remote_branch = find_remote_branch_for_issue(issue_number, branch_name)
                        if remote_branch:
                            reason = (
                                f"🚫 Issue #{issue_number} のリモートブランチが既に存在します！\n"
                                f"   リモートブランチ: {remote_branch}\n\n"
                                f"既存ブランチで作業するか、別のIssueを選択してください。\n"
                                f"既存ブランチを使う: `git worktree add .worktrees/issue-{issue_number} {remote_branch}`"
                            )
                            result = make_block_result("issue-auto-assign", reason)
                        else:
                            # Check if an open PR already exists for this issue (BLOCK)
                            existing_pr = find_open_pr_for_issue(issue_number)
                            if existing_pr:
                                reason = (
                                    f"🚫 Issue #{issue_number} を参照するオープンPRが既に存在します！\n"
                                    f"   PR #{existing_pr['number']}: {existing_pr['title']}\n"
                                    f"   URL: {existing_pr['url']}\n\n"
                                    f"既存PRをレビュー・マージするか、別のIssueを選択してください。"
                                )
                                result = make_block_result("issue-auto-assign", reason)
                            else:
                                # Check if issue already has assignees (BLOCK to prevent conflicts)
                                assignees = (
                                    [
                                        login
                                        for a in issue_info.get("assignees", [])
                                        if (login := a.get("login")) and login.strip()
                                    ]
                                    if issue_info
                                    else []
                                )
                                if assignees:
                                    # Get current user to check if self-assigned
                                    current_user = get_current_user()
                                    # Block only if there are assignees OTHER than the current user
                                    # If current_user is None (gh unavailable), treat all as others (fail-safe)
                                    other_assignees = (
                                        [a for a in assignees if a != current_user]
                                        if current_user
                                        else assignees
                                    )
                                    if other_assignees:
                                        reason = (
                                            f"🚫 Issue #{issue_number} は既にアサイン済み: "
                                            f"{', '.join(other_assignees)}\n"
                                            f"このIssueは他のセッションで作業中の可能性があります。\n\n"
                                            f"別のIssueを選択するか、担当者に確認してください。\n"
                                            f"確認: `gh issue view {issue_number}`"
                                        )
                                        result = make_block_result("issue-auto-assign", reason)
                                    else:
                                        # Only self-assigned - allow the operation
                                        result["systemMessage"] = (
                                            f"✅ Issue #{issue_number} は既に自分にアサイン済み（作業継続可能）"
                                        )
                                else:
                                    # Auto-assign the issue
                                    if assign_issue(issue_number):
                                        result["systemMessage"] = (
                                            f"✅ Issue #{issue_number} に自動アサインしました（競合防止）"
                                        )
                                    else:
                                        result["systemMessage"] = (
                                            f"⚠️ Issue #{issue_number} のアサインに失敗しました。"
                                            f"手動で実行: `gh issue edit {issue_number} --add-assignee @me`"
                                        )

                                # Issue #1453: Check for recently merged PRs (warning only)
                                # Note: Skip warning if already blocking (warning is redundant)
                                if result.get("decision") != "block":
                                    merged_pr = find_recently_merged_pr_for_issue(issue_number)
                                    if merged_pr:
                                        warning = (
                                            f"\n\n⚠️ Issue #{issue_number} を参照するPRが最近マージされました:\n"
                                            f"   PR #{merged_pr['number']}: {merged_pr['title']}\n"
                                            f"   URL: {merged_pr['url']}\n\n"
                                            f"同じ修正が既に適用されている可能性があります。\n"
                                            f"確認: `gh pr view {merged_pr['number']}`"
                                        )
                                        existing_msg = result.get("systemMessage", "")
                                        result["systemMessage"] = existing_msg + warning

    except Exception as e:
        # Don't block on errors
        print(f"[issue-auto-assign] Error: {e}", file=sys.stderr)
        result = {"decision": "approve"}

    # Always log execution for accurate statistics
    log_hook_execution("issue-auto-assign", result.get("decision", "approve"), result.get("reason"))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
