#!/usr/bin/env python3
"""セッション終了時にセッション内作成Issueのステータスを確認し、未完了ならブロック。

Why:
    セッション内で作成したIssueは同セッションで実装まで完遂する必要がある。
    Issue作成だけで終了させず、実装を強制する。

What:
    - セッション終了時（Stopフック）に発火
    - session-created-issues-{session_id}.jsonから作成Issue一覧を取得
    - GitHubでIssueステータスを確認
    - 未完了（OPEN）Issueがあればセッション終了をブロック
    - fork-sessionへの委譲（PR/ロック済worktree存在）は許可

State:
    - reads: .claude/logs/flow/session-created-issues-{session_id}.json

Remarks:
    - ブロック型フック（回数制限なし、完了まで無限ブロック）
    - issue-creation-trackerが記録、本フックが検証
    - 見送る場合は `gh issue close --reason "not planned"` で明示

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1918: セッションファイルをプロジェクトログに移動
    - silenvx/dekita#2076: AGENTS.md原則を明示
    - silenvx/dekita#2090: 回数制限を削除（完了まで無限ブロック）
    - silenvx/dekita#2470: fork-sessionでも自作成Issue実装可能と明記
    - silenvx/dekita#2525: fork-sessionへの委譲検出を追加
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from lib.constants import CONTINUATION_HINT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.session import create_hook_context, is_fork_session, parse_hook_input

# Maximum title length for display
MAX_TITLE_DISPLAY_LENGTH = 50


def _matches_issue_in_branch(branch: str, issue_number: int) -> bool:
    """Check if a branch name references a specific issue number.

    Uses word boundary matching to avoid false positives
    (e.g., issue-12 should not match issue-123).
    """
    import re

    # Pattern: issue-{number} followed by word boundary (-, /, end of string)
    pattern = rf"issue-{issue_number}(?:[-/]|$)"
    return bool(re.search(pattern, branch, re.IGNORECASE))


def _matches_issue_in_title(title: str, issue_number: int) -> bool:
    """Check if a PR title references a specific issue number.

    Uses word boundary matching to avoid false positives
    (e.g., #12 should not match #123).
    """
    import re

    # Pattern: #{number} followed by word boundary
    # Includes: space, ), ], ,, :, ., ?, !, ;, or end of string
    pattern = rf"#{issue_number}(?:[\s\)\],:\.?!;]|$)"
    return bool(re.search(pattern, title))


def is_issue_delegated(issue_number: int) -> tuple[bool, str | None]:
    """Check if an issue has been delegated to a fork-session.

    An issue is considered delegated if either:
    1. An open PR exists that references the issue, OR
    2. A worktree exists for that issue AND is locked (another session is working)

    Args:
        issue_number: The GitHub issue number to check.

    Returns:
        Tuple of (is_delegated, pr_number_or_none).
        - (True, "2524") if PR #2524 is open for this issue
        - (True, None) if worktree is locked but no PR yet
        - (False, None) if not delegated

    Issue #2525: Prevents parent session from interfering with fork-session work.
    """
    # Check 1: Is there an open PR for this issue?
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,headRefName,title",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            prs = json.loads(result.stdout)
            for pr in prs:
                branch = pr.get("headRefName", "")
                title = pr.get("title", "")
                # Check if branch or title references the issue with word boundaries
                if _matches_issue_in_branch(branch, issue_number) or _matches_issue_in_title(
                    title, issue_number
                ):
                    return (True, str(pr.get("number")))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass  # Continue to worktree check

    # Check 2: Is there a locked worktree for this issue?
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            current_worktree = None
            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    current_worktree = line[9:]  # Remove "worktree " prefix
                elif line == "locked" and current_worktree:
                    # Check if this worktree is for our issue (exact match on directory name)
                    worktree_name = Path(current_worktree).name.lower()
                    if worktree_name == f"issue-{issue_number}":
                        return (True, None)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Git not found or timeout - fail-open to avoid blocking session end

    return (False, None)


def _get_session_log_dir() -> Path:
    """Get the directory for session-specific log files.

    Uses .claude/logs/flow/ for consistency with other session logs
    (e.g., state-*.json, events-*.jsonl).

    Issue #1918: Moved from TMPDIR to project logs for persistence.

    Returns:
        Path to the session log directory.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    return Path(project_dir) / ".claude" / "logs" / "flow"


def truncate_title(title: str) -> str:
    """Truncate title with ellipsis if it exceeds MAX_TITLE_DISPLAY_LENGTH."""
    if len(title) > MAX_TITLE_DISPLAY_LENGTH:
        return title[: MAX_TITLE_DISPLAY_LENGTH - 3] + "..."
    return title


def get_session_issues_file(session_id: str) -> Path:
    """Get the file path for storing session-created issues.

    Args:
        session_id: The Claude session ID to scope the file.

    Returns:
        Path to session-specific issues file.
    """
    return _get_session_log_dir() / f"session-created-issues-{session_id}.json"


def load_session_issues(session_id: str) -> list[int]:
    """Load list of issue numbers created in this session.

    Args:
        session_id: The Claude session ID.

    Returns:
        List of issue numbers created in this session.
    """
    issues_file = get_session_issues_file(session_id)
    if issues_file.exists():
        try:
            data = json.loads(issues_file.read_text())
            return data.get("issues", [])
        except (json.JSONDecodeError, OSError):
            pass  # Best effort - corrupted data is ignored
    return []


def clear_session_files(session_id: str) -> None:
    """Clear session files for this session only.

    Args:
        session_id: The Claude session ID.

    Issue #1918: Only call this when we have successfully processed all issues,
    not when file loading fails (which could be a temporary error).

    Called after processing to ensure the next session starts fresh.
    Only deletes files for the current session, not other sessions.
    """
    issues_file = get_session_issues_file(session_id)
    try:
        if issues_file.exists():
            issues_file.unlink()
    except OSError:
        pass  # Silently ignore file deletion errors - best effort cleanup


def get_issue_status(issue_numbers: list[int]) -> list[dict]:
    """Get status of specified issues from GitHub.

    Returns list of dicts with issue number, title, and state.
    """
    if not issue_numbers:
        return []

    issues = []
    for number in issue_numbers:
        try:
            result = subprocess.run(
                [
                    "gh",
                    "issue",
                    "view",
                    str(number),
                    "--json",
                    "number,title,state",
                ],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_MEDIUM,
            )
            if result.returncode == 0:
                issue = json.loads(result.stdout)
                issues.append(issue)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass  # Skip issues that fail to fetch - continue with others
    return issues


def format_info_message(open_issues: list[dict], closed_issues: list[dict]) -> str:
    """Format the informational message for systemMessage."""
    lines = []

    if open_issues:
        lines.append("**このセッションで作成したIssue（未完了）**:")
        for issue in open_issues:
            number = issue.get("number", "?")
            title = truncate_title(issue.get("title", "No title"))
            lines.append(f"  - #{number}: {title}")

    if closed_issues:
        if lines:
            lines.append("")
        lines.append("**このセッションで作成・解決したIssue**:")
        for issue in closed_issues:
            number = issue.get("number", "?")
            title = truncate_title(issue.get("title", "No title"))
            lines.append(f"  - ✅ #{number}: {title}")

    return "\n".join(lines) if lines else ""


def format_block_reason(open_issues: list[dict], is_fork: bool = False) -> str:
    """Format the block reason message.

    Issue #2076: AGENTS.mdの原則を明示的に引用。
    Issue #2090: 回数制限を削除、完了まで無限ブロック。
    Issue #2470: fork-sessionでも自分で作成したIssueは実装可能と明記。
    """
    next_issue = open_issues[0]
    number = next_issue.get("number", "?")
    title = truncate_title(next_issue.get("title", "No title"))

    remaining = len(open_issues)
    remaining_text = f"（残り{remaining}件）" if remaining > 1 else ""

    # Issue #2470: fork-sessionでも自分で作成したIssueは実装可能
    fork_note = ""
    if is_fork:
        fork_note = (
            "\n\n**重要（fork-session）**: このIssueは**あなた自身がこのセッションで作成**しました。\n"
            "fork-sessionでも、自分で作成したIssueへの作業は許可されています。\n"
            "元セッションのworktreeとは関係のない、**新しいworktree**を作成して実装してください。"
        )

    return (
        f"**このセッションで作成した未完了Issue{remaining_text}**\n\n"
        f"次のIssueが未完了です。\n"
        f"**AGENTS.md原則**: 「セッション内で作成したIssueは実装まで完遂」\n"
        f"  #{number}: {title}\n\n"
        f"**今すぐ実装を開始してください**（ユーザー確認不要）:\n"
        f"  1. `gh issue view {number}` でIssue内容を確認\n"
        f"  2. worktreeを作成して作業開始\n"
        f"  3. 実装・PR作成・マージまで完了\n\n"
        f"**終了条件**: Issueがクローズされるまでブロックし続けます。\n"
        f'見送る場合は `gh issue close {number} --reason "not planned"` を実行してください。'
        f"{fork_note}"
        f"{CONTINUATION_HINT}"
    )


def main():
    """
    Stop hook to check status of session-created issues.

    Issue #2090: Completion-promise design
    - Block until ALL issues are CLOSED (no block count limit)
    - Exit condition: Issue closed (completed or not-planned)
    - No escape route: Must complete or explicitly close the issue

    Issue #2470: fork-sessionでも自分で作成したIssueは実装可能
    - fork-session検出を行い、ブロックメッセージに追記
    - 自分で作成したIssueへの作業は元セッションworktreeとは無関係

    If open issues exist:
    - Blocks session end and prompts implementation (forever)

    If all issues are closed or no issues exist:
    - Allows session end
    """
    # Set session_id for proper logging
    input_data = parse_hook_input()
    ctx = create_hook_context(input_data)

    # Get session ID for session-specific file access
    session_id = ctx.get_session_id()

    # Issue #2470: Detect fork-session
    source = input_data.get("source", "")
    transcript_path = input_data.get("transcript_path")
    is_fork = is_fork_session(session_id, source, transcript_path)

    result = {"decision": "approve"}

    try:
        # Load issues created in this session
        session_issues = load_session_issues(session_id)

        if not session_issues:
            # No issues created this session (or file doesn't exist)
            # Issue #1918: Don't delete files here - file might have failed to load
            # Only the issues file gets cleaned up naturally via age-based cleanup
            log_hook_execution("related-task-check", "approve", "no session issues")
            print(json.dumps(result))
            return

        # Get current status of these issues
        issues = get_issue_status(session_issues)

        if not issues:
            # Issue #1918: Don't delete files here - API call might have failed temporarily
            # Keep the issues file so we can retry next time
            log_hook_execution("related-task-check", "approve", "could not fetch issue status")
            print(json.dumps(result))
            return

        # Separate open and closed issues
        open_issues = [i for i in issues if i.get("state") == "OPEN"]
        closed_issues = [i for i in issues if i.get("state") == "CLOSED"]

        # Issue #2525: Check for issues delegated to fork-session
        delegated_issues: list[dict] = []
        actionable_issues: list[dict] = []

        for issue in open_issues:
            issue_number = issue.get("number")
            if issue_number:
                is_delegated, pr_number = is_issue_delegated(issue_number)
                if is_delegated:
                    issue["delegated_pr"] = pr_number
                    delegated_issues.append(issue)
                else:
                    actionable_issues.append(issue)
            else:
                actionable_issues.append(issue)

        # If all open issues are delegated, approve with info message
        if not actionable_issues:
            info_lines = []
            if delegated_issues:
                info_lines.append("**fork-sessionに委譲済みのIssue**:")
                for issue in delegated_issues:
                    number = issue.get("number", "?")
                    title = truncate_title(issue.get("title", "No title"))
                    pr_number = issue.get("delegated_pr")
                    if pr_number:
                        info_lines.append(f"  - #{number}: {title} → PR #{pr_number} が対応中")
                    else:
                        info_lines.append(f"  - #{number}: {title} → worktreeがロック中（対応中）")
                info_lines.append("")
                info_lines.append("介入せず、fork-sessionの完了を待ちます。")

            if closed_issues:
                if info_lines:
                    info_lines.append("")
                info_lines.append("**このセッションで作成・解決したIssue**:")
                for issue in closed_issues:
                    number = issue.get("number", "?")
                    title = truncate_title(issue.get("title", "No title"))
                    info_lines.append(f"  - ✅ #{number}: {title}")

            if info_lines:
                result["systemMessage"] = "\n".join(info_lines)

            # Issue #2525: Clear files only if all issues are actually closed (not just delegated)
            # If delegated, files shouldn't be cleared as issues are still open
            if not delegated_issues:
                clear_session_files(session_id)

            log_reason = (
                f"actionable: 0, delegated: {len(delegated_issues)}, closed: {len(closed_issues)}"
            )
            log_hook_execution("related-task-check", "approve", log_reason)
            print(json.dumps(result))
            return

        # There are actionable open issues - block until closed (no count limit)
        result = {
            "decision": "block",
            "reason": format_block_reason(actionable_issues, is_fork=is_fork),
        }
        log_hook_execution(
            "related-task-check",
            "block",
            f"open={len(actionable_issues)}, delegated={len(delegated_issues)}, is_fork={is_fork}",
        )
        print(json.dumps(result))
        return

    except Exception as e:
        print(f"[related-task-check] Error: {e}", file=sys.stderr)
        log_hook_execution("related-task-check", "approve", f"error: {e}")
        # Issue #1918: Don't clear session files on error - error might be temporary
        # Files will be cleaned up by age-based cleanup mechanism

    print(json.dumps(result))


if __name__ == "__main__":
    main()
