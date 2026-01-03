#!/usr/bin/env python3
"""セッション終了時に成果物（PR、Issue、コミット）を収集。

Why:
    セッションの成果を定量的に記録することで、フック有効性の測定や
    事後分析が可能になる。リアルタイム追跡ではなく結果ベースで評価する。

What:
    - セッション終了時（Stop）に発火
    - セッション開始時刻以降のPR作成・マージを収集
    - Issue作成、コミット数を収集
    - 成果物からタスクタイプを推定
    - outcomes/にセッション別で保存

State:
    - reads: .claude/logs/flow/state-*.json（セッション開始時刻）
    - writes: .claude/logs/outcomes/session-outcomes-*.jsonl

Remarks:
    - 非ブロック型（Stopフック）
    - GitHub API経由でPR/Issueを取得
    - flow_definitions.pyのestimate_task_type()でタスクタイプ推定

Changelog:
    - silenvx/dekita#1158: フック追加（成果物ベース評価）
    - silenvx/dekita#1840: セッション別ファイル出力
    - silenvx/dekita#2545: HookContextパターン移行
"""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common import get_session_start_time
from flow_definitions import estimate_task_type
from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.logging import log_to_session_file
from lib.session import create_hook_context, parse_hook_input

# Outcome log directory
OUTCOME_LOG_DIR = Path(__file__).parent.parent / "logs" / "outcomes"


def collect_prs_created(since: datetime) -> list[dict[str, Any]]:
    """Collect PRs created by current user since the given time.

    Args:
        since: Datetime to filter PRs created after

    Returns:
        List of PR info dicts with number, title, state, url
    """
    # Convert to UTC and format for GitHub API comparison
    since_utc = since.astimezone(UTC) if since.tzinfo else since
    since_str = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--author",
                "@me",
                "--json",
                "number,title,state,url,createdAt",
                "--limit",
                "50",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        prs = json.loads(result.stdout) if result.stdout else []
        # Filter by creation time
        filtered = []
        for pr in prs:
            created_at = pr.get("createdAt", "")
            if created_at >= since_str:
                filtered.append(
                    {
                        "number": pr.get("number"),
                        "title": pr.get("title"),
                        "state": pr.get("state"),
                        "url": pr.get("url"),
                    }
                )
        return filtered
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def collect_prs_merged(since: datetime) -> list[dict[str, Any]]:
    """Collect PRs merged by current user since the given time.

    Args:
        since: Datetime to filter PRs merged after

    Returns:
        List of merged PR info dicts with number, title, url
    """
    # Convert to UTC and format for GitHub API comparison
    since_utc = since.astimezone(UTC) if since.tzinfo else since
    since_str = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--author",
                "@me",
                "--state",
                "merged",
                "--json",
                "number,title,url,mergedAt",
                "--limit",
                "50",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        prs = json.loads(result.stdout) if result.stdout else []
        # Filter by merge time
        filtered = []
        for pr in prs:
            merged_at = pr.get("mergedAt", "")
            if merged_at and merged_at >= since_str:
                filtered.append(
                    {
                        "number": pr.get("number"),
                        "title": pr.get("title"),
                        "url": pr.get("url"),
                    }
                )
        return filtered
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def collect_issues_created(since: datetime) -> list[dict[str, Any]]:
    """Collect Issues created by current user since the given time.

    Args:
        since: Datetime to filter Issues created after

    Returns:
        List of Issue info dicts with number, title, url
    """
    # Convert to UTC and format for GitHub API comparison
    since_utc = since.astimezone(UTC) if since.tzinfo else since
    since_str = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--author",
                "@me",
                "--json",
                "number,title,url,createdAt",
                "--limit",
                "50",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        issues = json.loads(result.stdout) if result.stdout else []
        # Filter by creation time
        filtered = []
        for issue in issues:
            created_at = issue.get("createdAt", "")
            if created_at >= since_str:
                filtered.append(
                    {
                        "number": issue.get("number"),
                        "title": issue.get("title"),
                        "url": issue.get("url"),
                    }
                )
        return filtered
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def get_git_user_email() -> str:
    """Get current git user email.

    Returns:
        User email from git config, or empty string if not configured
    """
    try:
        result = subprocess.run(
            ["git", "config", "user.email"], capture_output=True, text=True, timeout=TIMEOUT_LIGHT
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        # git設定が取得できなくても致命的ではないため、空文字でフォールバック
        pass
    return ""


def collect_commits_count(since: datetime) -> int:
    """Count commits made by current user since the given time.

    Args:
        since: Datetime to filter commits after

    Returns:
        Number of commits made by current user
    """
    # Use isoformat() to preserve timezone info for accurate filtering
    since_str = since.isoformat()
    try:
        # Get user email for author filtering
        user_email = get_git_user_email()
        cmd = ["git", "log", f"--since={since_str}", "--oneline"]
        if user_email:
            cmd.append(f"--author={user_email}")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_MEDIUM)
        if result.returncode != 0:
            return 0

        lines = [line for line in result.stdout.strip().split("\n") if line]
        return len(lines)
    except (subprocess.TimeoutExpired, OSError):
        return 0


def get_pr_commits_since(pr_number: int, since: datetime) -> list[str]:
    """Get commits for a PR that were committed after the given time.

    Args:
        pr_number: PR number to check
        since: Datetime to filter commits after

    Returns:
        List of commit SHAs committed after since time
    """
    # Convert to UTC for GitHub API comparison
    since_utc = since.astimezone(UTC) if since.tzinfo else since
    since_str = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "commits",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        data = json.loads(result.stdout) if result.stdout else {}
        commits = data.get("commits", [])

        # Filter commits by committedDate (not authoredDate) to catch rebased commits
        recent_commits = []
        for commit in commits:
            committed_date = commit.get("committedDate", "")
            if committed_date >= since_str:
                oid = commit.get("oid")
                if oid:
                    recent_commits.append(oid)

        return recent_commits
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def collect_prs_pushed(since: datetime, prs_created: list[dict]) -> list[int]:
    """Identify PRs that received pushes but were not created in this session.

    Uses GitHub API to check PR commits, avoiding hardcoded remote names.

    Args:
        since: Session start time
        prs_created: List of PRs created during session (to exclude)

    Returns:
        List of PR numbers that received pushes
    """
    created_numbers = {pr.get("number") for pr in prs_created}

    try:
        # Get all open PRs by current user
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--author",
                "@me",
                "--state",
                "open",
                "--json",
                "number",
                "--limit",
                "50",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        prs = json.loads(result.stdout) if result.stdout else []
        pushed_prs = []

        for pr in prs:
            pr_number = pr.get("number")
            if pr_number in created_numbers:
                continue  # Skip PRs created in this session

            # Use GitHub API to check for commits since session start
            recent_commits = get_pr_commits_since(pr_number, since)
            if recent_commits:
                pushed_prs.append(pr_number)

        return pushed_prs
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def save_outcome(session_id: str, outcome: dict[str, Any]) -> bool:
    """Save session outcome to JSONL log file.

    Issue #1840: Now writes to session-specific file.

    Args:
        session_id: The session ID
        outcome: Outcome data to save

    Returns:
        True if saved successfully, False otherwise
    """
    # Issue #1840: Write to session-specific file
    return log_to_session_file(OUTCOME_LOG_DIR, "session-outcomes", session_id, outcome)


def format_outcome_summary(outcome: dict[str, Any]) -> str:
    """Format outcome for display in session end message.

    Args:
        outcome: Session outcome dict

    Returns:
        Formatted summary string
    """
    task_type = outcome.get("task_type", "unknown")
    prs_merged = outcome.get("prs_merged", [])
    prs_created = outcome.get("prs_created", [])
    issues_created = outcome.get("issues_created", [])
    commits_count = outcome.get("commits_count", 0)

    lines = ["\n[session-outcome] セッション成果物:"]
    lines.append(f"  タスクタイプ: {task_type}")

    if prs_merged:
        pr_list = ", ".join(f"#{pr['number']}" for pr in prs_merged)
        lines.append(f"  マージ済みPR: {pr_list}")

    if prs_created:
        pr_list = ", ".join(f"#{pr['number']}" for pr in prs_created)
        lines.append(f"  作成したPR: {pr_list}")

    if issues_created:
        issue_list = ", ".join(f"#{issue['number']}" for issue in issues_created)
        lines.append(f"  作成したIssue: {issue_list}")

    lines.append(f"  コミット数: {commits_count}")

    return "\n".join(lines)


def main() -> None:
    """Main entry point for the hook."""
    hook_input = parse_hook_input()

    ctx = create_hook_context(hook_input)

    # Prevent infinite loops in Stop hooks
    if hook_input.get("stop_hook_active"):
        print(json.dumps({"decision": "approve"}))
        return

    session_id = ctx.get_session_id()
    session_start = get_session_start_time(session_id)

    if not session_start:
        # No session start time available, skip collection
        print(json.dumps({"decision": "approve"}))
        return

    # Collect session outcomes
    prs_created = collect_prs_created(session_start)
    prs_merged = collect_prs_merged(session_start)
    issues_created = collect_issues_created(session_start)
    commits_count = collect_commits_count(session_start)
    prs_pushed = collect_prs_pushed(session_start, prs_created)

    # Build outcomes dict for task type estimation
    outcomes_for_estimation = {
        "prs_merged": [pr["number"] for pr in prs_merged],
        "prs_created": [pr["number"] for pr in prs_created],
        "prs_pushed": prs_pushed,
        "issues_created": [issue["number"] for issue in issues_created],
        "commits_count": commits_count,
    }

    # Estimate task type
    task_type = estimate_task_type(outcomes_for_estimation)

    # Build full outcome record
    outcome: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "session_start": session_start.isoformat(),
        "task_type": task_type.value,
        "prs_created": prs_created,
        "prs_merged": prs_merged,
        "prs_pushed": prs_pushed,
        "issues_created": issues_created,
        "commits_count": commits_count,
    }

    # Save to log
    save_outcome(session_id, outcome)

    # Log outcome collection
    log_hook_execution(
        "session-outcome-collector",
        "approve",
        f"Session outcomes collected: {task_type.value}",
        {
            "task_type": task_type.value,
            "prs_created": len(prs_created),
            "prs_merged": len(prs_merged),
            "issues_created": len(issues_created),
            "commits_count": commits_count,
        },
    )

    # Format summary for display
    summary = format_outcome_summary(outcome)

    result = {"decision": "approve", "systemMessage": summary}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
